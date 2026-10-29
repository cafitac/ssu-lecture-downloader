"""GradesService: consent check, 30-minute memory snapshot, single-flight u-SAINT access.

One instance per backend process. Grade values exist only here (in memory) and in the HTTP response
to the local webview; the only thing written to disk is the consent file (see consent.py). This
class never logs a value, a username, or an exception message: failures leave as
`GradesError(code)` and the log carries `result=<code|ok> elapsed_ms=<n>` only.
"""

from __future__ import annotations

import asyncio
import dataclasses
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
    GRADES_UNAVAILABLE,
    MISSING_CREDENTIALS,
    SEMESTER_NAMES,
    ClassList,
    GradesError,
    GradesOverview,
    GradesStatus,
)

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


@dataclass
class _Snapshot:
    """What was read for one account. `account_key` (the username) is compared, never logged or stored."""

    account_key: str
    overview: GradesOverview | None = None
    overview_at: datetime | None = None
    classes: dict[tuple[int, str], tuple[datetime, ClassList]] = field(default_factory=dict)


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
    ) -> None:
        self._consent = consent
        self._gateway = gateway
        self._credentials = credentials_provider
        self._ttl = ttl
        self._overview_timeout = overview_timeout
        self._classes_timeout = classes_timeout
        self._now = now
        self._flight = asyncio.Lock()  # at most one u-SAINT login/fetch in flight (I5)
        self._state = threading.Lock()  # guards the fields below; routes run on the loop and the thread pool
        self._snapshot: _Snapshot | None = None
        self._session: tuple[str, Any] | None = None
        self._generation = 0

    # ---- consent -------------------------------------------------------------------------------

    def status(self) -> GradesStatus:
        fetched_at = None
        pair = self._pair_or_none()
        if pair is not None:
            cached = self._fresh_overview(pair[0])
            fetched_at = cached[1].fetched_at if cached else None
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
        self.clear()  # memory first: nothing may outlive a revoked consent
        try:
            self._consent.revoke()
        except OSError:
            raise GradesError(GRADES_UNAVAILABLE) from None
        return self.status()

    def clear(self) -> None:
        """Drop the snapshot and the u-SAINT session (consent revoke, logout, changed credentials)."""
        with self._state:
            self._snapshot = None
            self._session = None
            self._generation += 1

    # ---- reads ---------------------------------------------------------------------------------

    async def overview(self, refresh: bool = False) -> GradesOverview:
        username, password = self._require_access()
        started = self._now()
        if not refresh:
            cached = self._fresh_overview(username)
            if cached is not None:
                return cached[1]
        async with self._flight:
            self._require_access()  # consent may have been revoked while waiting
            cached = self._fresh_overview(username)
            if cached is not None and (not refresh or cached[0] >= started):
                return cached[1]  # a waiter re-checks: one login serves every concurrent request
            generation = self._generation

            async def operation(session: Any) -> GradesOverview:
                fetched = await self._gateway.overview(session)
                return dataclasses.replace(fetched, fetched_at=_iso(self._now()))

            overview = await self._fetch("grades_fetch", username, password, operation, self._overview_timeout, generation)
            with self._state:
                if self._generation != generation:
                    raise GradesError(GRADES_UNAVAILABLE)  # cleared mid-fetch: do not resurrect it
                snapshot = self._snapshot_for(username)
                snapshot.overview, snapshot.overview_at = overview, self._now()
            return overview

    async def classes(self, year: int, semester: str, refresh: bool = False) -> ClassList:
        if semester not in SEMESTER_NAMES:
            raise ValueError("unknown semester")
        username, password = self._require_access()
        started = self._now()
        key = (year, semester)
        if not refresh:
            cached = self._fresh_classes(username, key)
            if cached is not None:
                return cached[1]
        async with self._flight:
            self._require_access()
            cached = self._fresh_classes(username, key)
            if cached is not None and (not refresh or cached[0] >= started):
                return cached[1]
            generation = self._generation

            async def operation(session: Any) -> ClassList:
                return await self._gateway.classes(session, year, semester)

            result = await self._fetch("grades_classes", username, password, operation, self._classes_timeout, generation)
            with self._state:
                if self._generation != generation:
                    raise GradesError(GRADES_UNAVAILABLE)
                self._snapshot_for(username).classes[key] = (self._now(), result)
            return result

    # ---- internals -----------------------------------------------------------------------------

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

    def _snapshot_for(self, username: str) -> _Snapshot:
        """The caller holds `_state`. Another account's snapshot is replaced, never reused (I2)."""
        if self._snapshot is None or self._snapshot.account_key != username:
            self._snapshot = _Snapshot(account_key=username)
        return self._snapshot

    def _fresh(self, moment: datetime) -> bool:
        return self._now() - moment < self._ttl

    def _fresh_overview(self, username: str) -> tuple[datetime, GradesOverview] | None:
        with self._state:
            snapshot = self._snapshot
            if snapshot is None or snapshot.account_key != username or snapshot.overview is None:
                return None
            if snapshot.overview_at is None or not self._fresh(snapshot.overview_at):
                return None
            return snapshot.overview_at, snapshot.overview

    def _fresh_classes(self, username: str, key: tuple[int, str]) -> tuple[datetime, ClassList] | None:
        with self._state:
            snapshot = self._snapshot
            if snapshot is None or snapshot.account_key != username:
                return None
            entry = snapshot.classes.get(key)
            return entry if entry is not None and self._fresh(entry[0]) else None

    def _session_for(self, username: str) -> Any | None:
        with self._state:
            if self._session is not None and self._session[0] == username:
                return self._session[1]
            return None

    def _drop_session(self) -> None:
        with self._state:
            self._session = None

    async def _fetch(
        self,
        event: str,
        username: str,
        password: str,
        operation: Callable[[Any], Awaitable[T]],
        timeout: float,
        generation: int,
    ) -> T:
        """Run `operation` on a session. A failure on a reused session gets one retry on a fresh login."""
        started = time.monotonic()
        result = "ok"
        try:
            reused = self._session_for(username) is not None
            try:
                return await self._attempt(username, password, operation, timeout, generation)
            except GradesError as error:
                if error.code != GRADES_UNAVAILABLE or not reused:
                    raise
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
                session = await self._gateway.login(username, password)
                with self._state:
                    if self._generation == generation:
                        self._session = (username, session)
            return await operation(session)

        try:
            return await asyncio.wait_for(run(), timeout)
        except GradesError:
            self._drop_session()  # any failure: the next attempt logs in fresh
            raise
        except asyncio.CancelledError:
            self._drop_session()
            raise
        except Exception:  # noqa: BLE001 - timeout or an unexpected gateway error; the cause is never kept
            self._drop_session()
            raise GradesError(GRADES_UNAVAILABLE) from None

