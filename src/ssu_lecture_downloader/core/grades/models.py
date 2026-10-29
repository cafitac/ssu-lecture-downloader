"""Plain data shapes of the grades feature (memory only; `to_dict` is the HTTP response shape)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The only error codes this feature produces. They are deliberately NOT LMS authentication codes
# (service/api_errors.AUTHENTICATION_ERROR_CODES), so a grades failure never signs the user out.
GRADES_CONSENT_REQUIRED = "grades_consent_required"
GRADES_LOGIN_REJECTED = "grades_login_rejected"
GRADES_UNAVAILABLE = "grades_unavailable"
# Reused from the existing credentials contract when no LMS credentials are in memory.
MISSING_CREDENTIALS = "missing_credentials"

GRADES_ERROR_CODES = frozenset({GRADES_CONSENT_REQUIRED, GRADES_LOGIN_REJECTED, GRADES_UNAVAILABLE})
SEMESTER_NAMES = ("ONE", "SUMMER", "TWO", "WINTER")


class GradesError(Exception):
    """A failure carried by its code alone: no message, no cause, so no page text or value can leak."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True)
class GradeSummaryDto:
    attempted_credits: float | None
    earned_credits: float | None
    pf_earned_credits: float | None
    grade_points_sum: float | None
    grade_points_average: float | None
    arithmetic_mean: float | None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class RankDto:
    """rusaint `U32Pair(first, second)` read as rank `first` out of `second` (order unverified, O3)."""

    rank: int | None
    total: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"rank": self.rank, "total": self.total}


@dataclass(frozen=True)
class SemesterDto:
    year: int
    semester: str  # ONE | SUMMER | TWO | WINTER
    attempted_credits: float | None
    earned_credits: float | None
    pf_earned_credits: float | None
    grade_points_average: float | None
    grade_points_sum: float | None
    arithmetic_mean: float | None
    semester_rank: RankDto
    general_rank: RankDto
    academic_probation: bool
    consult: bool
    flunked: bool

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["semester_rank"] = self.semester_rank.to_dict()
        data["general_rank"] = self.general_rank.to_dict()
        return data


@dataclass(frozen=True)
class GradesOverview:
    fetched_at: str  # ISO-8601 UTC
    recorded_summary: GradeSummaryDto
    certificated_summary: GradeSummaryDto
    semesters: tuple[SemesterDto, ...]  # newest first

    def to_dict(self) -> dict[str, Any]:
        return {
            "fetched_at": self.fetched_at,
            "recorded_summary": self.recorded_summary.to_dict(),
            "certificated_summary": self.certificated_summary.to_dict(),
            "semesters": [semester.to_dict() for semester in self.semesters],
        }


@dataclass(frozen=True)
class ClassGradeDto:
    """One class row. The field meanings are carried over from rusaint without interpretation (O3):

    credits <- ClassGrade.grade_points, grade <- ClassGrade.rank, score <- ClassGrade.score
    (a number as a string, "P", "F", or None when empty).
    """

    code: str
    class_name: str
    credits: float | None
    score: str | None
    grade: str
    professor: str

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class ClassList:
    year: int
    semester: str
    classes: tuple[ClassGradeDto, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"year": self.year, "semester": self.semester, "classes": [item.to_dict() for item in self.classes]}


@dataclass(frozen=True)
class GradesConsent:
    consent_version: int
    granted_at: str  # ISO-8601 UTC
    schema: int = 1


@dataclass(frozen=True)
class GradesStatus:
    consent: GradesConsent | None
    available: bool
    current_consent_version: int
    fetched_at: str | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        consent = self.consent
        return {
            "consent": {
                "granted": consent is not None,
                "consent_version": consent.consent_version if consent else None,
                "granted_at": consent.granted_at if consent else None,
            },
            "available": self.available,
            "current_consent_version": self.current_consent_version,
            "fetched_at": self.fetched_at,
        }
