"""rusaint objects -> grades DTOs. Pure and duck-typed (it never imports rusaint).

Every value is type-checked. A mismatch raises `GradesMappingError` carrying the *field name only*,
never the offending value, so a parser break cannot echo page text into logs or error bodies.
Field meanings that the rusaint documentation leaves open (O3) are carried over unchanged and are
not interpreted here: `ClassGrade.grade_points` -> `credits`, `ClassGrade.rank` -> `grade`,
`ClassGrade.score` -> `score`, `U32Pair(first, second)` -> `{rank: first, total: second}`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from ssu_lecture_downloader.core.grades.models import (
    SEMESTER_NAMES,
    ClassGradeDto,
    ClassList,
    GradeSummaryDto,
    GradesOverview,
    RankDto,
    SemesterDto,
)


class GradesMappingError(Exception):
    """Unexpected shape from rusaint. `field` is a fixed field name, never a value."""

    def __init__(self, field: str) -> None:
        super().__init__(field)
        self.field = field

    def __str__(self) -> str:
        return self.field


def _number(obj: Any, field: str) -> float | None:
    value = getattr(obj, field, _MISSING)
    if value is _MISSING or value is None:
        raise GradesMappingError(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GradesMappingError(field)
    number = float(value)
    return number if math.isfinite(number) else None  # JSON cannot carry NaN/inf


def _integer(obj: Any, field: str) -> int:
    value = getattr(obj, field, _MISSING)
    if isinstance(value, bool) or not isinstance(value, int):
        raise GradesMappingError(field)
    return value


def _text(obj: Any, field: str) -> str:
    value = getattr(obj, field, _MISSING)
    if not isinstance(value, str):
        raise GradesMappingError(field)
    return value


def _flag(obj: Any, field: str) -> bool:
    value = getattr(obj, field, _MISSING)
    if not isinstance(value, bool):
        raise GradesMappingError(field)
    return value


def _semester_name(obj: Any, field: str = "semester") -> str:
    value = getattr(obj, field, _MISSING)
    name = getattr(value, "name", None)
    if name not in SEMESTER_NAMES:
        raise GradesMappingError(field)
    return name


def _rank(obj: Any, field: str) -> RankDto:
    pair = getattr(obj, field, _MISSING)
    first, second = getattr(pair, "first", _MISSING), getattr(pair, "second", _MISSING)
    for value in (first, second):
        if isinstance(value, bool) or not isinstance(value, int):
            raise GradesMappingError(field)
    return RankDto(rank=first, total=second)


def _score(obj: Any) -> str | None:
    """A number as a string ("88.5", "90"), "P", "F", or None when the cell is empty."""
    score = getattr(obj, "score", _MISSING)
    if score is _MISSING:
        raise GradesMappingError("score")
    try:
        if score.is_score():
            value = score[0]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise GradesMappingError("score")
            return format(float(value), "g")
        if score.is_pass():
            return "P"
        if score.is_failed():
            return "F"
        if score.is_empty():
            return None
    except GradesMappingError:
        raise
    except Exception:  # noqa: BLE001 - any other shape is a field-level mismatch, never echoed
        raise GradesMappingError("score") from None
    raise GradesMappingError("score")


def to_summary(summary: Any) -> GradeSummaryDto:
    return GradeSummaryDto(
        attempted_credits=_number(summary, "attempted_credits"),
        earned_credits=_number(summary, "earned_credits"),
        pf_earned_credits=_number(summary, "pf_earned_credits"),
        grade_points_sum=_number(summary, "grade_points_sum"),
        grade_points_average=_number(summary, "grade_points_average"),
        arithmetic_mean=_number(summary, "arithmetic_mean"),
    )


def to_semester(item: Any) -> SemesterDto:
    return SemesterDto(
        year=_integer(item, "year"),
        semester=_semester_name(item),
        attempted_credits=_number(item, "attempted_credits"),
        earned_credits=_number(item, "earned_credits"),
        pf_earned_credits=_number(item, "pf_earned_credits"),
        grade_points_average=_number(item, "grade_points_average"),
        grade_points_sum=_number(item, "grade_points_sum"),
        arithmetic_mean=_number(item, "arithmetic_mean"),
        semester_rank=_rank(item, "semester_rank"),
        general_rank=_rank(item, "general_rank"),
        academic_probation=_flag(item, "academic_probation"),
        consult=_flag(item, "consult"),
        flunked=_flag(item, "flunked"),
    )


_SEMESTER_ORDER = {name: index for index, name in enumerate(SEMESTER_NAMES)}


def to_overview(semesters: Iterable[Any], recorded: Any, certificated: Any, *, fetched_at: str = "") -> GradesOverview:
    mapped = sorted(
        (to_semester(item) for item in semesters),
        key=lambda item: (item.year, _SEMESTER_ORDER[item.semester]),
        reverse=True,  # newest first
    )
    return GradesOverview(
        fetched_at=fetched_at,
        recorded_summary=to_summary(recorded),
        certificated_summary=to_summary(certificated),
        semesters=tuple(mapped),
    )


def to_class(item: Any) -> ClassGradeDto:
    return ClassGradeDto(
        code=_text(item, "code"),
        class_name=_text(item, "class_name"),
        credits=_number(item, "grade_points"),
        score=_score(item),
        grade=_text(item, "rank"),
        professor=_text(item, "professor"),
    )


def to_classes(items: Iterable[Any], *, year: int, semester: str) -> ClassList:
    return ClassList(year=year, semester=semester, classes=tuple(to_class(item) for item in items))


_MISSING = object()
