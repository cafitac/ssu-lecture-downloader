from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any, Callable
from urllib.parse import urljoin

from ssu_lecture_downloader.download import download_to_plan, plan_download_path
from ssu_lecture_downloader.http_client import HttpClient
from ssu_lecture_downloader.lms import DEFAULT_TERM_ID_CANDIDATES, LearningXLister
from ssu_lecture_downloader.login_flow import LoginDiscoveryResult, run_http_login_discovery
from ssu_lecture_downloader.media import AttendanceItemMedia, extract_lti_launch_form, parse_attendance_item_media, parse_commons_download
from ssu_lecture_downloader.models import CourseDetail, CourseSummary
from ssu_lecture_downloader.redaction import redact_url


MEDIA_DOWNLOAD_MAX_ATTEMPTS = 8


LoginFunc = Callable[[str, str, object], LoginDiscoveryResult]


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


class _TtlCache:
    def __init__(self, ttl_seconds: float) -> None:
        self.ttl_seconds = ttl_seconds
        self._values: dict[tuple, tuple[float, Any]] = {}

    def get(self, key: tuple):
        entry = self._values.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at <= monotonic():
            self._values.pop(key, None)
            return None
        return value

    def set(self, key: tuple, value: Any) -> Any:
        self._values[key] = (monotonic() + self.ttl_seconds, value)
        return value

    def clear(self) -> None:
        self._values.clear()


def _credential_fingerprint(username: str, password: str) -> str:
    return sha256(f"{username}\0{password}".encode("utf-8")).hexdigest()


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


class SsuLectureService:
    def __init__(
        self,
        *,
        http=None,
        login_func: LoginFunc = run_http_login_discovery,
        current_term_candidates: list[int] | None = None,
        cache_ttl_seconds: float = 900,
    ) -> None:
        self.http = http or HttpClient()
        self.login_func = login_func
        self.current_term_candidates = current_term_candidates or DEFAULT_TERM_ID_CANDIDATES
        self._session_cache: dict[str, LoginDiscoveryResult] = {}
        self._terms_cache = _TtlCache(cache_ttl_seconds)
        self._courses_cache = _TtlCache(cache_ttl_seconds)
        self._course_detail_cache = _TtlCache(cache_ttl_seconds)

    def clear_cache(self) -> None:
        self._session_cache.clear()
        self._terms_cache.clear()
        self._courses_cache.clear()
        self._course_detail_cache.clear()
        self._clear_http_cookies()

    def _clear_http_cookies(self) -> None:
        cookies = getattr(self.http, "cookies", None)
        clear = getattr(cookies, "clear", None)
        if clear:
            clear()

    def _forget_credential_session(self, credential_key: str) -> None:
        self._session_cache.pop(credential_key, None)
        self._clear_http_cookies()

    def _download_http_client(self):
        if isinstance(self.http, HttpClient):
            client = HttpClient(trace=self.http.trace)
            client.cookies.update(self.http.cookies)
            return client
        return self.http


    def list_terms(self, username: str, password: str) -> list[dict[str, Any]]:
        credential_key = _credential_fingerprint(username, password)
        cache_key = (credential_key, tuple(self.current_term_candidates))
        cached = self._terms_cache.get(cache_key)
        if cached is not None:
            return cached

        def fetch_terms_once() -> list[dict[str, Any]]:
            login_result = self.login(username, password)
            if not login_result.ok:
                return []
            lister = LearningXLister(self.http)
            terms: list[dict[str, Any]] = []
            latest_current: int | None = None
            for term_id in self.current_term_candidates:
                try:
                    courses = lister.list_courses(term_ids=[term_id])
                except Exception:
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

        terms = fetch_terms_once()
        if not terms:
            # Cached login state can outlive the LearningX xn_api_token cookie. Empty
            # term discovery after a cached login is therefore not enough to prove the
            # student has no courses; invalidate the cached session and try a fresh
            # SmartID/LMS login once before API routes surface an auth-verification error.
            self._forget_credential_session(credential_key)
            terms = fetch_terms_once()
        return self._terms_cache.set(cache_key, terms)

    def login(self, username: str, password: str) -> LoginDiscoveryResult:
        credential_key = _credential_fingerprint(username, password)
        cached = self._session_cache.get(credential_key)
        if cached is not None:
            return cached
        self._clear_http_cookies()
        result = self.login_func(username, password, self.http)
        if result.ok:
            self._session_cache[credential_key] = result
        return result

    def discover_courses(self, username: str, password: str, term_ids: list[int] | None = None) -> CourseDiscoveryResult:
        credential_key = _credential_fingerprint(username, password)
        cache_key = (credential_key, tuple(term_ids) if term_ids else ("current", tuple(self.current_term_candidates)))
        cached = self._courses_cache.get(cache_key)
        if cached is not None:
            return cached
        login_result = self.login(username, password)
        if not login_result.ok:
            return CourseDiscoveryResult(ok=False, error_code=login_result.error_code, login=login_result, courses=[])

        lister = LearningXLister(self.http)
        courses = lister.list_courses(term_ids=term_ids) if term_ids else lister.list_current_courses(self.current_term_candidates)
        summary: list[CourseSummary] = []
        for course in courses:
            course_id = course.get("id") or course.get("course_id")
            if course_id is None:
                continue
            modules = lister.list_course_modules(course_id)
            lectures = lister.extract_lecture_items(modules)
            summary.append(CourseSummary.from_course_and_modules(course, modules, lectures))
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
        cached = self._course_detail_cache.get(cache_key)
        if cached is not None:
            return cached
        login_result = self.login(username, password)
        if not login_result.ok:
            return CourseDetailResult(ok=False, error_code=login_result.error_code, course=None, lectures=[], content_items=[])
        lister = LearningXLister(self.http)
        modules = lister.list_course_modules(course_id)
        lectures = lister.extract_lecture_items(modules)
        content_items = lister.extract_course_content_items(modules)
        course = CourseSummary(
            id=course_id,
            name=course_name or "",
            module_count=len(modules),
            lecture_count=len(lectures),
            uncompleted_lecture_count=sum(1 for lecture in lectures if not lecture.completed),
        )
        return self._course_detail_cache.set(cache_key, CourseDetailResult(ok=True, error_code=None, course=course, lectures=lectures, content_items=content_items))

    def resolve_media(self, username: str, password: str, *, course_id: int | str, module_item_id: int | str) -> ResolvedMediaResult:
        login_result = self.login(username, password)
        if not login_result.ok:
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
        return self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id).result

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
        login_result = self.login(username, password)
        if not login_result.ok:
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

        resolved = self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id)
        result = resolved.result
        extension = resolved.extension or "mp4"
        plan = plan_download_path(
            output_dir=output_dir,
            course_name=course_name,
            lecture_title=result.title or "lecture",
            content_id=result.content_id or "content",
            extension=extension,
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
            download_to_plan(
                http=self.http,
                download_url=resolved.raw_download_url,
                plan=plan,
                referer=resolved.referer,
                max_attempts=MEDIA_DOWNLOAD_MAX_ATTEMPTS,
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
        login_result = self.login(username, password)
        if not login_result.ok:
            return NativeDownloadPlanResult(
                result=DownloadLectureResult(
                    ok=False,
                    error_code=login_result.error_code,
                    dry_run=True,
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
                ),
                native_download=None,
            )

        resolved = self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id)
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

    def official_viewer_plan(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        module_item_id: int | str,
    ) -> OfficialViewerPlanResult:
        login_result = self.login(username, password)
        if not login_result.ok:
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
        return self._resolve_official_viewer_authenticated(course_id=course_id, module_item_id=module_item_id).result

    def official_viewer_session_bridge_plan(
        self,
        username: str,
        password: str,
        *,
        course_id: int | str,
        module_item_id: int | str,
    ) -> OfficialViewerSessionBridgeResult:
        login_result = self.login(username, password)
        if not login_result.ok:
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
        resolved = self._resolve_official_viewer_authenticated(course_id=course_id, module_item_id=module_item_id).result
        cookies = _bridge_cookies(self.http.cookies)
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
                "http_trace": self.http.trace.to_dict() if hasattr(self.http, "trace") else None,
            },
        )

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
        login_result = self.login(username, password)
        if not login_result.ok:
            return BatchDownloadResult(
                ok=False,
                error_code=login_result.error_code,
                dry_run=dry_run,
                course_id=course_id,
                course_name=course_name,
                items=[],
            )

        lister = LearningXLister(self.http)
        modules = lister.list_course_modules(course_id)
        content_items = lister.extract_course_content_items(modules)
        selected_ids = {str(item_id) for item_id in module_item_ids} if module_item_ids else None
        selected_lectures = []
        for lecture in content_items:
            if not lecture.downloadable or lecture.module_item_id is None:
                continue
            if selected_ids is not None:
                if str(lecture.module_item_id) in selected_ids:
                    selected_lectures.append(lecture)
            elif include_completed or lecture.completed is not True:
                selected_lectures.append(lecture)

        total = len(selected_lectures)
        immediate_results: dict[int, BatchDownloadItemResult] = {}
        download_tasks: list[dict[str, Any]] = []

        for index, lecture in enumerate(selected_lectures, start=1):
            if should_cancel and should_cancel():
                break
            module_item_id = lecture.module_item_id
            assert module_item_id is not None
            if on_progress:
                on_progress({"stage": "resolving", "index": index, "total": total, "module_item_id": module_item_id, "title": lecture.title})
            try:
                resolved = self._resolve_media_authenticated(course_id=course_id, module_item_id=module_item_id)
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
                planned = {
                    "index": index,
                    "lecture": lecture,
                    "module_item_id": module_item_id,
                    "resolved": resolved,
                    "result": result,
                    "plan": plan,
                    "title": result.title or lecture.title,
                    "file_size": file_size,
                }
                if dry_run:
                    if on_progress:
                        on_progress({"stage": "planned", "index": index, "total": total, "module_item_id": module_item_id, "title": planned["title"], "output_path": str(plan.file_path), "file_exists": plan.file_exists, "total_bytes": result.download_size})
                    immediate_results[index] = BatchDownloadItemResult(
                        ok=True,
                        error_code=None,
                        dry_run=dry_run,
                        written=False,
                        course_id=course_id,
                        module_item_id=module_item_id,
                        title=planned["title"],
                        completed=lecture.completed,
                        attendance_item_id=result.attendance_item_id,
                        content_id=result.content_id,
                        download_url=result.download_url,
                        download_size=result.download_size,
                        output_path=str(plan.file_path),
                        file_size=file_size,
                        file_exists=plan.file_exists,
                        skipped=False,
                        conflict_strategy=conflict_strategy,
                    )
                elif plan.file_exists and conflict_strategy == 'skip':
                    if on_progress:
                        on_progress({"stage": "skipped", "index": index, "total": total, "module_item_id": module_item_id, "title": planned["title"], "output_path": str(plan.file_path), "file_size": file_size, "bytes_written": file_size, "total_bytes": file_size})
                    immediate_results[index] = BatchDownloadItemResult(
                        ok=True,
                        error_code=None,
                        dry_run=dry_run,
                        written=False,
                        course_id=course_id,
                        module_item_id=module_item_id,
                        title=planned["title"],
                        completed=lecture.completed,
                        attendance_item_id=result.attendance_item_id,
                        content_id=result.content_id,
                        download_url=result.download_url,
                        download_size=result.download_size,
                        output_path=str(plan.file_path),
                        file_size=file_size,
                        file_exists=plan.file_exists,
                        skipped=True,
                        conflict_strategy=conflict_strategy,
                    )
                else:
                    download_tasks.append(planned)
            except Exception as exc:
                if on_progress:
                    on_progress({"stage": "failed", "index": index, "total": total, "module_item_id": module_item_id, "title": lecture.title, "error_code": type(exc).__name__})
                immediate_results[index] = BatchDownloadItemResult(
                    ok=False,
                    error_code=type(exc).__name__,
                    dry_run=dry_run,
                    written=False,
                    course_id=course_id,
                    module_item_id=module_item_id,
                    title=lecture.title,
                    completed=lecture.completed,
                    attendance_item_id=None,
                    content_id=None,
                    download_url=None,
                    download_size=None,
                    output_path=None,
                    file_size=None,
                )

        def download_planned(planned: dict[str, Any]) -> BatchDownloadItemResult:
            index = planned["index"]
            lecture = planned["lecture"]
            module_item_id = planned["module_item_id"]
            resolved = planned["resolved"]
            result = planned["result"]
            plan = planned["plan"]
            title = planned["title"]
            if on_progress:
                on_progress({"stage": "downloading", "index": index, "total": total, "module_item_id": module_item_id, "title": title, "output_path": str(plan.file_path), "total_bytes": result.download_size})

            def relay_download_progress(event: dict[str, Any]) -> None:
                if on_progress:
                    on_progress({**event, "index": index, "total": total, "module_item_id": module_item_id, "title": title})

            download_to_plan(
                http=self._download_http_client(),
                download_url=resolved.raw_download_url,
                plan=plan,
                referer=resolved.referer,
                max_attempts=MEDIA_DOWNLOAD_MAX_ATTEMPTS,
                on_progress=relay_download_progress,
                should_cancel=should_cancel,
            )
            file_size = plan.file_path.stat().st_size
            if on_progress:
                on_progress({"stage": "done", "index": index, "total": total, "module_item_id": module_item_id, "title": title, "output_path": str(plan.file_path), "file_size": file_size, "bytes_written": file_size, "total_bytes": file_size})
            return BatchDownloadItemResult(
                ok=True,
                error_code=None,
                dry_run=dry_run,
                written=True,
                course_id=course_id,
                module_item_id=module_item_id,
                title=title,
                completed=lecture.completed,
                attendance_item_id=result.attendance_item_id,
                content_id=result.content_id,
                download_url=result.download_url,
                download_size=result.download_size,
                output_path=str(plan.file_path),
                file_size=file_size,
                file_exists=plan.file_exists,
                skipped=False,
                conflict_strategy=conflict_strategy,
            )

        completed_results = dict(immediate_results)
        if on_progress:
            on_progress({
                "stage": "batch_prepare_done",
                "total": total,
                "resolved_count": len(immediate_results) + len(download_tasks),
                "download_count": len(download_tasks),
            })
        if download_tasks:
            worker_count = max(1, min(max_parallel_downloads, len(download_tasks)))
            if on_progress:
                on_progress({
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
                    module_item_id = planned["module_item_id"]
                    if should_cancel and should_cancel():
                        continue
                    try:
                        completed_results[index] = future.result()
                    except Exception as exc:
                        if on_progress:
                            on_progress({"stage": "failed", "index": index, "total": total, "module_item_id": module_item_id, "title": planned.get("title") or lecture.title, "error_code": type(exc).__name__})
                        completed_results[index] = BatchDownloadItemResult(
                            ok=False,
                            error_code=type(exc).__name__,
                            dry_run=dry_run,
                            written=False,
                            course_id=course_id,
                            module_item_id=module_item_id,
                            title=planned.get("title") or lecture.title,
                            completed=lecture.completed,
                            attendance_item_id=None,
                            content_id=None,
                            download_url=None,
                            download_size=None,
                            output_path=str(planned["plan"].file_path) if planned.get("plan") else None,
                            file_size=None,
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

    def _resolve_official_viewer_authenticated(self, *, course_id: int | str, module_item_id: int | str) -> _ResolvedViewerInternal:
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
        media = parse_attendance_item_media(attendance_response.json())
        if not media.viewer_url:
            raise RuntimeError("attendance item does not expose official viewer_url")
        return _ResolvedViewerInternal(
            result=OfficialViewerPlanResult(
                ok=True,
                error_code=None,
                course_id=course_id,
                module_item_id=module_item_id,
                attendance_item_id=attendance_item_id,
                title=media.title,
                content_id=media.content_id,
                content_type=media.content_type,
                duration=media.duration,
                viewer_url=media.viewer_url,
            ),
            raw_viewer_url=media.viewer_url,
        )

    def _module_item_media_from_course_modules(self, *, course_id: int | str, module_item_id: int | str) -> AttendanceItemMedia | None:
        modules = LearningXLister(self.http).list_course_modules(course_id)
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
        media = parse_attendance_item_media(attendance_response.json())
        return self._resolve_commons_download(
            course_id=course_id,
            module_item_id=module_item_id,
            attendance_item_id=attendance_item_id,
            media=media,
            api_headers=api_headers,
            referer=form.action,
        )
