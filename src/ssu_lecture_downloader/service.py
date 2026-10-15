from __future__ import annotations

import queue
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, Callable, TypeVar
from urllib.parse import urljoin

from ssu_lecture_downloader.download import DownloadCancelled, download_to_plan, plan_download_path
from ssu_lecture_downloader.errors import is_lms_auth_failure, is_signed_url_failure
from ssu_lecture_downloader.http_client import HttpClient
from ssu_lecture_downloader.lms import DEFAULT_TERM_ID_CANDIDATES, LearningXLister
from ssu_lecture_downloader.login_flow import LoginDiscoveryResult
from ssu_lecture_downloader.media import AttendanceItemMedia, extract_lti_launch_form, parse_attendance_item_media, parse_commons_download
from ssu_lecture_downloader.models import CourseSummary
from ssu_lecture_downloader.redaction import redact_url


MEDIA_DOWNLOAD_MAX_ATTEMPTS = 8
# A login older than this is renewed even without an auth failure, so long-lived
# backends do not keep using a silently expired LMS session.
SESSION_TTL_SECONDS = 30 * 60
# Parallelism of the term-id discovery scan (separate HTTP clients, bounded).
TERM_SCAN_WORKERS = 4
# Course module lists are reused while resolving several items of one course.
MODULES_CACHE_TTL_SECONDS = 300

T = TypeVar("T")


LoginFunc = Callable[[str, str, object], LoginDiscoveryResult]


def _default_login_func(username: str, password: str, http: object) -> LoginDiscoveryResult:
    # Imported lazily: the login flow pulls in bs4 and cryptography, which the API
    # does not need until the first login.
    from ssu_lecture_downloader.login_flow import run_http_login_discovery

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
        items = getattr(cookies, "items", lambda: [])()
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



class _TtlCache:
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self._values: dict[tuple, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: tuple):
        with self._lock:
            entry = self._values.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= monotonic():
                self._values.pop(key, None)
                return None
            return value

    def set(self, key: tuple, value: Any) -> Any:
        with self._lock:
            self._values[key] = (monotonic() + self.ttl_seconds, value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._values.clear()


def _credential_fingerprint(username: str, password: str) -> str:
    return sha256(f"{username}\0{password}".encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _AttendanceLaunch:
    """Result of the LTI launch -> attendance item lookup shared by viewer and media resolution."""

    action: str
    attendance_item_id: str
    api_headers: dict[str, str]
    media: AttendanceItemMedia


def _login_failed_download_result(login_result: LoginDiscoveryResult, *, dry_run: bool, course_id: int | str, module_item_id: int | str) -> DownloadLectureResult:
    return DownloadLectureResult(
        ok=False,
        error_code=login_result.error_code,
        dry_run=dry_run,
        written=False,
        course_id=course_id,
        module_item_id=module_item_id,
        attendance_item_id=None,
        title=None,
        content_id=None,
        download_url=None,
        download_size=None,
        output_path=None,
        file_size=None,
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
    `HttpClient` created by `_download_http_client()`, and the term scan fans out on
    separate clients as well.
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
    ) -> None:
        self.http = http or HttpClient()
        self.login_func = login_func or _default_login_func
        self.current_term_candidates = current_term_candidates or DEFAULT_TERM_ID_CANDIDATES
        self.session_ttl_seconds = session_ttl_seconds
        self.term_scan_workers = max(1, term_scan_workers)
        self._lock = threading.RLock()
        self._session_cache: dict[str, tuple[LoginDiscoveryResult, float]] = {}
        self._terms_cache = _TtlCache(cache_ttl_seconds)
        self._term_courses_cache = _TtlCache(cache_ttl_seconds)
        self._courses_cache = _TtlCache(cache_ttl_seconds)
        self._course_detail_cache = _TtlCache(cache_ttl_seconds)
        self._modules_cache = _TtlCache(min(cache_ttl_seconds, MODULES_CACHE_TTL_SECONDS))

    # ------------------------------------------------------------------ session

    def clear_cache(self) -> None:
        with self._lock:
            self._session_cache.clear()
            self._terms_cache.clear()
            self._term_courses_cache.clear()
            self._courses_cache.clear()
            self._course_detail_cache.clear()
            self._modules_cache.clear()
            self._clear_http_cookies()

    def _clear_http_cookies(self) -> None:
        cookies = getattr(self.http, "cookies", None)
        clear = getattr(cookies, "clear", None)
        if clear:
            clear()

    def _forget_credential_session(self, credential_key: str) -> None:
        with self._lock:
            self._session_cache.pop(credential_key, None)
            self._term_courses_cache.clear()
            self._modules_cache.clear()
            self._clear_http_cookies()

    def login(self, username: str, password: str) -> LoginDiscoveryResult:
        credential_key = _credential_fingerprint(username, password)
        with self._lock:
            cached = self._session_cache.get(credential_key)
            if cached is not None:
                result, logged_in_at = cached
                if monotonic() - logged_in_at < self.session_ttl_seconds:
                    return result
                self._session_cache.pop(credential_key, None)
            self._clear_http_cookies()
            result = self.login_func(username, password, self.http)
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
                if not is_lms_auth_failure(exc):
                    raise
                self._forget_credential_session(_credential_fingerprint(username, password))
                login_result = self.login(username, password)
                if not login_result.ok:
                    return login_result, None
                return login_result, operation()

    def _clone_http_client(self):
        """A private client carrying the current cookies, or the shared fake in tests."""
        if isinstance(self.http, HttpClient):
            with self._lock:
                client = HttpClient(trace=self.http.trace)
                client.cookies.update(self.http.cookies)
            return client
        return self.http

    @contextmanager
    def _download_http_client(self) -> Iterator[Any]:
        client = self._clone_http_client()
        try:
            yield client
        finally:
            if client is not self.http:
                client.close()

    # -------------------------------------------------------------------- terms

    def _fetch_term_courses(self, term_ids: list[int]) -> dict[int, list[dict[str, Any]] | None]:
        """Fetch several terms concurrently, each worker on its own HTTP client."""
        workers = max(1, min(self.term_scan_workers, len(term_ids)))
        clients = [self._clone_http_client() for _ in range(workers)]
        available: queue.Queue = queue.Queue()
        for client in clients:
            available.put(client)

        def fetch(term_id: int) -> tuple[int, list[dict[str, Any]] | None]:
            client = available.get()
            try:
                return term_id, LearningXLister(client).list_courses(term_ids=[term_id])
            except Exception:
                return term_id, None
            finally:
                available.put(client)

        try:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ssu-terms") as executor:
                return dict(executor.map(fetch, term_ids))
        finally:
            for client in clients:
                if client is not self.http:
                    client.close()

    def _scan_term_courses(self, credential_key: str) -> dict[int, list[dict[str, Any]] | None]:
        """Courses per candidate term id; cached so terms and current courses share one scan."""
        candidates = list(self.current_term_candidates)
        scanned: dict[int, list[dict[str, Any]] | None] = {}
        missing: list[int] = []
        for term_id in candidates:
            cached = self._term_courses_cache.get((credential_key, term_id))
            if cached is None:
                missing.append(term_id)
            else:
                scanned[term_id] = cached
        if missing:
            for term_id, courses in self._fetch_term_courses(missing).items():
                scanned[term_id] = courses
                if courses is not None:
                    self._term_courses_cache.set((credential_key, term_id), courses)
        return scanned

    def list_terms(self, username: str, password: str) -> list[dict[str, Any]]:
        credential_key = _credential_fingerprint(username, password)
        cache_key = (credential_key, tuple(self.current_term_candidates))
        with self._lock:
            cached = self._terms_cache.get(cache_key)
            if cached is not None:
                return cached

            terms = self._discover_terms(username, password, credential_key)
            if not terms:
                # Cached login state can outlive the LearningX xn_api_token cookie. Empty
                # term discovery after a cached login is therefore not enough to prove the
                # student has no courses; invalidate the cached session and try a fresh
                # SmartID/LMS login once before API routes surface an auth-verification error.
                self._forget_credential_session(credential_key)
                terms = self._discover_terms(username, password, credential_key)
            return self._terms_cache.set(cache_key, terms)

    def _discover_terms(self, username: str, password: str, credential_key: str) -> list[dict[str, Any]]:
        login_result = self.login(username, password)
        if not login_result.ok:
            return []
        scanned = self._scan_term_courses(credential_key)
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
                        "label": f"term {term_id}",
                    }
                )
        if latest_current is not None:
            for term in terms:
                term["is_current"] = term["term_id"] == latest_current
        return terms

    def _current_courses(self, credential_key: str) -> list[dict[str, Any]]:
        scanned = self._scan_term_courses(credential_key)
        latest_current_term_id: int | None = None
        for term_id in self.current_term_candidates:
            courses = scanned.get(term_id)
            if courses and any(course.get("ended") is False for course in courses):
                latest_current_term_id = term_id
        if latest_current_term_id is None:
            return []
        return [course for course in scanned[latest_current_term_id] or [] if course.get("ended") is False]

    # ------------------------------------------------------------------ courses

    def _course_modules(self, course_id: int | str, *, refresh: bool = False) -> list[dict[str, Any]]:
        key = (str(course_id),)
        if not refresh:
            cached = self._modules_cache.get(key)
            if cached is not None:
                return cached
        modules = LearningXLister(self.http).list_course_modules(course_id)
        return self._modules_cache.set(key, modules)

    def discover_courses(self, username: str, password: str, term_ids: list[int] | None = None) -> CourseDiscoveryResult:
        credential_key = _credential_fingerprint(username, password)
        cache_key = (credential_key, tuple(term_ids) if term_ids else ("current", tuple(self.current_term_candidates)))
        with self._lock:
            cached = self._courses_cache.get(cache_key)
            if cached is not None:
                return cached

            def operation() -> list[CourseSummary]:
                lister = LearningXLister(self.http)
                courses = lister.list_courses(term_ids=term_ids) if term_ids else self._current_courses(credential_key)
                summary: list[CourseSummary] = []
                for course in courses:
                    course_id = course.get("id") or course.get("course_id")
                    if course_id is None:
                        continue
                    modules = self._course_modules(course_id, refresh=True)
                    lectures = lister.extract_lecture_items(modules)
                    summary.append(CourseSummary.from_course_and_modules(course, modules, lectures))
                return summary

            login_result, summary = self._authenticated(username, password, operation)
            if not login_result.ok or summary is None:
                return CourseDiscoveryResult(ok=False, error_code=login_result.error_code, login=login_result, courses=[])
            return self._courses_cache.set(cache_key, CourseDiscoveryResult(ok=True, error_code=None, login=login_result, courses=summary))

    def get_course_detail(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        course_name: str | None = None,
    ) -> CourseDetailResult:
        credential_key = _credential_fingerprint(username, password)
        cache_key = (credential_key, str(course_id), course_name or "")
        with self._lock:
            cached = self._course_detail_cache.get(cache_key)
            if cached is not None:
                return cached

            def operation() -> CourseDetailResult:
                lister = LearningXLister(self.http)
                modules = self._course_modules(course_id, refresh=True)
                lectures = lister.extract_lecture_items(modules)
                content_items = lister.extract_course_content_items(modules)
                course = CourseSummary(
                    id=course_id,
                    name=course_name or "",
                    module_count=len(modules),
                    lecture_count=len(lectures),
                    uncompleted_lecture_count=sum(1 for lecture in lectures if not lecture.completed),
                )
                return CourseDetailResult(ok=True, error_code=None, course=course, lectures=lectures, content_items=content_items)

            login_result, detail = self._authenticated(username, password, operation)
            if not login_result.ok or detail is None:
                return CourseDetailResult(ok=False, error_code=login_result.error_code, course=None, lectures=[], content_items=[])
            return self._course_detail_cache.set(cache_key, detail)

    # -------------------------------------------------------------------- media

    def resolve_media(self, username: str, password: str, *, course_id: int | str, module_item_id: int | str) -> ResolvedMediaResult:
        login_result, resolved = self._authenticated(
            username, password, lambda: self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id)
        )
        if not login_result.ok or resolved is None:
            return ResolvedMediaResult(
                ok=False,
                error_code=login_result.error_code,
                course_id=course_id,
                module_item_id=module_item_id,
                attendance_item_id=None,
                title=None,
                content_id=None,
                content_type=None,
                duration=None,
                download_url=None,
                download_size=None,
            )
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
        login_result, resolved = self._authenticated(
            username, password, lambda: self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id)
        )
        if not login_result.ok or resolved is None:
            return _login_failed_download_result(login_result, dry_run=dry_run, course_id=course_id, module_item_id=module_item_id)

        result = resolved.result
        plan = plan_download_path(
            output_dir=output_dir,
            course_name=course_name,
            lecture_title=result.title or "lecture",
            content_id=result.content_id or "content",
            extension=resolved.extension or "mp4",
            conflict_strategy=conflict_strategy,
        )
        written = False
        skipped = False
        file_size: int | None = plan.file_path.stat().st_size if plan.file_exists and plan.file_path.exists() else None
        if plan.file_exists and conflict_strategy == 'skip':
            skipped = True
            if on_progress:
                on_progress({'stage': 'download_skipped', 'module_item_id': module_item_id, 'output_path': str(plan.file_path), 'file_size': file_size})
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

        return DownloadLectureResult(
            ok=True,
            error_code=None,
            dry_run=dry_run,
            written=written,
            course_id=course_id,
            module_item_id=module_item_id,
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
        again once and resume into the same `.part` file.
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
            if on_progress:
                on_progress({'stage': 'download_reresolving', 'module_item_id': module_item_id, 'output_path': str(plan.file_path)})
            login_result, fresh = self._authenticated(
                username, password, lambda: self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id)
            )
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
        login_result, resolved = self._authenticated(
            username, password, lambda: self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id)
        )
        if not login_result.ok or resolved is None:
            return NativeDownloadPlanResult(
                result=_login_failed_download_result(login_result, dry_run=True, course_id=course_id, module_item_id=module_item_id),
                native_download=None,
            )

        result = resolved.result
        plan = plan_download_path(
            output_dir=output_dir,
            course_name=course_name,
            lecture_title=result.title or "lecture",
            content_id=result.content_id or "content",
            extension=resolved.extension or "mp4",
            conflict_strategy=conflict_strategy,
        )
        skipped = plan.file_exists and conflict_strategy == 'skip'
        file_size: int | None = plan.file_path.stat().st_size if plan.file_exists and plan.file_path.exists() else None
        download_result = DownloadLectureResult(
            ok=True,
            error_code=None,
            dry_run=True,
            written=False,
            course_id=course_id,
            module_item_id=module_item_id,
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
        return NativeDownloadPlanResult(
            result=download_result,
            native_download=None if skipped else NativeDownloadTransferPlan(
                download_url=resolved.raw_download_url,
                referer=resolved.referer,
                output_path=str(plan.file_path),
                total_bytes=result.download_size,
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
            return OfficialViewerPlanResult(
                ok=False,
                error_code=login_result.error_code,
                course_id=course_id,
                module_item_id=module_item_id,
                attendance_item_id=None,
                title=None,
                content_id=None,
                content_type=None,
                duration=None,
                viewer_url=None,
            )
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
            return OfficialViewerSessionBridgeResult(
                ok=False,
                error_code=login_result.error_code,
                course_id=course_id,
                module_item_id=module_item_id,
                attendance_item_id=None,
                title=None,
                content_id=None,
                content_type=None,
                duration=None,
                viewer_url=None,
                launch_url=None,
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
    ) -> BatchDownloadResult:
        def emit(event: dict[str, Any]) -> None:
            if on_progress:
                on_progress(event)

        def cancelled() -> bool:
            return bool(should_cancel and should_cancel())

        def load_content_items() -> list:
            # One module fetch per course; item resolution below reuses it through the modules cache.
            lister = LearningXLister(self.http)
            return lister.extract_course_content_items(self._course_modules(course_id, refresh=True))

        login_result, content_items = self._authenticated(username, password, load_content_items)
        if not login_result.ok or content_items is None:
            return BatchDownloadResult(ok=False, error_code=login_result.error_code, dry_run=dry_run, course_id=course_id, course_name=course_name, items=[])

        selected_lectures = _select_batch_lectures(content_items, module_item_ids=module_item_ids, include_completed=include_completed)
        total = len(selected_lectures)
        immediate_results: dict[int, BatchDownloadItemResult] = {}
        download_tasks: list[dict[str, Any]] = []

        for index, lecture in enumerate(selected_lectures, start=1):
            if cancelled():
                break
            module_item_id = lecture.module_item_id
            assert module_item_id is not None
            emit({"stage": "resolving", "index": index, "total": total, "module_item_id": module_item_id, "title": lecture.title})
            try:
                outcome = self._plan_batch_item(
                    username,
                    password,
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
            except Exception as exc:
                emit({"stage": "failed", "index": index, "total": total, "module_item_id": module_item_id, "title": lecture.title, "error_code": type(exc).__name__})
                outcome = _batch_item_result(course_id=course_id, module_item_id=module_item_id, lecture=lecture, dry_run=dry_run, ok=False, error_code=type(exc).__name__)
            if isinstance(outcome, BatchDownloadItemResult):
                immediate_results[index] = outcome
            else:
                download_tasks.append(outcome)

        completed_results = dict(immediate_results)
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
        username: str,
        password: str,
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
        """Resolve one lecture: a finished item result (planned/skipped/login failure) or a download task."""
        module_item_id = lecture.module_item_id
        login_result, resolved = self._authenticated(
            username, password, lambda: self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id)
        )
        if not login_result.ok or resolved is None:
            emit({"stage": "failed", "index": index, "total": total, "module_item_id": module_item_id, "title": lecture.title, "error_code": login_result.error_code})
            return _batch_item_result(course_id=course_id, module_item_id=module_item_id, lecture=lecture, dry_run=dry_run, ok=False, error_code=login_result.error_code)

        result = resolved.result
        plan = plan_download_path(
            output_dir=output_dir,
            course_name=course_name,
            lecture_title=result.title or lecture.title or "lecture",
            content_id=result.content_id or "content",
            extension=resolved.extension or "mp4",
            conflict_strategy=conflict_strategy,
        )
        file_size: int | None = plan.file_path.stat().st_size if plan.file_exists and plan.file_path.exists() else None
        title = result.title or lecture.title
        if dry_run:
            emit({"stage": "planned", "index": index, "total": total, "module_item_id": module_item_id, "title": title, "output_path": str(plan.file_path), "file_exists": plan.file_exists, "total_bytes": result.download_size})
            return _batch_item_result(course_id=course_id, module_item_id=module_item_id, lecture=lecture, dry_run=dry_run, resolved=resolved, plan=plan, title=title, file_size=file_size, conflict_strategy=conflict_strategy)
        if plan.file_exists and conflict_strategy == 'skip':
            emit({"stage": "skipped", "index": index, "total": total, "module_item_id": module_item_id, "title": title, "output_path": str(plan.file_path), "file_size": file_size, "bytes_written": file_size, "total_bytes": file_size})
            return _batch_item_result(course_id=course_id, module_item_id=module_item_id, lecture=lecture, dry_run=dry_run, resolved=resolved, plan=plan, title=title, file_size=file_size, skipped=True, conflict_strategy=conflict_strategy)
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
    ) -> dict[int, BatchDownloadItemResult]:
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
                    results[index] = future.result()
                except Exception as exc:
                    title = planned.get("title") or lecture.title
                    emit({"stage": "failed", "index": index, "total": total, "module_item_id": planned["module_item_id"], "title": title, "error_code": type(exc).__name__})
                    results[index] = BatchDownloadItemResult(
                        ok=False,
                        error_code=type(exc).__name__,
                        dry_run=dry_run,
                        written=False,
                        course_id=course_id,
                        module_item_id=planned["module_item_id"],
                        title=title,
                        completed=lecture.completed,
                        attendance_item_id=None,
                        content_id=None,
                        download_url=None,
                        download_size=None,
                        output_path=str(planned["plan"].file_path) if planned.get("plan") else None,
                        file_size=None,
                    )
        return results

    # ------------------------------------------------------- resolution (locked)

    def _launch_attendance_item(self, *, course_id: int | str, module_item_id: int | str) -> _AttendanceLaunch:
        """Canvas module item -> LTI launch form -> LearningX attendance item detail."""
        module_item_url = f"https://canvas.ssu.ac.kr/courses/{course_id}/modules/items/{module_item_id}"
        module_item_response = self.http.get("fetch_module_item_html", module_item_url, allow_redirects=True)
        module_item_response.raise_for_status()
        form = extract_lti_launch_form(module_item_response.text)

        lti_response = self.http.post(
            "submit_lti_launch",
            form.action,
            data=form.fields,
            headers={"Referer": module_item_url},
            allow_redirects=True,
        )
        lti_response.raise_for_status()
        attendance_item_id = form.action.rstrip("/").split("/")[-1]

        api_headers = {
            "Authorization": f"Bearer {self.http.cookies.get('xn_api_token')}",
            "Accept": "application/json",
            "Referer": form.action,
        }
        attendance_url = f"https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/attendance_items/{attendance_item_id}"
        attendance_response = self.http.get("get_attendance_item_detail", attendance_url, headers=api_headers)
        attendance_response.raise_for_status()
        return _AttendanceLaunch(
            action=form.action,
            attendance_item_id=attendance_item_id,
            api_headers=api_headers,
            media=parse_attendance_item_media(attendance_response.json()),
        )

    def _resolve_official_viewer_authenticated(self, *, course_id: int | str, module_item_id: int | str) -> _ResolvedViewerInternal:
        with self._lock:
            launch = self._launch_attendance_item(course_id=course_id, module_item_id=module_item_id)
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

    def _module_item_media_from_course_modules(self, *, course_id: int | str, module_item_id: int | str) -> AttendanceItemMedia | None:
        modules = self._course_modules(course_id)
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
        commons_response = self.http.get("get_commons_content", commons_url, headers=api_headers)
        commons_response.raise_for_status()
        download = parse_commons_download(commons_response.json())

        head_response = self.http.request(
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
            title=download.title or media.title,
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

    def _resolve_media_authenticated(self, *, course_id: int | str, module_item_id: int | str) -> _ResolvedMediaInternal:
        with self._lock:
            api_headers = {
                "Authorization": f"Bearer {self.http.cookies.get('xn_api_token')}",
                "Accept": "application/json",
                "Referer": "https://canvas.ssu.ac.kr/learningx/dashboard",
            }
            module_media = self._module_item_media_from_course_modules(course_id=course_id, module_item_id=module_item_id)
            if module_media is not None:
                return self._resolve_commons_download(
                    course_id=course_id,
                    module_item_id=module_item_id,
                    attendance_item_id=module_media.item_id,
                    media=module_media,
                    api_headers=api_headers,
                    referer=f"https://canvas.ssu.ac.kr/courses/{course_id}/modules/items/{module_item_id}",
                )

            launch = self._launch_attendance_item(course_id=course_id, module_item_id=module_item_id)
            return self._resolve_commons_download(
                course_id=course_id,
                module_item_id=module_item_id,
                attendance_item_id=launch.attendance_item_id,
                media=launch.media,
                api_headers=launch.api_headers,
                referer=launch.action,
            )


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
