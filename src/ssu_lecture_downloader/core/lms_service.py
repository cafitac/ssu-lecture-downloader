from __future__ import annotations

import dataclasses
import logging
import threading
from datetime import datetime, timezone
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, TypeVar
from urllib.parse import quote, urljoin

from ssu_lecture_downloader.core.cache import TtlCache
from ssu_lecture_downloader.core.canvas_files import (
    CANVAS_ORIGIN,
    MAX_PAGE_FILES,
    extract_file_links,
    parse_canvas_json,
)
from ssu_lecture_downloader.core.download import (
    DownloadCancelled,
    DownloadPlan,
    download_to_plan,
    plan_download_path,
)
from ssu_lecture_downloader.core.errors import is_session_lost, is_signed_url_failure, response_status
from ssu_lecture_downloader.core.http_client import HttpClient
from ssu_lecture_downloader.core.lms import (
    DEFAULT_TERM_ID_CANDIDATES,
    LearningXLister,
    _optional_int,
    latest_current_courses,
    latest_current_term,
    learningx_api_headers,
)
from ssu_lecture_downloader.core.login_flow import LoginDiscoveryResult
from ssu_lecture_downloader.core.media import (
    AttendanceItemMedia,
    extract_lti_launch_form,
    parse_attendance_item_media,
    parse_commons_download,
)
from ssu_lecture_downloader.core.models import (
    CourseSummary,
    DeadlineItem,
    build_deadline_items,
    is_task,
    sort_deadline_rows,
)
from ssu_lecture_downloader.core.progress import emit_progress
from ssu_lecture_downloader.core.redaction import redact_url

MEDIA_DOWNLOAD_MAX_ATTEMPTS = 8
# A login older than this is renewed even without an auth failure, so long-lived
# backends do not keep using a silently expired LMS session.
SESSION_TTL_SECONDS = 30 * 60
# Parallelism of the term-id discovery scan and of per-course module fetches
# (separate pooled HTTP clients, bounded).
TERM_SCAN_WORKERS = 4
# Parallelism of per-lecture media resolution in batch plans.
RESOLVE_WORKERS = 4
# Course module lists are reused while resolving several items of one course.
MODULES_CACHE_TTL_SECONDS = 300
# A batch reuses module lists fetched this recently (plan -> download, dry run -> real run)
# instead of asking the LMS again; older ones are refreshed so completion state stays current.
BATCH_MODULES_REUSE_SECONDS = 60
# A resolved media item (including its signed URL) is reused by the follow-up download.
# An expired URL is still recovered by `_download_resolved` re-resolving once.
RESOLVED_MEDIA_TTL_SECONDS = 120
# Warm worker clients kept between parallel phases.
MAX_IDLE_WORKER_CLIENTS = 8

T = TypeVar("T")


LoginFunc = Callable[[str, str, object], LoginDiscoveryResult]


def _default_login_func(username: str, password: str, http: object) -> LoginDiscoveryResult:
    # Imported lazily: the login flow pulls in bs4 and cryptography, which the API
    # does not need until the first login.
    from ssu_lecture_downloader.core.login_flow import run_http_login_discovery

    return run_http_login_discovery(username, password, http)


@dataclass(frozen=True)
class CourseDiscoveryResult:
    ok: bool
    error_code: str | None
    login: LoginDiscoveryResult
    courses: list[CourseSummary]

    @property
    def course_count(self) -> int:
        return len(self.courses)


@dataclass(frozen=True)
class DeadlinesResult:
    ok: bool
    error_code: str | None
    deadlines: list[DeadlineItem]


@dataclass(frozen=True)
class ResolvedMediaResult:
    ok: bool
    error_code: str | None
    course_id: int | str
    module_item_id: int | str
    attendance_item_id: str | None
    title: str | None
    content_id: str | None
    content_type: str | None
    duration: int | float | None
    download_url: str | None
    download_size: int | None
    head_status: int | None = None
    head_location: str | None = None
    head_content_type: str | None = None
    head_content_length: str | None = None


@dataclass(frozen=True)
class CourseDetailResult:
    ok: bool
    error_code: str | None
    course: CourseSummary | None
    lectures: list
    content_items: list | None = None


@dataclass(frozen=True)
class DownloadLectureResult:
    ok: bool
    error_code: str | None
    dry_run: bool
    written: bool
    course_id: int | str
    module_item_id: int | str
    attendance_item_id: str | None
    title: str | None
    content_id: str | None
    download_url: str | None
    download_size: int | None
    output_path: str | None
    file_size: int | None
    file_exists: bool = False
    skipped: bool = False
    conflict_strategy: str = 'rename'


@dataclass(frozen=True)
class NativeDownloadTransferPlan:
    download_url: str
    referer: str | None
    output_path: str
    total_bytes: int | None


@dataclass(frozen=True)
class NativeDownloadPlanResult:
    result: DownloadLectureResult
    native_download: NativeDownloadTransferPlan | None


@dataclass(frozen=True)
class OfficialViewerPlanResult:
    ok: bool
    error_code: str | None
    course_id: int | str
    module_item_id: int | str
    attendance_item_id: str | None
    title: str | None
    content_id: str | None
    content_type: str | None
    duration: int | float | None
    viewer_url: str | None


@dataclass(frozen=True)
class OfficialViewerBridgeCookie:
    name: str
    value: str
    domain: str
    path: str
    secure: bool
    http_only: bool
    same_site: str | None = None


@dataclass(frozen=True)
class OfficialViewerSessionBridgeResult:
    ok: bool
    error_code: str | None
    course_id: int | str
    module_item_id: int | str
    attendance_item_id: str | None
    title: str | None
    content_id: str | None
    content_type: str | None
    duration: int | float | None
    viewer_url: str | None
    launch_url: str | None
    cookies: list[OfficialViewerBridgeCookie]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class LmsSessionCookiesResult:
    """The LMS cookies for an in-app page window (values are for the local desktop shell only)."""

    ok: bool
    error_code: str | None
    cookies: list[OfficialViewerBridgeCookie]
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class PageFile:
    file_id: str
    display_name: str
    size: int | None
    # Signed Canvas download address (from /api/v1/files/:id); None when Canvas refuses the file.
    download_url: str | None
    content_type: str | None = None


@dataclass(frozen=True)
class PageFilesResult:
    ok: bool
    error_code: str | None
    title: str | None
    files: list[PageFile]


@dataclass(frozen=True)
class CanvasFileDownloadResult:
    ok: bool
    error_code: str | None
    file_id: str
    display_name: str | None
    output_path: str | None
    file_size: int | None
    skipped: bool = False
    written: bool = False
    conflict_strategy: str = 'skip'


@dataclass(frozen=True)
class BatchDownloadItemResult:
    ok: bool
    error_code: str | None
    dry_run: bool
    written: bool
    course_id: int | str
    module_item_id: int | str
    title: str | None
    completed: bool | None
    attendance_item_id: str | None
    content_id: str | None
    download_url: str | None
    download_size: int | None
    output_path: str | None
    file_size: int | None
    file_exists: bool = False
    skipped: bool = False
    conflict_strategy: str = 'rename'


@dataclass(frozen=True)
class BatchDownloadResult:
    ok: bool
    error_code: str | None
    dry_run: bool
    course_id: int | str
    course_name: str
    items: list[BatchDownloadItemResult]

    @property
    def total_count(self) -> int:
        return len(self.items)

    @property
    def success_count(self) -> int:
        return sum(1 for item in self.items if item.ok)

    @property
    def failure_count(self) -> int:
        return sum(1 for item in self.items if not item.ok)


@dataclass(frozen=True)
class _ResolvedMediaInternal:
    result: ResolvedMediaResult
    raw_download_url: str
    referer: str | None
    extension: str


@dataclass(frozen=True)
class _ResolvedViewerInternal:
    result: OfficialViewerPlanResult
    raw_viewer_url: str | None


def _bridge_cookies(cookies: object) -> list[OfficialViewerBridgeCookie]:
    raw_cookies = getattr(cookies, "jar", None)
    if raw_cookies is None:
        items = getattr(cookies, "items", list)()
        return [
            OfficialViewerBridgeCookie(
                name=str(name),
                value=str(value),
                domain="canvas.ssu.ac.kr",
                path="/",
                secure=True,
                http_only=True,
            )
            for name, value in items
            if str(name)
        ]

    bridge: list[OfficialViewerBridgeCookie] = []
    for cookie in raw_cookies:
        name = str(getattr(cookie, "name", ""))
        value = str(getattr(cookie, "value", ""))
        domain = str(getattr(cookie, "domain", "") or "canvas.ssu.ac.kr")
        if not name or not value:
            continue
        rest = getattr(cookie, "_rest", {}) or {}
        same_site = rest.get("SameSite") or rest.get("samesite")
        bridge.append(
            OfficialViewerBridgeCookie(
                name=name,
                value=value,
                domain=domain.lstrip("."),
                path=str(getattr(cookie, "path", "") or "/"),
                secure=bool(getattr(cookie, "secure", False)) or domain.endswith("ssu.ac.kr"),
                http_only="HttpOnly" in rest,
                same_site=str(same_site) if same_site else None,
            )
        )
    return bridge



def _term_label(term_id: int, courses: list[dict[str, Any]]) -> str:
    """The LMS's own name for a term when the course payloads carry one, else `term <id>`."""
    for course in courses:
        term = course.get("term")
        for candidate in (course.get("term_name"), term.get("name") if isinstance(term, dict) else None):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
    return f"term {term_id}"


def _credential_fingerprint(username: str, password: str) -> str:
    return sha256(f"{username}\0{password}".encode()).hexdigest()


@dataclass(frozen=True)
class _AttendanceLaunch:
    """Result of the LTI launch -> attendance item lookup shared by viewer and media resolution."""

    action: str
    attendance_item_id: str
    api_headers: dict[str, str]
    media: AttendanceItemMedia


@dataclass
class _PooledClient:
    client: Any
    epoch: int = -1  # cookie epoch the client's jar was last synced to


# Marks a batch item that was never started because the job was cancelled first.
_NOT_STARTED = object()


def _failure(cls: type[T], error_code: str | None, **fixed: Any) -> T:
    """A failed result dataclass: every field without a default is None unless given in `fixed`."""
    values: dict[str, Any] = {
        field.name: None
        for field in dataclasses.fields(cls)  # type: ignore[arg-type]
        if field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING
    }
    values.update(ok=False, error_code=error_code, **fixed)
    return cls(**values)


def _planned_file(resolved: _ResolvedMediaInternal, *, output_dir: Path, course_name: str, fallback_title: str, conflict_strategy: str) -> tuple[DownloadPlan, int | None]:
    """Where a resolved item would be written, and the size of what is already there."""
    result = resolved.result
    plan = plan_download_path(
        output_dir=output_dir,
        course_name=course_name,
        lecture_title=result.title or fallback_title,
        content_id=result.content_id or "content",
        extension=resolved.extension or "mp4",
        conflict_strategy=conflict_strategy,
    )
    file_size = plan.file_path.stat().st_size if plan.file_exists and plan.file_path.exists() else None
    return plan, file_size


def _download_result(
    result: ResolvedMediaResult,
    plan: DownloadPlan,
    *,
    dry_run: bool,
    written: bool,
    skipped: bool,
    file_size: int | None,
    conflict_strategy: str,
) -> DownloadLectureResult:
    return DownloadLectureResult(
        ok=True,
        error_code=None,
        dry_run=dry_run,
        written=written,
        course_id=result.course_id,
        module_item_id=result.module_item_id,
        attendance_item_id=result.attendance_item_id,
        title=result.title,
        content_id=result.content_id,
        download_url=result.download_url,
        download_size=result.download_size,
        output_path=str(plan.file_path),
        file_size=file_size,
        file_exists=plan.file_exists,
        skipped=skipped,
        conflict_strategy=conflict_strategy,
    )


def _batch_item_result(
    *,
    course_id: int | str,
    module_item_id: int | str,
    lecture: Any,
    dry_run: bool,
    resolved: _ResolvedMediaInternal | None = None,
    plan: Any = None,
    title: str | None = None,
    file_size: int | None = None,
    written: bool = False,
    skipped: bool = False,
    conflict_strategy: str | None = None,
    ok: bool = True,
    error_code: str | None = None,
) -> BatchDownloadItemResult:
    """Single construction point for every BatchDownloadItemResult variant.

    Failure variants pass no `conflict_strategy`, which keeps the dataclass default.
    """
    media = resolved.result if resolved is not None else None
    extra: dict[str, Any] = {} if conflict_strategy is None else {"conflict_strategy": conflict_strategy}
    return BatchDownloadItemResult(
        ok=ok,
        error_code=error_code,
        dry_run=dry_run,
        written=written,
        course_id=course_id,
        module_item_id=module_item_id,
        title=title if title is not None else lecture.title,
        completed=lecture.completed,
        attendance_item_id=media.attendance_item_id if media else None,
        content_id=media.content_id if media else None,
        download_url=media.download_url if media else None,
        download_size=media.download_size if media else None,
        output_path=str(plan.file_path) if plan is not None else None,
        file_size=file_size,
        file_exists=plan.file_exists if (plan is not None and ok) else False,
        skipped=skipped,
        **extra,
    )


class SsuLectureService:
    """LMS facade.

    Thread-safety model: `self.http` is one shared curl session whose cookie jar is
    mutated by login, so every use of it happens while holding `self._lock` (an
    RLock). Long byte transfers never hold the lock; they run on their own
    `HttpClient` created by `_download_http_client()`. Parallel read-only phases (term
    scan, per-course module fetches, batch media resolution) are driven by a thread
    holding the lock and fan out on pooled worker clients that carry a copy of the
    session cookies, so nothing else can log in or clear cookies underneath them.

    Caching: every LMS lookup has an explicit TTL (`cache_ttl_seconds`, modules and
    resolved media shorter). Public lookups are single-flight (concurrent identical
    requests share one upstream call) and all caches are dropped by `clear_cache()`
    and whenever a different account logs in.
    """

    def __init__(
        self,
        *,
        http=None,
        login_func: LoginFunc | None = None,
        current_term_candidates: list[int] | None = None,
        cache_ttl_seconds: float = 900,
        session_ttl_seconds: float = SESSION_TTL_SECONDS,
        term_scan_workers: int = TERM_SCAN_WORKERS,
        resolve_workers: int = RESOLVE_WORKERS,
        resolved_media_ttl_seconds: float = RESOLVED_MEDIA_TTL_SECONDS,
        worker_client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.http = http or HttpClient()
        self.login_func = login_func or _default_login_func
        self.current_term_candidates = current_term_candidates or DEFAULT_TERM_ID_CANDIDATES
        self.session_ttl_seconds = session_ttl_seconds
        self.term_scan_workers = max(1, term_scan_workers)
        self.resolve_workers = max(1, resolve_workers)
        if worker_client_factory is None and isinstance(self.http, HttpClient):
            worker_client_factory = self._new_http_client
        # None means "no private clients": workers share `self.http` (test doubles).
        self._worker_client_factory = worker_client_factory
        self._lock = threading.RLock()
        self._session_cache: dict[str, tuple[LoginDiscoveryResult, float]] = {}
        self._active_key: str | None = None
        self._cookie_epoch = 0
        self._pool_lock = threading.Lock()
        self._idle_clients: list[_PooledClient] = []
        self._terms_cache: TtlCache[list[dict[str, Any]]] = TtlCache(cache_ttl_seconds)
        self._term_courses_cache: TtlCache[list[dict[str, Any]]] = TtlCache(cache_ttl_seconds)
        self._courses_cache: TtlCache[CourseDiscoveryResult] = TtlCache(cache_ttl_seconds)
        self._course_detail_cache: TtlCache[CourseDetailResult] = TtlCache(cache_ttl_seconds)
        self._modules_cache: TtlCache[list[dict[str, Any]]] = TtlCache(min(cache_ttl_seconds, MODULES_CACHE_TTL_SECONDS))
        self._resolved_cache: TtlCache[_ResolvedMediaInternal] = TtlCache(min(cache_ttl_seconds, resolved_media_ttl_seconds))

    # ------------------------------------------------------------------ session

    def _reset_derived_state(self, *, keep_inflight: bool = False) -> None:
        """Forget everything learned under the current session (caller holds the lock)."""
        self._session_cache.clear()
        for cache in (self._terms_cache, self._term_courses_cache, self._courses_cache, self._course_detail_cache, self._modules_cache, self._resolved_cache):
            cache.clear(keep_inflight=keep_inflight)

    def clear_cache(self) -> None:
        with self._lock:
            self._reset_derived_state()
            self._active_key = None
            self._clear_http_cookies()
            self._close_idle_clients()

    def _clear_http_cookies(self) -> None:
        cookies = getattr(self.http, "cookies", None)
        clear = getattr(cookies, "clear", None)
        if clear:
            clear()
        self._cookie_epoch += 1

    def _forget_credential_session(self, credential_key: str) -> None:
        with self._lock:
            self._session_cache.pop(credential_key, None)
            self._term_courses_cache.clear()
            self._modules_cache.clear()
            self._resolved_cache.clear()
            self._clear_http_cookies()

    def _fresh_login(self, credential_key: str) -> LoginDiscoveryResult | None:
        cached = self._session_cache.get(credential_key)
        if cached is not None and monotonic() - cached[1] < self.session_ttl_seconds:
            return cached[0]
        return None

    def login(self, username: str, password: str) -> LoginDiscoveryResult:
        credential_key = _credential_fingerprint(username, password)
        # A valid session needs neither the lock nor another login round trip.
        result = self._fresh_login(credential_key)
        if result is not None:
            return result
        with self._lock:
            result = self._fresh_login(credential_key)
            if result is not None:
                return result
            self._session_cache.pop(credential_key, None)
            if self._active_key != credential_key:
                # The cookie jar is about to belong to a different account. Usually called from
                # inside a lookup's loader, whose own result must still be cached afterwards.
                self._reset_derived_state(keep_inflight=True)
            self._clear_http_cookies()
            result = self.login_func(username, password, self.http)
            self._cookie_epoch += 1
            self._active_key = credential_key
            if result.ok:
                self._session_cache[credential_key] = (result, monotonic())
            return result

    def _authenticated(self, username: str, password: str, operation: Callable[[], T]) -> tuple[LoginDiscoveryResult, T | None]:
        """Run `operation` on the shared session with a valid login.

        If the LMS rejects the session (401/403 from an LMS host) the login is
        renewed and the operation is retried exactly once. The value is None when
        no valid login could be established.
        """
        with self._lock:
            login_result = self.login(username, password)
            if not login_result.ok:
                return login_result, None
            try:
                return login_result, operation()
            except Exception as exc:
                if not is_session_lost(exc):
                    raise
                self._forget_credential_session(_credential_fingerprint(username, password))
                login_result = self.login(username, password)
                if not login_result.ok:
                    return login_result, None
                return login_result, operation()

    def _resolve_authenticated(self, username: str, password: str, course_id: int | str, module_item_id: int | str, *, refresh: bool = False):
        """(login result, resolved media or None) for one item, on the shared session."""
        extra = {"refresh": True} if refresh else {}
        return self._authenticated(
            username, password, lambda: self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id, **extra)
        )

    # ------------------------------------------------------------ worker clients

    def _new_http_client(self) -> HttpClient:
        return HttpClient(trace=self.http.trace)

    def _close_idle_clients(self) -> None:
        with self._pool_lock:
            idle, self._idle_clients = self._idle_clients, []
        for entry in idle:
            entry.client.close()

    @contextmanager
    def _worker_client(self) -> Iterator[Any]:
        """An exclusive client for one parallel task, reusing warm connections between phases.

        Its cookie jar mirrors the session's (re-synced whenever the session changed).
        Callers hold `self._lock`, so the session cannot change while the client is in use.
        """
        if self._worker_client_factory is None:
            yield self.http
            return
        with self._pool_lock:
            entry = self._idle_clients.pop() if self._idle_clients else None
        if entry is None:
            entry = _PooledClient(self._worker_client_factory())
        epoch = self._cookie_epoch
        if entry.epoch != epoch:
            entry.client.cookies.clear()
            entry.client.cookies.update(self.http.cookies)
            entry.epoch = epoch
        try:
            yield entry.client
        finally:
            with self._pool_lock:
                keep = len(self._idle_clients) < MAX_IDLE_WORKER_CLIENTS
                if keep:
                    self._idle_clients.append(entry)
            if not keep:
                entry.client.close()

    def _parallel_map(self, fn: Callable[[Any, Any], Any], items: Iterable[Any], *, workers: int) -> list[Any]:
        """`fn(client, item)` for every item on at most `workers` threads, results in order.

        An exception raised for an item is returned in its slot rather than raised, so
        the caller decides whether it is fatal. Must be called with `self._lock` held:
        a single item (or `workers == 1`) runs inline on the shared session.
        """
        items = list(items)
        if len(items) <= 1 or workers <= 1:
            outcomes: list[Any] = []
            for item in items:
                try:
                    outcomes.append(fn(self.http, item))
                except Exception as exc:  # noqa: BLE001
                    outcomes.append(exc)
            return outcomes

        def run(item: Any) -> Any:
            try:
                with self._worker_client() as client:
                    return fn(client, item)
            except Exception as exc:  # noqa: BLE001
                return exc

        with ThreadPoolExecutor(max_workers=min(workers, len(items)), thread_name_prefix="ssu-fetch") as executor:
            return list(executor.map(run, items))

    @contextmanager
    def _download_http_client(self) -> Iterator[Any]:
        """A private client for one long byte transfer (never holds the session lock while streaming)."""
        if self._worker_client_factory is None:
            yield self.http
            return
        with self._lock:
            client = self._worker_client_factory()
            client.cookies.update(self.http.cookies)
        try:
            yield client
        finally:
            client.close()

    # -------------------------------------------------------------------- terms

    def _scan_term_courses(self, credential_key: str, *, refresh: bool = False) -> dict[int, list[dict[str, Any]] | None]:
        """Courses per candidate term id; cached so terms and current courses share one scan."""
        scanned: dict[int, list[dict[str, Any]] | None] = {}
        missing: list[int] = []
        for term_id in self.current_term_candidates:
            cached = None if refresh else self._term_courses_cache.get((credential_key, term_id))
            if cached is None:
                missing.append(term_id)
            else:
                scanned[term_id] = cached
        outcomes = self._parallel_map(
            lambda client, term_id: LearningXLister(client).list_courses(term_ids=[term_id]), missing, workers=self.term_scan_workers
        )
        for term_id, outcome in zip(missing, outcomes):
            if isinstance(outcome, BaseException):
                scanned[term_id] = None
            else:
                scanned[term_id] = outcome
                self._term_courses_cache.set((credential_key, term_id), outcome)
        return scanned

    def list_terms(self, username: str, password: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        credential_key = _credential_fingerprint(username, password)
        cache_key = (credential_key, tuple(self.current_term_candidates))

        def load() -> list[dict[str, Any]]:
            with self._lock:
                terms = self._discover_terms(username, password, credential_key, refresh=refresh)
                if not terms:
                    # Cached login state can outlive the LearningX xn_api_token cookie. Empty
                    # term discovery after a cached login is therefore not enough to prove the
                    # student has no courses; invalidate the cached session and try a fresh
                    # SmartID/LMS login once before API routes surface an auth-verification error.
                    self._forget_credential_session(credential_key)
                    terms = self._discover_terms(username, password, credential_key, refresh=True)
                return terms

        return self._terms_cache.get_or_load(cache_key, load, refresh=refresh, cache_if=bool)

    def _discover_terms(self, username: str, password: str, credential_key: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        login_result = self.login(username, password)
        if not login_result.ok:
            return []
        scanned = self._scan_term_courses(credential_key, refresh=refresh)
        terms: list[dict[str, Any]] = []
        latest_current: int | None = None
        for term_id in self.current_term_candidates:
            courses = scanned.get(term_id)
            if courses is None:
                continue
            current_count = sum(1 for course in courses if course.get("ended") is False)
            if current_count:
                latest_current = term_id
            if courses:
                terms.append(
                    {
                        "term_id": term_id,
                        "course_count": len(courses),
                        "current_course_count": current_count,
                        "is_current": False,
                        "label": _term_label(term_id, courses),
                    }
                )
        if latest_current is not None:
            for term in terms:
                term["is_current"] = term["term_id"] == latest_current
        return terms

    def _current_courses(self, credential_key: str, *, refresh: bool = False) -> list[dict[str, Any]]:
        scanned = self._scan_term_courses(credential_key, refresh=refresh)
        return latest_current_courses(scanned, self.current_term_candidates)

    def _term_courses(self, credential_key: str, term_ids: list[int], *, refresh: bool = False) -> list[dict[str, Any]]:
        """Courses of explicit terms; one term shares the cache the terms scan filled.

        The current term also lists still-open courses of other terms (see `latest_current_courses`).
        """
        if len(term_ids) == 1:
            scanned = self._scan_term_courses(credential_key)
            if term_ids[0] == latest_current_term(scanned, self.current_term_candidates):
                if refresh:  # only terms with open courses can change what this list shows
                    lister = LearningXLister(self.http)
                    for term_id, courses in list(scanned.items()):
                        if term_id == term_ids[0] or any(course.get("ended") is False for course in courses or []):
                            scanned[term_id] = self._term_courses_cache.set((credential_key, term_id), lister.list_courses(term_ids=[term_id]))
                return latest_current_courses(scanned, self.current_term_candidates)
        if len(term_ids) != 1:
            return LearningXLister(self.http).list_courses(term_ids=term_ids)
        key = (credential_key, term_ids[0])
        cached = None if refresh else self._term_courses_cache.get(key)
        if cached is not None:
            return cached
        return self._term_courses_cache.set(key, LearningXLister(self.http).list_courses(term_ids=term_ids))

    # ------------------------------------------------------------------ courses

    def _course_modules(self, course_id: int | str, *, refresh: bool = False, max_age: float | None = None, http: Any = None) -> list[dict[str, Any]]:
        """Module list of a course: cached, or fetched when `refresh`/older than `max_age`."""
        key = (str(course_id),)
        if not refresh:
            age = self._modules_cache.age(key)
            cached = self._modules_cache.get(key)
            if cached is not None and age is not None and (max_age is None or age <= max_age):
                return cached
        modules = LearningXLister(http or self.http).list_course_modules(course_id)
        return self._modules_cache.set(key, modules)

    def _fetch_modules(self, course_ids: list[int | str], *, refresh: bool = False) -> dict[str, list[dict[str, Any]]]:
        """Module lists for several courses, the missing ones fetched in parallel."""
        modules_by_id: dict[str, list[dict[str, Any]]] = {}
        wanted: list[int | str] = []
        for course_id in course_ids:
            key = str(course_id)
            if key in modules_by_id or any(str(w) == key for w in wanted):
                continue
            cached = None if refresh else self._modules_cache.get((key,))
            if cached is not None:
                modules_by_id[key] = cached
            else:
                wanted.append(course_id)
        outcomes = self._parallel_map(
            lambda client, course_id: LearningXLister(client).list_course_modules(course_id), wanted, workers=self.term_scan_workers
        )
        first_error: BaseException | None = None
        for course_id, outcome in zip(wanted, outcomes):
            if isinstance(outcome, BaseException):
                first_error = first_error or outcome
            else:
                modules_by_id[str(course_id)] = self._modules_cache.set((str(course_id),), outcome)
        if first_error is not None:
            raise first_error
        return modules_by_id

    def discover_courses(self, username: str, password: str, term_ids: list[int] | None = None, *, refresh: bool = False) -> CourseDiscoveryResult:
        credential_key = _credential_fingerprint(username, password)
        cache_key = (credential_key, tuple(term_ids) if term_ids else ("current", tuple(self.current_term_candidates)))

        def operation() -> list[CourseSummary]:
            lister = LearningXLister(self.http)
            courses = self._term_courses(credential_key, term_ids, refresh=refresh) if term_ids else self._current_courses(credential_key, refresh=refresh)
            course_ids = [course.get("id") or course.get("course_id") for course in courses]
            # Fresh module lists (completion state) whenever the course list itself is rebuilt.
            modules_by_id = self._fetch_modules([course_id for course_id in course_ids if course_id is not None], refresh=True)
            summary: list[CourseSummary] = []
            for course, course_id in zip(courses, course_ids):
                if course_id is None:
                    continue
                modules = modules_by_id[str(course_id)]
                tasks = [item for item in lister.extract_course_content_items(modules) if is_task(item)]
                summary.append(CourseSummary.from_course_and_modules(course, modules, lister.extract_lecture_items(modules), tasks=tasks))
            return summary

        def load() -> CourseDiscoveryResult:
            login_result, summary = self._authenticated(username, password, operation)
            if not login_result.ok or summary is None:
                return CourseDiscoveryResult(ok=False, error_code=login_result.error_code, login=login_result, courses=[])
            return CourseDiscoveryResult(ok=True, error_code=None, login=login_result, courses=summary)

        return self._courses_cache.get_or_load(cache_key, load, refresh=refresh, cache_if=lambda result: result.ok)

    def list_deadlines(
        self,
        username: str,
        password: str,
        term_ids: list[int] | None = None,
        *,
        refresh: bool = False,
        now: datetime | None = None,
    ) -> DeadlinesResult:
        """Upcoming and recently missed videos/assignments/quizzes across the courses of a term.

        Built from the cached course list and module lists, so right after the course list
        was loaded it needs no upstream request. `refresh` reloads both once.
        """
        courses_result = self.discover_courses(username, password, term_ids=term_ids, refresh=refresh)
        if not courses_result.ok:
            return DeadlinesResult(ok=False, error_code=courses_result.error_code, deadlines=[])
        now = now or datetime.now(timezone.utc)
        course_ids = [course.id for course in courses_result.courses]

        def operation() -> list[DeadlineItem]:
            lister = LearningXLister(self.http)
            # The course list just (re)loaded these module lists; a cached copy is current enough.
            modules_by_id = self._fetch_modules(course_ids)
            rows = []
            for course in courses_result.courses:
                content_items = lister.extract_course_content_items(modules_by_id[str(course.id)])
                rows.extend(build_deadline_items(course_id=course.id, course_name=course.name, content_items=content_items, now=now))
            return sort_deadline_rows(rows)

        login_result, deadlines = self._authenticated(username, password, operation)
        if not login_result.ok or deadlines is None:
            return DeadlinesResult(ok=False, error_code=login_result.error_code, deadlines=[])
        return DeadlinesResult(ok=True, error_code=None, deadlines=deadlines)

    def get_course_detail(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        course_name: str | None = None,
        refresh: bool = False,
    ) -> CourseDetailResult:
        credential_key = _credential_fingerprint(username, password)
        cache_key = (credential_key, str(course_id), course_name or "")

        def operation() -> CourseDetailResult:
            lister = LearningXLister(self.http)
            # Usually already fetched by the course list a moment ago.
            modules = self._course_modules(course_id, refresh=refresh)
            lectures = lister.extract_lecture_items(modules)
            content_items = lister.extract_course_content_items(modules)
            tasks = [item for item in content_items if is_task(item)]
            course = CourseSummary.from_lectures(id=course_id, name=course_name or "", module_count=len(modules), lectures=lectures, tasks=tasks)
            return CourseDetailResult(ok=True, error_code=None, course=course, lectures=lectures, content_items=content_items)

        def load() -> CourseDetailResult:
            login_result, detail = self._authenticated(username, password, operation)
            if not login_result.ok or detail is None:
                return CourseDetailResult(ok=False, error_code=login_result.error_code, course=None, lectures=[], content_items=[])
            return detail

        return self._course_detail_cache.get_or_load(cache_key, load, refresh=refresh, cache_if=lambda result: result.ok)

    # -------------------------------------------------------------------- media

    def resolve_media(self, username: str, password: str, *, course_id: int | str, module_item_id: int | str) -> ResolvedMediaResult:
        login_result, resolved = self._resolve_authenticated(username, password, course_id, module_item_id)
        if not login_result.ok or resolved is None:
            return _failure(ResolvedMediaResult, login_result.error_code, course_id=course_id, module_item_id=module_item_id)
        return resolved.result

    def download_lecture(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        module_item_id: int | str,
        course_name: str,
        output_dir: Path,
        dry_run: bool,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        conflict_strategy: str = 'rename',
    ) -> DownloadLectureResult:
        login_result, resolved = self._resolve_authenticated(username, password, course_id, module_item_id)
        if not login_result.ok or resolved is None:
            return _failure(
                DownloadLectureResult, login_result.error_code, dry_run=dry_run, written=False, course_id=course_id, module_item_id=module_item_id
            )

        plan, file_size = _planned_file(resolved, output_dir=output_dir, course_name=course_name, fallback_title="lecture", conflict_strategy=conflict_strategy)
        written = False
        skipped = False
        if plan.file_exists and conflict_strategy == 'skip':
            skipped = True
            emit_progress(on_progress, {'stage': 'download_skipped', 'module_item_id': module_item_id, 'output_path': str(plan.file_path), 'file_size': file_size})
        elif not dry_run:
            self._download_resolved(
                username,
                password,
                course_id=course_id,
                module_item_id=module_item_id,
                resolved=resolved,
                plan=plan,
                on_progress=on_progress,
                should_cancel=should_cancel,
            )
            written = True
            file_size = plan.file_path.stat().st_size

        return _download_result(resolved.result, plan, dry_run=dry_run, written=written, skipped=skipped, file_size=file_size, conflict_strategy=conflict_strategy)

    def _download_resolved(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        module_item_id: int | str,
        resolved: _ResolvedMediaInternal,
        plan: Any,
        on_progress: Callable[[dict[str, Any]], None] | None,
        should_cancel: Callable[[], bool] | None,
    ) -> None:
        """Transfer the bytes on a private HTTP client.

        A 401/403 from the media host means the signed URL expired: resolve the item
        again once (bypassing the resolved-media cache) and resume into the same
        `.part` file.
        """

        def transfer(current: _ResolvedMediaInternal) -> None:
            with self._download_http_client() as http:
                download_to_plan(
                    http=http,
                    download_url=current.raw_download_url,
                    plan=plan,
                    referer=current.referer,
                    max_attempts=MEDIA_DOWNLOAD_MAX_ATTEMPTS,
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                )

        try:
            transfer(resolved)
        except Exception as exc:
            if not is_signed_url_failure(exc):
                raise
            emit_progress(on_progress, {'stage': 'download_reresolving', 'module_item_id': module_item_id, 'output_path': str(plan.file_path)})
            login_result, fresh = self._resolve_authenticated(username, password, course_id, module_item_id, refresh=True)
            if not login_result.ok or fresh is None:
                raise
            transfer(fresh)

    def native_download_plan(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        module_item_id: int | str,
        course_name: str,
        output_dir: Path,
        conflict_strategy: str = 'rename',
    ) -> NativeDownloadPlanResult:
        # The desktop shell transfers right away, and re-plans after a CDN rejection, so it
        # always needs a freshly signed URL rather than the short-lived resolved-media cache.
        login_result, resolved = self._resolve_authenticated(username, password, course_id, module_item_id, refresh=True)
        if not login_result.ok or resolved is None:
            failed = _failure(
                DownloadLectureResult, login_result.error_code, dry_run=True, written=False, course_id=course_id, module_item_id=module_item_id
            )
            return NativeDownloadPlanResult(result=failed, native_download=None)

        plan, file_size = _planned_file(resolved, output_dir=output_dir, course_name=course_name, fallback_title="lecture", conflict_strategy=conflict_strategy)
        skipped = plan.file_exists and conflict_strategy == 'skip'
        download_result = _download_result(resolved.result, plan, dry_run=True, written=False, skipped=skipped, file_size=file_size, conflict_strategy=conflict_strategy)
        return NativeDownloadPlanResult(
            result=download_result,
            native_download=None if skipped else NativeDownloadTransferPlan(
                download_url=resolved.raw_download_url,
                referer=resolved.referer,
                output_path=str(plan.file_path),
                total_bytes=resolved.result.download_size,
            ),
        )

    # ------------------------------------------------------------------- viewer

    def official_viewer_plan(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        module_item_id: int | str,
    ) -> OfficialViewerPlanResult:
        login_result, resolved = self._authenticated(
            username, password, lambda: self._resolve_official_viewer_authenticated(course_id=course_id, module_item_id=module_item_id)
        )
        if not login_result.ok or resolved is None:
            return _failure(OfficialViewerPlanResult, login_result.error_code, course_id=course_id, module_item_id=module_item_id)
        return resolved.result

    def official_viewer_session_bridge_plan(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        module_item_id: int | str,
    ) -> OfficialViewerSessionBridgeResult:
        def operation() -> tuple[OfficialViewerPlanResult, list[OfficialViewerBridgeCookie], Any]:
            resolved = self._resolve_official_viewer_authenticated(course_id=course_id, module_item_id=module_item_id).result
            trace = self.http.trace.to_dict() if hasattr(self.http, "trace") else None
            return resolved, _bridge_cookies(self.http.cookies), trace

        login_result, outcome = self._authenticated(username, password, operation)
        if not login_result.ok or outcome is None:
            return _failure(
                OfficialViewerSessionBridgeResult,
                login_result.error_code,
                course_id=course_id,
                module_item_id=module_item_id,
                cookies=[],
                diagnostics={"cookie_count": 0, "cookie_names": []},
            )
        resolved, cookies, trace = outcome
        launch_url = f"https://canvas.ssu.ac.kr/courses/{course_id}/modules/items/{module_item_id}"
        return OfficialViewerSessionBridgeResult(
            ok=resolved.ok,
            error_code=resolved.error_code,
            course_id=resolved.course_id,
            module_item_id=resolved.module_item_id,
            attendance_item_id=resolved.attendance_item_id,
            title=resolved.title,
            content_id=resolved.content_id,
            content_type=resolved.content_type,
            duration=resolved.duration,
            viewer_url=resolved.viewer_url,
            launch_url=launch_url,
            cookies=cookies,
            diagnostics={
                "cookie_count": len(cookies),
                "cookie_names": [cookie.name for cookie in cookies],
                "cookie_domains": sorted({cookie.domain for cookie in cookies}),
                "http_trace": trace,
            },
        )

    # ---------------------------------------------------- LMS pages, Canvas files

    def lms_session_cookies(self, username: str, password: str) -> LmsSessionCookiesResult:
        """The session cookies an in-app LMS page window needs (wiki pages, boards, assignments)."""
        login_result, cookies = self._authenticated(username, password, lambda: _bridge_cookies(self.http.cookies))
        if not login_result.ok or cookies is None:
            return LmsSessionCookiesResult(ok=False, error_code=login_result.error_code, cookies=[], diagnostics={"cookie_count": 0})
        return LmsSessionCookiesResult(
            ok=True,
            error_code=None,
            cookies=cookies,
            diagnostics={"cookie_count": len(cookies), "cookie_names": [cookie.name for cookie in cookies]},
        )

    def _canvas_json(self, step: str, path: str) -> Any:
        """GET a Canvas API path on the shared session (caller holds the lock)."""
        response = self.http.get(
            step,
            f"{CANVAS_ORIGIN}{path}",
            headers={"Accept": "application/json+canvas-string-ids, application/json", "Referer": f"{CANVAS_ORIGIN}/"},
        )
        response.raise_for_status()
        return parse_canvas_json(response)

    def list_page_files(self, username: str, password: str, *, course_id: int | str, page_ref: str) -> PageFilesResult:
        """Files linked from a wiki page, each with its name, size and signed download address."""

        def operation() -> PageFilesResult:
            page = self._canvas_json("get_canvas_page", f"/api/v1/courses/{quote(str(course_id), safe='')}/pages/{quote(page_ref, safe='')}")
            page = page if isinstance(page, dict) else {}
            files: list[PageFile] = []
            for link in extract_file_links(str(page.get("body") or ""), MAX_PAGE_FILES):
                files.append(self._page_file(link.file_id, link.link_text))
            title = page.get("title")
            return PageFilesResult(ok=True, error_code=None, title=title if isinstance(title, str) else None, files=files)

        login_result, result = self._authenticated(username, password, operation)
        if not login_result.ok or result is None:
            return PageFilesResult(ok=False, error_code=login_result.error_code, title=None, files=[])
        return result

    def _page_file(self, file_id: str, link_text: str, *, strict: bool = False) -> PageFile:
        try:
            info = self._canvas_json("get_canvas_file", f"/api/v1/files/{quote(file_id, safe='')}")
        except Exception as exc:
            # A forbidden, removed or locked file is not a lost session: it is still listed (by
            # its link text) but cannot be downloaded.
            if response_status(exc) not in (403, 404, 410) and (strict or is_session_lost(exc)):
                raise
            return PageFile(file_id=file_id, display_name=link_text or f"file-{file_id}", size=None, download_url=None)
        info = info if isinstance(info, dict) else {}
        name = info.get("display_name") or info.get("filename") or link_text or f"file-{file_id}"
        url = info.get("url")
        return PageFile(
            file_id=file_id,
            display_name=str(name),
            size=_optional_int(info.get("size")),
            download_url=url if isinstance(url, str) and url.startswith("https://") else None,
            content_type=info.get("content-type") if isinstance(info.get("content-type"), str) else None,
        )

    def download_canvas_file(
        self,
        username: str,
        password: str,
        *,
        file_id: str,
        course_name: str,
        output_dir: Path,
        conflict_strategy: str = 'skip',
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> CanvasFileDownloadResult:
        """Save one Canvas file into the course folder (same `.part` -> rename handling as lectures)."""

        def failure(code: str | None) -> CanvasFileDownloadResult:
            return CanvasFileDownloadResult(
                ok=False, error_code=code, file_id=file_id, display_name=None, output_path=None, file_size=None, conflict_strategy=conflict_strategy
            )

        login_result, file = self._authenticated(username, password, lambda: self._page_file(file_id, "", strict=True))
        if not login_result.ok or file is None:
            return failure(login_result.error_code)
        if file.download_url is None:
            return failure("canvas_file_unavailable")

        stem, extension = _split_file_name(file.display_name)
        plan = plan_download_path(
            output_dir=output_dir,
            course_name=course_name,
            lecture_title=stem,
            content_id=f"canvas-{file_id}",
            extension=extension or "bin",
            conflict_strategy=conflict_strategy,
        )
        if plan.file_exists and conflict_strategy == 'skip':
            emit_progress(on_progress, {'stage': 'download_skipped', 'output_path': str(plan.file_path)})
            return CanvasFileDownloadResult(
                ok=True,
                error_code=None,
                file_id=file_id,
                display_name=file.display_name,
                output_path=str(plan.file_path),
                file_size=plan.file_path.stat().st_size,
                skipped=True,
                conflict_strategy=conflict_strategy,
            )

        def transfer(current: PageFile) -> None:
            with self._download_http_client() as http:
                download_to_plan(
                    http=http,
                    download_url=str(current.download_url),
                    plan=plan,
                    referer=f"{CANVAS_ORIGIN}/",
                    max_attempts=3,
                    on_progress=on_progress,
                    should_cancel=should_cancel,
                    expected_total_bytes=current.size,
                )

        try:
            transfer(file)
        except Exception as exc:
            if not is_signed_url_failure(exc):
                raise
            # The signed address expired: ask Canvas for a fresh one once and resume.
            login_result, fresh = self._authenticated(username, password, lambda: self._page_file(file_id, "", strict=True))
            if not login_result.ok or fresh is None or fresh.download_url is None:
                raise
            transfer(fresh)
        return CanvasFileDownloadResult(
            ok=True,
            error_code=None,
            file_id=file_id,
            display_name=file.display_name,
            output_path=str(plan.file_path),
            file_size=plan.file_path.stat().st_size,
            written=True,
            conflict_strategy=conflict_strategy,
        )

    # -------------------------------------------------------------------- batch

    def batch_download_lectures(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        course_name: str,
        output_dir: Path,
        dry_run: bool,
        module_item_ids: list[int | str] | None = None,
        include_completed: bool = False,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
        conflict_strategy: str = 'rename',
        max_parallel_downloads: int = 4,
        on_item_ready: Callable[[BatchDownloadItemResult], None] | None = None,
    ) -> BatchDownloadResult:
        """Plan (dry run) or download the selected lectures.

        `on_item_ready(item)` (real downloads only) is called once per item as soon as that item is
        settled: skipped or unresolvable items right after planning, downloaded or failed items the
        moment their own download ends, without waiting for the other downloads. Items dropped
        because the batch was cancelled are not reported. The callback must be quick and must not
        raise; an exception from it is ignored so one consumer error cannot stop the batch.
        """

        def emit(event: dict[str, Any]) -> None:
            emit_progress(on_progress, event)

        def item_ready(item: BatchDownloadItemResult) -> None:
            if on_item_ready is None or dry_run:
                return
            try:
                on_item_ready(item)
            except Exception:  # noqa: BLE001 - a consumer bug must not abort the downloads
                logging.getLogger(__name__).exception('on_item_ready callback failed')

        def cancelled() -> bool:
            return bool(should_cancel and should_cancel())

        def load_content_items() -> list:
            # One module fetch per course; item resolution below reuses it through the modules cache.
            modules = self._course_modules(course_id, max_age=BATCH_MODULES_REUSE_SECONDS)
            return LearningXLister(self.http).extract_course_content_items(modules)

        login_result, content_items = self._authenticated(username, password, load_content_items)
        if not login_result.ok or content_items is None:
            return BatchDownloadResult(ok=False, error_code=login_result.error_code, dry_run=dry_run, course_id=course_id, course_name=course_name, items=[])

        selected_lectures = _select_batch_lectures(content_items, module_item_ids=module_item_ids, include_completed=include_completed)
        total = len(selected_lectures)
        numbered = list(enumerate(selected_lectures, start=1))

        def plan_one(client: Any, entry: tuple[int, Any]) -> Any:
            index, lecture = entry
            if cancelled():
                return _NOT_STARTED
            emit({"stage": "resolving", "index": index, "total": total, "module_item_id": lecture.module_item_id, "title": lecture.title})
            return self._plan_batch_item(
                client,
                index=index,
                total=total,
                lecture=lecture,
                course_id=course_id,
                course_name=course_name,
                output_dir=output_dir,
                dry_run=dry_run,
                conflict_strategy=conflict_strategy,
                emit=emit,
            )

        def plan_all() -> list[Any]:
            # Lectures are resolved concurrently; items finished before a session expiry
            # are served from the resolved-media cache when `_authenticated` retries.
            outcomes = self._parallel_map(plan_one, numbered, workers=self.resolve_workers)
            for outcome in outcomes:
                if isinstance(outcome, Exception) and is_session_lost(outcome):
                    raise outcome
            return outcomes

        login_result, outcomes = self._authenticated(username, password, plan_all)
        if not login_result.ok or outcomes is None:
            outcomes = [login_result.error_code or "authentication_failed"] * len(numbered)  # str outcome = login failure code

        immediate_results: dict[int, BatchDownloadItemResult] = {}
        download_tasks: list[dict[str, Any]] = []
        for (index, lecture), outcome in zip(numbered, outcomes):
            if outcome is _NOT_STARTED:
                continue
            module_item_id = lecture.module_item_id
            if isinstance(outcome, (Exception, str)):
                code = outcome if isinstance(outcome, str) else type(outcome).__name__
                emit({"stage": "failed", "index": index, "total": total, "module_item_id": module_item_id, "title": lecture.title, "error_code": code})
                immediate_results[index] = _batch_item_result(course_id=course_id, module_item_id=module_item_id, lecture=lecture, dry_run=dry_run, ok=False, error_code=code)
            elif isinstance(outcome, BatchDownloadItemResult):
                immediate_results[index] = outcome
            else:
                download_tasks.append(outcome)

        completed_results = dict(immediate_results)
        for index in sorted(immediate_results):
            item_ready(immediate_results[index])
        emit({
            "stage": "batch_prepare_done",
            "total": total,
            "resolved_count": len(immediate_results) + len(download_tasks),
            "download_count": len(download_tasks),
        })
        if download_tasks:
            completed_results.update(
                self._run_batch_downloads(
                    username,
                    password,
                    download_tasks,
                    course_id=course_id,
                    total=total,
                    dry_run=dry_run,
                    conflict_strategy=conflict_strategy,
                    max_parallel_downloads=max_parallel_downloads,
                    emit=emit,
                    should_cancel=should_cancel,
                    on_item_ready=item_ready,
                )
            )

        items = [completed_results[index] for index in sorted(completed_results)]
        return BatchDownloadResult(
            ok=all(item.ok for item in items),
            error_code=None if all(item.ok for item in items) else "partial_failure",
            dry_run=dry_run,
            course_id=course_id,
            course_name=course_name,
            items=items,
        )

    def _plan_batch_item(
        self,
        client: Any,
        *,
        index: int,
        total: int,
        lecture: Any,
        course_id: int | str,
        course_name: str,
        output_dir: Path,
        dry_run: bool,
        conflict_strategy: str,
        emit: Callable[[dict[str, Any]], None],
    ) -> BatchDownloadItemResult | dict[str, Any]:
        """Resolve one lecture on `client`: a finished item result (planned/skipped) or a download task."""
        module_item_id = lecture.module_item_id
        resolved = self._resolve_media_cached(client, course_id=course_id, module_item_id=module_item_id)
        result = resolved.result
        plan, file_size = _planned_file(resolved, output_dir=output_dir, course_name=course_name, fallback_title=lecture.title or "lecture", conflict_strategy=conflict_strategy)
        title = result.title or lecture.title
        item_kwargs = {"course_id": course_id, "module_item_id": module_item_id, "lecture": lecture, "dry_run": dry_run, "resolved": resolved, "plan": plan, "title": title, "file_size": file_size, "conflict_strategy": conflict_strategy}
        if dry_run:
            emit({"stage": "planned", "index": index, "total": total, "module_item_id": module_item_id, "title": title, "output_path": str(plan.file_path), "file_exists": plan.file_exists, "total_bytes": result.download_size})
            return _batch_item_result(**item_kwargs)
        if plan.file_exists and conflict_strategy == 'skip':
            emit({"stage": "skipped", "index": index, "total": total, "module_item_id": module_item_id, "title": title, "output_path": str(plan.file_path), "file_size": file_size, "bytes_written": file_size, "total_bytes": file_size})
            return _batch_item_result(**item_kwargs, skipped=True)
        return {
            "index": index,
            "lecture": lecture,
            "module_item_id": module_item_id,
            "resolved": resolved,
            "result": result,
            "plan": plan,
            "title": title,
            "file_size": file_size,
        }

    def _run_batch_downloads(
        self,
        username: str,
        password: str,
        download_tasks: list[dict[str, Any]],
        *,
        course_id: int | str,
        total: int,
        dry_run: bool,
        conflict_strategy: str,
        max_parallel_downloads: int,
        emit: Callable[[dict[str, Any]], None],
        should_cancel: Callable[[], bool] | None,
        on_item_ready: Callable[[BatchDownloadItemResult], None] | None = None,
    ) -> dict[int, BatchDownloadItemResult]:
        def settle(index: int, item: BatchDownloadItemResult) -> None:
            results[index] = item
            if on_item_ready is not None:
                on_item_ready(item)

        def download_planned(planned: dict[str, Any]) -> BatchDownloadItemResult:
            index = planned["index"]
            module_item_id = planned["module_item_id"]
            plan = planned["plan"]
            title = planned["title"]
            if should_cancel and should_cancel():
                # Queued work that starts after a cancel must not open a connection.
                raise DownloadCancelled('download cancelled')
            emit({"stage": "downloading", "index": index, "total": total, "module_item_id": module_item_id, "title": title, "output_path": str(plan.file_path), "total_bytes": planned["result"].download_size})

            def relay_download_progress(event: dict[str, Any]) -> None:
                emit({**event, "index": index, "total": total, "module_item_id": module_item_id, "title": title})

            self._download_resolved(
                username,
                password,
                course_id=course_id,
                module_item_id=module_item_id,
                resolved=planned["resolved"],
                plan=plan,
                on_progress=relay_download_progress,
                should_cancel=should_cancel,
            )
            file_size = plan.file_path.stat().st_size
            emit({"stage": "done", "index": index, "total": total, "module_item_id": module_item_id, "title": title, "output_path": str(plan.file_path), "file_size": file_size, "bytes_written": file_size, "total_bytes": file_size})
            return _batch_item_result(
                course_id=course_id,
                module_item_id=module_item_id,
                lecture=planned["lecture"],
                dry_run=dry_run,
                resolved=planned["resolved"],
                plan=plan,
                title=title,
                file_size=file_size,
                written=True,
                conflict_strategy=conflict_strategy,
            )

        results: dict[int, BatchDownloadItemResult] = {}
        worker_count = max(1, min(max_parallel_downloads, len(download_tasks)))
        emit({
            "stage": "batch_downloads_started",
            "total": total,
            "download_count": len(download_tasks),
            "max_parallel_downloads": worker_count,
        })
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="ssu-download") as executor:
            future_map = {executor.submit(download_planned, planned): planned for planned in download_tasks}
            for future in as_completed(future_map):
                planned = future_map[future]
                index = planned["index"]
                lecture = planned["lecture"]
                if should_cancel and should_cancel():
                    continue
                try:
                    downloaded = future.result()
                except Exception as exc:
                    title = planned.get("title") or lecture.title
                    emit({"stage": "failed", "index": index, "total": total, "module_item_id": planned["module_item_id"], "title": title, "error_code": type(exc).__name__})
                    settle(index, _batch_item_result(
                        course_id=course_id,
                        module_item_id=planned["module_item_id"],
                        lecture=lecture,
                        dry_run=dry_run,
                        plan=planned.get("plan"),
                        title=title,
                        ok=False,
                        error_code=type(exc).__name__,
                    ))
                else:
                    settle(index, downloaded)
        return results

    # ------------------------------------------------------- resolution (locked)

    def _launch_attendance_item(self, client: Any, *, course_id: int | str, module_item_id: int | str) -> _AttendanceLaunch:
        """Canvas module item -> LTI launch form -> LearningX attendance item detail."""
        module_item_url = f"https://canvas.ssu.ac.kr/courses/{course_id}/modules/items/{module_item_id}"
        module_item_response = client.get("fetch_module_item_html", module_item_url, allow_redirects=True)
        module_item_response.raise_for_status()
        form = extract_lti_launch_form(module_item_response.text)

        lti_response = client.post(
            "submit_lti_launch",
            form.action,
            data=form.fields,
            headers={"Referer": module_item_url},
            allow_redirects=True,
        )
        lti_response.raise_for_status()
        attendance_item_id = form.action.rstrip("/").split("/")[-1]

        api_headers = learningx_api_headers(client.cookies, referer=form.action)
        attendance_url = f"https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/attendance_items/{attendance_item_id}"
        attendance_response = client.get("get_attendance_item_detail", attendance_url, headers=api_headers)
        attendance_response.raise_for_status()
        return _AttendanceLaunch(
            action=form.action,
            attendance_item_id=attendance_item_id,
            api_headers=api_headers,
            media=parse_attendance_item_media(attendance_response.json()),
        )

    def _resolve_official_viewer_authenticated(self, *, course_id: int | str, module_item_id: int | str) -> _ResolvedViewerInternal:
        with self._lock:
            launch = self._launch_attendance_item(self.http, course_id=course_id, module_item_id=module_item_id)
        media = launch.media
        if not media.viewer_url:
            raise RuntimeError("attendance item does not expose official viewer_url")
        return _ResolvedViewerInternal(
            result=OfficialViewerPlanResult(
                ok=True,
                error_code=None,
                course_id=course_id,
                module_item_id=module_item_id,
                attendance_item_id=launch.attendance_item_id,
                title=media.title,
                content_id=media.content_id,
                content_type=media.content_type,
                duration=media.duration,
                viewer_url=media.viewer_url,
            ),
            raw_viewer_url=media.viewer_url,
        )

    def _module_item_media_from_course_modules(self, client: Any, *, course_id: int | str, module_item_id: int | str) -> AttendanceItemMedia | None:
        modules = self._course_modules(course_id, http=client)
        for module in modules:
            for item in module.get("module_items", []) or []:
                if not isinstance(item, dict) or str(item.get("module_item_id")) != str(module_item_id):
                    continue
                content_data = item.get("content_data") if isinstance(item.get("content_data"), dict) else {}
                item_content_data = content_data.get("item_content_data") if isinstance(content_data.get("item_content_data"), dict) else {}
                content_id = item_content_data.get("content_id")
                if not content_id:
                    return None
                item_url = item.get("url") if isinstance(item.get("url"), str) else None
                attendance_item_id = content_data.get("item_id")
                if attendance_item_id is None and item_url:
                    attendance_item_id = item_url.rstrip("/").split("/")[-1]
                return AttendanceItemMedia(
                    item_id=attendance_item_id,
                    course_id=content_data.get("course_id") or course_id,
                    title=item.get("title") or content_data.get("title") or item_content_data.get("title") or "",
                    learningx_content_id=content_data.get("item_content_id"),
                    content_id=content_id,
                    content_type=item_content_data.get("content_type"),
                    duration=item_content_data.get("duration"),
                    viewer_url=item_content_data.get("view_url") or item_url,
                )
        return None

    def _resolve_commons_download(
        self,
        client: Any,
        *,
        course_id: int | str,
        module_item_id: int | str,
        attendance_item_id: int | str | None,
        media: AttendanceItemMedia,
        api_headers: dict[str, str],
        referer: str,
    ) -> _ResolvedMediaInternal:
        if not media.content_id:
            raise RuntimeError("attendance item does not expose Commons content_id")

        commons_url = f"https://canvas.ssu.ac.kr/learningx/api/v1/commons/contents?content_id={media.content_id}"
        commons_response = client.get("get_commons_content", commons_url, headers=api_headers)
        commons_response.raise_for_status()
        download = parse_commons_download(commons_response.json())

        head_response = client.request(
            "head_commons_download",
            "HEAD",
            download.download_url,
            headers={"Referer": media.viewer_url or referer},
            allow_redirects=False,
        )
        head_location = head_response.headers.get("location")
        raw_download_url = urljoin(download.download_url, head_location) if head_location else download.download_url
        result = ResolvedMediaResult(
            ok=True,
            error_code=None,
            course_id=course_id,
            module_item_id=module_item_id,
            attendance_item_id=str(attendance_item_id) if attendance_item_id is not None else None,
            # The LMS lecture title, as the user sees it in the app; the media server's own name is a fallback.
            title=media.title or download.title,
            content_id=download.content_id,
            content_type=media.content_type,
            duration=media.duration,
            download_url=redact_url(download.download_url),
            download_size=download.total_file_size,
            head_status=head_response.status_code,
            head_location=redact_url(raw_download_url) if head_location else None,
            head_content_type=head_response.headers.get("content-type"),
            head_content_length=head_response.headers.get("content-length"),
        )
        return _ResolvedMediaInternal(
            result=result,
            raw_download_url=raw_download_url,
            referer=media.viewer_url or referer,
            extension=(media.content_type or "mp4").lower(),
        )

    def _resolve_media_with(self, client: Any, *, course_id: int | str, module_item_id: int | str) -> _ResolvedMediaInternal:
        """Module item -> Commons download metadata, all requests on `client` (no locking, no cache)."""
        module_media = self._module_item_media_from_course_modules(client, course_id=course_id, module_item_id=module_item_id)
        if module_media is not None:
            return self._resolve_commons_download(
                client,
                course_id=course_id,
                module_item_id=module_item_id,
                attendance_item_id=module_media.item_id,
                media=module_media,
                api_headers=learningx_api_headers(client.cookies),
                referer=f"https://canvas.ssu.ac.kr/courses/{course_id}/modules/items/{module_item_id}",
            )

        launch = self._launch_attendance_item(client, course_id=course_id, module_item_id=module_item_id)
        return self._resolve_commons_download(
            client,
            course_id=course_id,
            module_item_id=module_item_id,
            attendance_item_id=launch.attendance_item_id,
            media=launch.media,
            api_headers=launch.api_headers,
            referer=launch.action,
        )

    def _resolve_media_cached(self, client: Any, *, course_id: int | str, module_item_id: int | str, refresh: bool = False) -> _ResolvedMediaInternal:
        """`_resolve_media_with` behind the short-lived resolved-media cache (plan -> download reuse)."""
        key = (str(course_id), str(module_item_id))
        cached = None if refresh else self._resolved_cache.get(key)
        if cached is not None:
            return cached
        return self._resolved_cache.set(key, self._resolve_media_with(client, course_id=course_id, module_item_id=module_item_id))

    def _resolve_media_authenticated(self, *, course_id: int | str, module_item_id: int | str, refresh: bool = False) -> _ResolvedMediaInternal:
        with self._lock:
            return self._resolve_media_cached(self.http, course_id=course_id, module_item_id=module_item_id, refresh=refresh)


def _split_file_name(name: str) -> tuple[str, str]:
    """`week 1.PDF` -> (`week 1`, `pdf`); a name without a usable extension -> (name, '')."""
    base = name.rsplit("/", 1)[-1].strip() or "file"
    stem, dot, extension = base.rpartition(".")
    if not dot or not stem or not extension.isalnum() or len(extension) > 8:
        return base, ""
    return stem, extension.lower()


def _select_batch_lectures(content_items: list, *, module_item_ids: list[int | str] | None, include_completed: bool) -> list:
    selected_ids = {str(item_id) for item_id in module_item_ids} if module_item_ids else None
    selected = []
    for lecture in content_items:
        if not lecture.downloadable or lecture.module_item_id is None:
            continue
        if selected_ids is not None:
            if str(lecture.module_item_id) in selected_ids:
                selected.append(lecture)
        elif include_completed or lecture.completed is not True:
            selected.append(lecture)
    return selected
