from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Protocol

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


class _Timed(Protocol):
    completed: bool | None
    unlock_at: str | None
    due_at: str | None
    late_at: str | None
    lock_at: str | None


def lecture_opened(item: _Timed, now: datetime) -> bool:
    """Whether the item's week has opened (no unlock time, or it has passed)."""
    unlock = _parse_time(item.unlock_at)
    return unlock is None or unlock <= now


def lecture_deadline(lecture: _Timed, now: datetime) -> tuple[str | None, bool]:
    """The next deadline still ahead for an incomplete item: (time, is_late_window).

    Attendance (or submission) counts until `due_at`; after it, `late_at` (or `lock_at`)
    closes a late window. Works for lectures and for assignments/quizzes alike.
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


def lecture_closed_at(item: _Timed) -> str | None:
    """The latest known closing time (due, late or lock), or None when the item has none."""
    known = [(parsed, value) for value in (item.due_at, item.late_at, item.lock_at) if (parsed := _parse_time(value)) is not None]
    return max(known, key=lambda pair: pair[0])[1] if known else None


def lecture_missed(lecture: _Timed, now: datetime) -> bool:
    """Incomplete and every window has closed."""
    if lecture.completed:
        return False
    times = [_parse_time(value) for value in (lecture.due_at, lecture.late_at, lecture.lock_at)]
    known = [value for value in times if value is not None]
    return bool(known) and all(value <= now for value in known)


TASK_KINDS = frozenset({"assignment", "quiz"})


def is_task(item: CourseContentItem) -> bool:
    return item.kind in TASK_KINDS


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
    # The LMS id of the page/board/assignment behind the module item (top-level `content_id`).
    resource_id: str | None = None
    # Wiki pages only: what the Canvas pages API takes to find the page (slug or numeric id).
    page_ref: str | None = None
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
    # Unwatched lectures whose week has opened; lectures that are not open yet are `upcoming_lecture_count`.
    uncompleted_lecture_count: int
    # Earliest deadline still ahead among opened, unwatched lectures, and whether it only closes a late window.
    next_deadline_at: str | None = None
    next_deadline_is_late: bool = False
    # Opened, unwatched lectures whose attendance windows have all closed.
    missed_lecture_count: int = 0
    upcoming_lecture_count: int = 0
    # Earliest due/lock still ahead among incomplete assignments and quizzes.
    next_task_deadline_at: str | None = None
    # Incomplete assignments/quizzes whose due and lock times have all passed.
    overdue_task_count: int = 0

    @classmethod
    def from_lectures(
        cls,
        *,
        id: int | str,
        name: str,
        module_count: int,
        lectures: list[LectureItem],
        tasks: list[CourseContentItem] | None = None,
        now: datetime | None = None,
    ) -> CourseSummary:
        now = now or datetime.now(timezone.utc)
        opened = [lecture for lecture in lectures if lecture_opened(lecture, now)]
        upcoming = [lecture_deadline(lecture, now) for lecture in opened]
        upcoming = [(at, late) for at, late in upcoming if at is not None]
        next_at, next_late = min(upcoming, key=lambda pair: _parse_time(pair[0])) if upcoming else (None, False)
        task_items = [task for task in tasks or [] if is_task(task)]
        task_deadlines = [at for at, _ in (lecture_deadline(task, now) for task in task_items) if at is not None]
        return cls(
            id=id,
            name=name,
            module_count=module_count,
            lecture_count=len(lectures),
            uncompleted_lecture_count=sum(1 for lecture in opened if not lecture.completed),
            next_deadline_at=next_at,
            next_deadline_is_late=next_late,
            missed_lecture_count=sum(1 for lecture in opened if lecture_missed(lecture, now)),
            upcoming_lecture_count=sum(1 for lecture in lectures if not lecture.completed and not lecture_opened(lecture, now)),
            next_task_deadline_at=min(task_deadlines, key=_parse_time) if task_deadlines else None,
            overdue_task_count=sum(1 for task in task_items if lecture_missed(task, now)),
        )

    @classmethod
    def from_course_and_modules(
        cls,
        course: dict[str, Any],
        modules: list[dict[str, Any]],
        lectures: list[LectureItem],
        now: datetime | None = None,
        tasks: list[CourseContentItem] | None = None,
    ) -> CourseSummary:
        return cls.from_lectures(
            id=course.get("id") or course.get("course_id"),
            name=course.get("name") or course.get("course_name") or "",
            module_count=len(modules),
            lectures=lectures,
            tasks=tasks,
            now=now,
        )


DEADLINE_KINDS = frozenset({"video", "assignment", "quiz"})
# Missed items older than this are no longer worth listing.


class DeadlineItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    course_id: int | str
    course_name: str
    kind: str
    title: str
    module_item_id: int | str | None = None
    url: str | None = None
    unlock_at: str | None = None
    due_at: str | None = None
    late_at: str | None = None
    lock_at: str | None = None
    completed: bool = False
    # open: window is open; late: only a late window is left; upcoming: not open yet; missed: all windows closed.
    state: str


def build_deadline_items(
    *,
    course_id: int | str,
    course_name: str,
    content_items: list[CourseContentItem],
    now: datetime,
) -> list[tuple[datetime, DeadlineItem]]:
    """Deadline rows of one course as (sort time, item): incomplete videos/assignments/quizzes only.

    Rows whose window is open, late or not open yet sort by their next deadline; missed
    rows (any this term, so they match the sidebar's missed count) sort by when they closed. Items without any
    deadline are left out.
    """
    rows: list[tuple[datetime, DeadlineItem]] = []
    for item in content_items:
        if item.kind not in DEADLINE_KINDS or item.completed:
            continue
        if lecture_opened(item, now) and lecture_missed(item, now):
            closed = _parse_time(lecture_closed_at(item))
            if closed is None:
                continue
            state, sort_time = "missed", closed
        else:
            at, is_late = lecture_deadline(item, now)
            sort_time = _parse_time(at)
            if sort_time is None:
                continue
            state = "upcoming" if not lecture_opened(item, now) else "late" if is_late else "open"
        rows.append(
            (
                sort_time,
                DeadlineItem(
                    course_id=course_id,
                    course_name=course_name,
                    kind=item.kind,
                    title=item.title,
                    module_item_id=item.module_item_id,
                    url=item.url,
                    unlock_at=item.unlock_at,
                    due_at=item.due_at,
                    late_at=item.late_at,
                    lock_at=item.lock_at,
                    completed=False,
                    state=state,
                ),
            )
        )
    return rows


def sort_deadline_rows(rows: list[tuple[datetime, DeadlineItem]]) -> list[DeadlineItem]:
    """Items still ahead by nearest deadline first, then missed ones, most recently closed first."""
    ahead = sorted((row for row in rows if row[1].state != "missed"), key=lambda row: row[0])
    missed = sorted((row for row in rows if row[1].state == "missed"), key=lambda row: row[0], reverse=True)
    return [item for _, item in ahead + missed]
