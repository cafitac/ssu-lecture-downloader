"""GradesService: consent check, 30-minute memory snapshot, single-flight u-SAINT access.

One instance per backend process. Grade values exist only here (in memory) and in the HTTP response
to the local webview; the only thing written to disk is the consent file (see consent.py). This
class never logs a value, a username, or an exception message: failures leave as
`GradesError(code)` and the log carries `result=<code|ok> elapsed_ms=<n>` only.

Safety model
- `_generation` counts `clear()` calls (consent revoke, logout, changed credentials). Every request
  captures it at entry, *before* reading consent and credentials; whatever it fetches is stored only
  if the generation is unchanged, and a request that queued behind another one re-reads consent and
  credentials once it holds the flight lock and gives up (no gateway call) if the generation moved.
- Concurrent equal requests share one flight, success or failure (one login per burst).
- A u-SAINT login rejection is remembered per (account, generation) until credentials change or
  `clear()`, so "retry" never re-sends a known-rejected password.
- All gateway coroutines run on a dedicated worker thread (worker.py) so a call that blocks cannot
  stall the API event loop.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

from ssu_lecture_downloader.core.grades.consent import CURRENT_CONSENT_VERSION, GradesConsentStore
from ssu_lecture_downloader.core.grades.gateway import UsaintGateway
from ssu_lecture_downloader.core.grades.models import (
    GRADES_CONSENT_REQUIRED,
    GRADES_LOGIN_REJECTED,
    GRADES_UNAVAILABLE,
    MISSING_CREDENTIALS,
    SEMESTER_NAMES,
    ClassList,
    GradesError,
    GradesOverview,
    GradesStatus,
)
from ssu_lecture_downloader.core.grades.worker import GatewayWorker

logger = logging.getLogger("ssu_lecture_downloader.grades")

T = TypeVar("T")

DEFAULT_TTL = timedelta(minutes=30)
OVERVIEW_TIMEOUT_SECONDS = 25.0
CLASSES_TIMEOUT_SECONDS = 20.0

CredentialsProvider = Callable[[], "tuple[str, str] | None"]


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _consume(future: asyncio.Future[Any]) -> None:
    """Mark a flight's exception as retrieved even when nobody else was waiting on it."""
    if not future.cancelled():
        future.exception()


@dataclass
class _Snapshot:
    """What was read for one account; classes live and die with it. `account_key` (the username) is
    compared, never logged or stored anywhere else."""

    account_key: str
    fetched_at: datetime
    overview: GradesOverview | None = None
    classes: dict[tuple[int, str], ClassList] = field(default_factory=dict)


class GradesService:
    def __init__(
        self,
        consent: GradesConsentStore,
        gateway: UsaintGateway,
        credentials_provider: CredentialsProvider,
        *,
        ttl: timedelta = DEFAULT_TTL,
        overview_timeout: float = OVERVIEW_TIMEOUT_SECONDS,
        classes_timeout: float = CLASSES_TIMEOUT_SECONDS,
        now: Callable[[], datetime] = _utc_now,
        worker: GatewayWorker | None = None,
    ) -> None:
        self._consent = consent
        self._gateway = gateway
        self._credentials = credentials_provider
        self._ttl = ttl
        self._overview_timeout = overview_timeout
        self._classes_timeout = classes_timeout
        self._now = now
        self._worker = worker or GatewayWorker()
        self._flight = asyncio.Lock()  # at most one u-SAINT login/fetch in flight (I5)
        self._inflight: dict[tuple[Any, ...], asyncio.Future[Any]] = {}  # event-loop thread only
        self._state = threading.Lock()  # guards the fields below; routes run on the loop and the thread pool
        self._snapshot: _Snapshot | None = None
        self._session: tuple[str, Any] | None = None
        self._rejected: tuple[str, int] | None = None  # (account_key, generation) of a rejected login
        self._generation = 0

    # ---- consent -------------------------------------------------------------------------------

    def status(self) -> GradesStatus:
        fetched_at = None
        pair = self._pair_or_none()
        if pair is not None:
            overview = self._fresh_overview(pair[0])
            fetched_at = overview.fetched_at if overview else None
        return GradesStatus(
            consent=self._consent.read(),
            available=self._gateway.available(),
            current_consent_version=CURRENT_CONSENT_VERSION,
            fetched_at=fetched_at,
        )

    def grant_consent(self, version: int) -> GradesStatus:
        try:
            self._consent.grant(version)  # ValueError for a version other than the current one
        except OSError:
            raise GradesError(GRADES_UNAVAILABLE) from None
        return self.status()

    def revoke_consent(self) -> GradesStatus:
        """Delete the file first, then clear: a fetch that finishes in between is wiped by the clear,
        and one that starts afterwards finds no consent."""
        failed = False
        try:
            self._consent.revoke()
        except OSError:
            failed = True
        finally:
            self.clear()
        if failed:
            raise GradesError(GRADES_UNAVAILABLE) from None
        return self.status()

    def clear(self) -> None:
        """Drop the snapshot, the u-SAINT session and any remembered rejection."""
        with self._state:
            self._snapshot = None
            self._session = None
            self._rejected = None
            self._generation += 1

    def close(self) -> None:
        self._worker.close()

    # ---- reads ---------------------------------------------------------------------------------

    async def overview(self, refresh: bool = False) -> GradesOverview:
        generation = self._current_generation()
        username, _password = self._require_access()
        if not refresh:
            cached = self._fresh_overview(username)
            if cached is not None:
                return cached
        self._raise_if_rejected(username, generation)

        async def fetch(session_user: str, password: str) -> GradesOverview:
            async def operation(session: Any) -> GradesOverview:
                fetched = await self._worker.run(self._gateway.overview(session))
                return _stamp(fetched, _iso(self._now()))

            overview = await self._fetch("grades_fetch", session_user, password, operation, self._overview_timeout, generation)
            with self._state:
                if self._generation != generation:
                    raise GradesError(GRADES_UNAVAILABLE)  # cleared mid-fetch: do not resurrect it
                live = self._live_snapshot(session_user)
                if live is None or live.overview is not None:  # a refresh starts a new snapshot (and drops its classes)
                    live = self._snapshot = _Snapshot(account_key=session_user, fetched_at=self._now())
                snapshot = live
                snapshot.overview = overview
            return overview

        def cached(session_user: str) -> GradesOverview | None:
            return None if refresh else self._fresh_overview(session_user)

        return await self._single_flight(("overview", username, refresh, generation), generation, username, cached, fetch)

    async def classes(self, year: int, semester: str, refresh: bool = False) -> ClassList:
        if semester not in SEMESTER_NAMES:
            raise ValueError("unknown semester")
        generation = self._current_generation()
        username, _password = self._require_access()
        key = (year, semester)
        if not refresh:
            cached_now = self._fresh_classes(username, key)
            if cached_now is not None:
                return cached_now
        self._raise_if_rejected(username, generation)

        async def fetch(session_user: str, password: str) -> ClassList:
            async def operation(session: Any) -> ClassList:
                return await self._worker.run(self._gateway.classes(session, year, semester))

            result = await self._fetch("grades_classes", session_user, password, operation, self._classes_timeout, generation)
            with self._state:
                if self._generation != generation:
                    raise GradesError(GRADES_UNAVAILABLE)
                snapshot = self._live_snapshot(session_user)
                if snapshot is None:
                    snapshot = self._snapshot = _Snapshot(account_key=session_user, fetched_at=self._now())
                snapshot.classes[key] = result
            return result

        def cached(session_user: str) -> ClassList | None:
            return None if refresh else self._fresh_classes(session_user, key)

        return await self._single_flight(("classes", username, year, semester, refresh, generation), generation, username, cached, fetch)

    # ---- internals -----------------------------------------------------------------------------

    def _current_generation(self) -> int:
        with self._state:
            return self._generation

    def _pair_or_none(self) -> tuple[str, str] | None:
        pair = self._credentials()
        return pair if pair else None

    def _require_access(self) -> tuple[str, str]:
        """Consent first (I1: no gateway call without it), then the in-memory LMS credentials."""
        if self._consent.read() is None:
            raise GradesError(GRADES_CONSENT_REQUIRED)
        pair = self._pair_or_none()
        if pair is None:
            raise GradesError(MISSING_CREDENTIALS)
        return pair

    def _raise_if_rejected(self, username: str, generation: int) -> None:
        with self._state:
            if self._rejected == (username, generation):
                raise GradesError(GRADES_LOGIN_REJECTED)

    def _fresh(self, moment: datetime) -> bool:
        return self._now() - moment < self._ttl

    def _live_snapshot(self, username: str) -> _Snapshot | None:
        """The caller holds `_state`. Another account's or an expired snapshot counts as absent (I2)."""
        snapshot = self._snapshot
        if snapshot is None or snapshot.account_key != username or not self._fresh(snapshot.fetched_at):
            return None
        return snapshot

    def _fresh_overview(self, username: str) -> GradesOverview | None:
        with self._state:
            snapshot = self._live_snapshot(username)
            return snapshot.overview if snapshot else None

    def _fresh_classes(self, username: str, key: tuple[int, str]) -> ClassList | None:
        with self._state:
            snapshot = self._live_snapshot(username)
            return snapshot.classes.get(key) if snapshot else None

    def _session_for(self, username: str) -> Any | None:
        with self._state:
            if self._session is not None and self._session[0] == username:
                return self._session[1]
            return None

    def _drop_session(self) -> None:
        with self._state:
            self._session = None

    async def _single_flight(
        self,
        key: tuple[Any, ...],
        generation: int,
        username: str,
        cached: Callable[[str], T | None],
        fetch: Callable[[str, str], Awaitable[T]],
    ) -> T:
        """Equal concurrent requests share one flight and its outcome, success or failure."""
        existing = self._inflight.get(key)
        if existing is not None:
            return await asyncio.shield(existing)
        flight: asyncio.Future[T] = asyncio.get_running_loop().create_future()
        flight.add_done_callback(_consume)
        self._inflight[key] = flight
        try:
            async with self._flight:
                # Consent, credentials and the generation may have moved while this request queued.
                session_user, password = self._require_access()
                if self._current_generation() != generation or session_user != username:
                    raise GradesError(GRADES_UNAVAILABLE)
                self._raise_if_rejected(session_user, generation)
                hit = cached(session_user)
                result = hit if hit is not None else await fetch(session_user, password)
        except asyncio.CancelledError:
            flight.set_exception(GradesError(GRADES_UNAVAILABLE))
            raise
        except Exception as error:
            flight.set_exception(error)
            raise
        else:
            flight.set_result(result)
            return result
        finally:
            if self._inflight.get(key) is flight:
                del self._inflight[key]

    async def _fetch(
        self,
        event: str,
        username: str,
        password: str,
        operation: Callable[[Any], Awaitable[T]],
        timeout: float,
        generation: int,
    ) -> T:
        """Run `operation` on a session. A transient failure gets one retry on a fresh login; a login
        rejection is never retried and is remembered until the credentials change."""
        started = time.monotonic()
        result = "ok"
        try:
            try:
                return await self._attempt(username, password, operation, timeout, generation)
            except GradesError as error:
                if error.code != GRADES_UNAVAILABLE or self._current_generation() != generation:
                    raise
            self._drop_session()
            return await self._attempt(username, password, operation, timeout, generation)
        except GradesError as error:
            result = error.code
            raise
        except asyncio.CancelledError:
            result = "cancelled"
            raise
        finally:
            logger.info("%s result=%s elapsed_ms=%d", event, result, (time.monotonic() - started) * 1000)

    async def _attempt(
        self,
        username: str,
        password: str,
        operation: Callable[[Any], Awaitable[T]],
        timeout: float,
        generation: int,
    ) -> T:
        async def run() -> T:
            session = self._session_for(username)
            if session is None:
                session = await self._worker.run(self._gateway.login(username, password))
                with self._state:
                    if self._generation == generation:
                        self._session = (username, session)
            return await operation(session)

        try:
            return await asyncio.wait_for(run(), timeout)
        except GradesError as error:
            self._drop_session()  # any failure: the next attempt logs in fresh
            if error.code == GRADES_LOGIN_REJECTED:
                with self._state:
                    if self._generation == generation:
                        self._rejected = (username, generation)
            raise
        except asyncio.CancelledError:
            self._drop_session()
            raise
        except Exception:  # noqa: BLE001 - timeout or an unexpected gateway error; the cause is never kept
            self._drop_session()
            raise GradesError(GRADES_UNAVAILABLE) from None


def _stamp(overview: GradesOverview, fetched_at: str) -> GradesOverview:
    from dataclasses import replace

    return replace(overview, fetched_at=fetched_at)
