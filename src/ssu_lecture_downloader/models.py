from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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

    @classmethod
    def from_course_and_modules(
        cls,
        course: dict[str, Any],
        modules: list[dict[str, Any]],
        lectures: list[LectureItem],
    ) -> "CourseSummary":
        course_id = course.get("id") or course.get("course_id")
        return cls(
            id=course_id,
            name=course.get("name") or course.get("course_name") or "",
            module_count=len(modules),
            lecture_count=len(lectures),
            uncompleted_lecture_count=sum(1 for lecture in lectures if not lecture.completed),
        )


class CourseDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    course: CourseSummary
    lectures: list[LectureItem]
    content_items: list[CourseContentItem] = Field(default_factory=list)
