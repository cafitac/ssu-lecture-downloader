from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict


class LectureItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    module_id: int | str | None
    module_name: str
    item_id: int | str | None
    module_item_id: int | str | None = None
    title: str
    completed: bool
    duration: int | float | None = None
    content_type: str | None = None
    unlock_at: str | None = None
    due_at: str | None = None
    late_at: str | None = None
    lock_at: str | None = None


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def lecture_deadline(lecture: LectureItem, now: datetime) -> tuple[str | None, bool]:
    """The next deadline still ahead for an unwatched lecture: (time, is_late_window).

    Attendance counts until `due_at`; after it, `late_at` (or `lock_at`) closes a late window.
    """
    if lecture.completed:
        return None, False
    due = _parse_time(lecture.due_at)
    if due is not None and due > now:
        return lecture.due_at, False
    for closing in (lecture.late_at, lecture.lock_at):
        parsed = _parse_time(closing)
        if parsed is not None and parsed > now and (due is None or parsed > due):
            return closing, due is not None
    return None, False


def lecture_missed(lecture: LectureItem, now: datetime) -> bool:
    """Unwatched and every attendance window has closed."""
    if lecture.completed:
        return False
    times = [_parse_time(value) for value in (lecture.due_at, lecture.late_at, lecture.lock_at)]
    known = [value for value in times if value is not None]
    return bool(known) and all(value <= now for value in known)


class CourseContentItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    module_id: int | str | None
    module_name: str
    week_position: int | str | None = None
    lesson_position: int | str | None = None
    item_id: int | str | None
    module_item_id: int | str | None = None
    title: str
    content_type: str | None = None
    inner_content_type: str | None = None
    kind: str
    content_id: str | None = None
    completed: bool | None = None
    attendance_status: str | None = None
    duration: int | float | None = None
    size_bytes: int | None = None
    file_name: str | None = None
    url: str | None = None
    published: bool | None = None
    unlock_at: str | None = None
    due_at: str | None = None
    lock_at: str | None = None
    late_at: str | None = None
    downloadable: bool = False
    resolvable: bool = False


class CourseSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int | str
    name: str
    module_count: int
    lecture_count: int
    uncompleted_lecture_count: int
    # Earliest deadline still ahead among unwatched lectures, and whether it only closes a late window.
    next_deadline_at: str | None = None
    next_deadline_is_late: bool = False
    # Unwatched lectures whose attendance windows have all closed.
    missed_lecture_count: int = 0

    @classmethod
    def from_lectures(
        cls,
        *,
        id: int | str,
        name: str,
        module_count: int,
        lectures: list[LectureItem],
        now: datetime | None = None,
    ) -> CourseSummary:
        now = now or datetime.now(timezone.utc)
        upcoming = [lecture_deadline(lecture, now) for lecture in lectures]
        upcoming = [(at, late) for at, late in upcoming if at is not None]
        next_at, next_late = min(upcoming, key=lambda pair: _parse_time(pair[0])) if upcoming else (None, False)
        return cls(
            id=id,
            name=name,
            module_count=module_count,
            lecture_count=len(lectures),
            uncompleted_lecture_count=sum(1 for lecture in lectures if not lecture.completed),
            next_deadline_at=next_at,
            next_deadline_is_late=next_late,
            missed_lecture_count=sum(1 for lecture in lectures if lecture_missed(lecture, now)),
        )

    @classmethod
    def from_course_and_modules(
        cls,
        course: dict[str, Any],
        modules: list[dict[str, Any]],
        lectures: list[LectureItem],
        now: datetime | None = None,
    ) -> CourseSummary:
        return cls.from_lectures(
            id=course.get("id") or course.get("course_id"),
            name=course.get("name") or course.get("course_name") or "",
            module_count=len(modules),
            lectures=lectures,
            now=now,
        )
