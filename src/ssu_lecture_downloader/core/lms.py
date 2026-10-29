from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ssu_lecture_downloader.core.canvas_files import wiki_page_ref
from ssu_lecture_downloader.core.models import CourseContentItem, LectureItem

COURSES_API_URL = "https://canvas.ssu.ac.kr/learningx/api/v1/learn_activities/courses"
MODULES_API_URL_TEMPLATE = "https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/modules"
LEARNINGX_DASHBOARD_URL = "https://canvas.ssu.ac.kr/learningx/dashboard"
DEFAULT_TERM_ID_CANDIDATES = list(range(35, 61))


def learningx_api_headers(cookies: Any, referer: str = LEARNINGX_DASHBOARD_URL) -> dict[str, str]:
    """Bearer headers for the LearningX JSON API (the single place that reads `xn_api_token`)."""
    token = cookies.get("xn_api_token")
    if not token:
        raise ValueError("xn_api_token cookie is required for LearningX API")
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Referer": referer,
    }


def latest_current_term(courses_by_term: Mapping[int, list[dict[str, Any]] | None], term_order: Iterable[int]) -> int | None:
    """The latest term (in `term_order`) that has an unended course; failed terms are None."""
    latest: int | None = None
    for term_id in term_order:
        courses = courses_by_term.get(term_id)
        if courses and any(course.get("ended") is False for course in courses):
            latest = term_id
    return latest


def latest_current_courses(courses_by_term: Mapping[int, list[dict[str, Any]] | None], term_order: Iterable[int]) -> list[dict[str, Any]]:
    """Unended courses of the latest term, then unended courses of other terms.

    Non-credit programs (비교과, 비정규 such as yearly mandatory trainings) sit in their own
    yearly term, so "this term" alone would hide them while they are still open.
    """
    order = list(term_order)
    latest = latest_current_term(courses_by_term, order)
    if latest is None:
        return []
    picked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for term_id in [latest, *reversed([term for term in order if term != latest])]:
        for course in courses_by_term.get(term_id) or []:
            course_id = str(course.get("id") or course.get("course_id"))
            if course.get("ended") is False and course_id not in seen:
                seen.add(course_id)
                picked.append(course)
    return picked


class LearningXLister:
    def __init__(self, http) -> None:
        self.http = http

    def _api_headers(self) -> dict[str, str]:
        return learningx_api_headers(self.http.cookies)

    def list_courses(self, term_ids: list[int] | None = None) -> list[dict[str, Any]]:
        params = {"term_ids[]": term_ids} if term_ids else None
        response = self.http.get("list_courses", COURSES_API_URL, headers=self._api_headers(), params=params)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict) and isinstance(payload.get("courses"), list):
            return payload["courses"]
        if isinstance(payload, list):
            return payload
        return []

    def list_current_courses(self, term_id_candidates: list[int] | None = None) -> list[dict[str, Any]]:
        candidates = term_id_candidates or DEFAULT_TERM_ID_CANDIDATES
        courses_by_term: dict[int, list[dict[str, Any]] | None] = {}
        for term_id in candidates:
            try:
                courses_by_term[term_id] = self.list_courses(term_ids=[term_id])
            except Exception:  # noqa: BLE001 - a term that fails to load is simply skipped
                courses_by_term[term_id] = None
        return latest_current_courses(courses_by_term, candidates)

    def list_course_modules(self, course_id: int | str) -> list[dict[str, Any]]:
        response = self.http.get(
            "list_course_modules",
            MODULES_API_URL_TEMPLATE.format(course_id=course_id),
            headers=self._api_headers(),
            params={"include_detail": "true"},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []

    def extract_lecture_items(self, modules: list[dict[str, Any]]) -> list[LectureItem]:
        lectures: list[LectureItem] = []
        for module in modules:
            module_id = module.get("id") or module.get("module_id")
            module_name = module.get("name") or module.get("title") or ""
            for item in module.get("module_items", []) or []:
                if not isinstance(item, dict) or not is_video_item(item):
                    continue
                content_data = item.get("content_data") or {}
                item_content_data = content_data.get("item_content_data") or {}
                lectures.append(
                    LectureItem(
                        module_id=module_id,
                        module_name=module_name,
                        item_id=item.get("id"),
                        module_item_id=item.get("module_item_id"),
                        title=item.get("title", ""),
                        completed=bool(item.get("completed", False)),
                        duration=item_content_data.get("duration"),
                        content_type=item_content_data.get("content_type"),
                        unlock_at=content_data.get("unlock_at") or module.get("unlock_at"),
                        due_at=content_data.get("due_at"),
                        late_at=content_data.get("late_at"),
                        lock_at=content_data.get("lock_at"),
                    )
                )
        return lectures

    def extract_course_content_items(self, modules: list[dict[str, Any]]) -> list[CourseContentItem]:
        content_items: list[CourseContentItem] = []
        for module in modules:
            module_id = module.get("id") or module.get("module_id")
            module_name = module.get("name") or module.get("title") or ""
            week_position = module.get("week_position")
            for item in module.get("module_items", []) or []:
                if not isinstance(item, dict):
                    continue
                content_data = item.get("content_data") if isinstance(item.get("content_data"), dict) else {}
                item_content_data = content_data.get("item_content_data") if isinstance(content_data.get("item_content_data"), dict) else {}
                content_type = item.get("content_type")
                inner_content_type = item_content_data.get("content_type")
                kind = normalize_content_kind(
                    content_type=content_type,
                    inner_content_type=inner_content_type,
                    duration=item_content_data.get("duration"),
                )
                has_download_metadata = bool(
                    item_content_data.get("content_id")
                    or item_content_data.get("total_file_size")
                    or item_content_data.get("file_size")
                    or item_content_data.get("size")
                    or item_content_data.get("file_name")
                )
                can_resolve_download = (
                    kind in {"video", "material"}
                    and item.get("module_item_id") is not None
                    and has_download_metadata
                )
                content_items.append(
                    CourseContentItem(
                        module_id=module_id,
                        module_name=module_name,
                        week_position=week_position,
                        lesson_position=content_data.get("lesson_position"),
                        item_id=item.get("id") or content_data.get("item_id"),
                        module_item_id=item.get("module_item_id"),
                        title=item.get("title") or content_data.get("title") or item_content_data.get("title") or "",
                        content_type=content_type,
                        inner_content_type=inner_content_type,
                        kind=kind,
                        content_id=str(item_content_data.get("content_id")) if item_content_data.get("content_id") else None,
                        resource_id=str(item.get("content_id")) if item.get("content_id") not in (None, "") else None,
                        page_ref=wiki_page_ref(
                            url=item.get("url") or content_data.get("url"),
                            page_url=item.get("page_url") or content_data.get("page_url"),
                            resource_id=item.get("content_id"),
                        )
                        if kind == "page"
                        else None,
                        completed=item.get("completed") if "completed" in item else None,
                        attendance_status=item.get("attendance_status"),
                        duration=item_content_data.get("duration"),
                        size_bytes=_optional_int(
                            item_content_data.get("total_file_size")
                            or item_content_data.get("file_size")
                            or item_content_data.get("size")
                        ),
                        file_name=item_content_data.get("file_name"),
                        url=item.get("url") or content_data.get("url") or item_content_data.get("view_url"),
                        published=item.get("published") if "published" in item else None,
                        unlock_at=content_data.get("unlock_at") or module.get("unlock_at"),
                        due_at=content_data.get("due_at"),
                        lock_at=content_data.get("lock_at"),
                        late_at=content_data.get("late_at"),
                        downloadable=can_resolve_download,
                        resolvable=str(content_type or "").lower() == "attendance_item"
                        and can_resolve_download,
                    )
                )
        return content_items


def is_video_item(item: dict[str, Any]) -> bool:
    if str(item.get("content_type", "")).lower() != "attendance_item":
        return False
    content_data = item.get("content_data")
    if not isinstance(content_data, dict):
        return False
    item_content_data = content_data.get("item_content_data")
    if not isinstance(item_content_data, dict):
        return False
    content_type = str(item_content_data.get("content_type", "")).lower()
    return content_type in {"everlec", "mp4", "video", "movie"} or bool(item_content_data.get("duration"))


def normalize_content_kind(*, content_type: Any, inner_content_type: Any, duration: Any = None) -> str:
    top = str(content_type or "").lower()
    inner = str(inner_content_type or "").lower()
    if top == "attendance_item" and (inner in {"everlec", "mp4", "video", "movie"} or bool(duration)):
        return "video"
    if top == "attendance_item" and inner in {"pdf", "ppt", "pptx", "doc", "docx", "hwp", "zip"}:
        return "material"
    if top == "assignment":
        return "assignment"
    if top == "quiz":
        return "quiz"
    if top == "board":
        return "board"
    if top == "wiki_page":
        return "page"
    if top == "sub_header":
        return "section"
    return "unknown"


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
