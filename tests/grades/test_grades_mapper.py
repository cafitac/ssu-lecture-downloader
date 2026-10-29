from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from grades_fakes import SENTINEL_CLASS

from ssu_lecture_downloader.core.grades import mapper
from ssu_lecture_downloader.core.grades.mapper import GradesMappingError


class Score:
    """Duck type of rusaint ClassScore: SCORE(x) / PASS / FAILED / EMPTY."""

    def __init__(self, kind, value=None):
        self.kind, self.value = kind, value

    def is_score(self):
        return self.kind == "score"

    def is_pass(self):
        return self.kind == "pass"

    def is_failed(self):
        return self.kind == "failed"

    def is_empty(self):
        return self.kind == "empty"

    def __getitem__(self, index):
        return self.value


def sem(name):
    return SimpleNamespace(name=name)


def pair(first, second):
    return SimpleNamespace(first=first, second=second)


def summary_obj(**overrides):
    base = dict(attempted_credits=60.0, earned_credits=57.0, pf_earned_credits=3.0, grade_points_sum=210.5, grade_points_average=3.5, arithmetic_mean=88.0)
    base.update(overrides)
    return SimpleNamespace(**base)


def semester_obj(year, name, **overrides):
    base = dict(
        year=year,
        semester=sem(name),
        attempted_credits=18.0,
        earned_credits=18.0,
        pf_earned_credits=0.0,
        grade_points_average=3.5,
        grade_points_sum=63.0,
        arithmetic_mean=87.0,
        semester_rank=pair(12, 140),
        general_rank=pair(30, 400),
        academic_probation=False,
        consult=False,
        flunked=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def class_obj(index, score=None, **overrides):
    base = dict(
        year=2025,
        semester=sem("ONE"),
        code=f"ZZ{index:04d}",
        class_name=f"{SENTINEL_CLASS}-{index}",
        grade_points=3.0,
        score=score or Score("score", 90.0),
        rank="A+",
        professor="invented professor",
        detail=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_overview_maps_three_semesters_newest_first_with_both_summaries():
    semesters = [semester_obj(2024, "TWO"), semester_obj(2025, "ONE"), semester_obj(2025, "TWO")]
    overview = mapper.to_overview(semesters, summary_obj(), summary_obj(earned_credits=50.0), fetched_at="2030-01-01T00:00:00Z")
    assert [(s.year, s.semester) for s in overview.semesters] == [(2025, "TWO"), (2025, "ONE"), (2024, "TWO")]
    assert overview.recorded_summary.earned_credits == 57.0
    assert overview.certificated_summary.earned_credits == 50.0
    data = overview.to_dict()
    assert data["semesters"][0]["semester_rank"] == {"rank": 12, "total": 140}
    assert data["semesters"][0]["academic_probation"] is False
    assert set(data["recorded_summary"]) == {"attempted_credits", "earned_credits", "pf_earned_credits", "grade_points_sum", "grade_points_average", "arithmetic_mean"}


def test_nine_classes_per_semester_keep_fields_without_interpretation():
    items = [class_obj(i) for i in range(9)]
    result = mapper.to_classes(items, year=2025, semester="ONE")
    assert len(result.classes) == 9
    first = result.to_dict()["classes"][0]
    assert first == {
        "code": "ZZ0000",
        "class_name": f"{SENTINEL_CLASS}-0",
        "credits": 3.0,  # rusaint ClassGrade.grade_points, unverified (O3)
        "score": "90",
        "grade": "A+",  # rusaint ClassGrade.rank, unverified (O3)
        "professor": "invented professor",
    }


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (Score("score", 88.5), "88.5"),
        (Score("score", 100), "100"),
        (Score("pass"), "P"),
        (Score("failed"), "F"),
        (Score("empty"), None),
    ],
)
def test_score_is_a_number_string_p_f_or_none(score, expected):
    assert mapper.to_class(class_obj(1, score=score)).score == expected


def test_non_finite_numbers_become_none_so_the_json_response_stays_valid():
    result = mapper.to_summary(summary_obj(arithmetic_mean=math.nan, grade_points_average=math.inf))
    assert result.arithmetic_mean is None and result.grade_points_average is None


@pytest.mark.parametrize(
    ("build", "field"),
    [
        (lambda: mapper.to_summary(summary_obj(earned_credits="ZZ-LEAK-VALUE")), "earned_credits"),
        (lambda: mapper.to_summary(summary_obj(earned_credits=True)), "earned_credits"),
        (lambda: mapper.to_summary(SimpleNamespace()), "attempted_credits"),
        (lambda: mapper.to_semester(semester_obj(2025, "AUTUMN")), "semester"),
        (lambda: mapper.to_semester(semester_obj("ZZ-LEAK-VALUE", "ONE")), "year"),
        (lambda: mapper.to_semester(semester_obj(2025, "ONE", semester_rank=pair("ZZ-LEAK-VALUE", 1))), "semester_rank"),
        (lambda: mapper.to_semester(semester_obj(2025, "ONE", flunked="ZZ-LEAK-VALUE")), "flunked"),
        (lambda: mapper.to_class(class_obj(1, rank=5)), "rank"),
        (lambda: mapper.to_class(class_obj(1, score=Score("score", "ZZ-LEAK-VALUE"))), "score"),
        (lambda: mapper.to_class(class_obj(1, score=object())), "score"),
        (lambda: mapper.to_class(class_obj(1, class_name=None)), "class_name"),
    ],
)
def test_a_type_mismatch_names_the_field_and_never_echoes_the_value(build, field):
    with pytest.raises(GradesMappingError) as raised:
        build()
    assert raised.value.field == field
    assert str(raised.value) == field
    assert "ZZ-LEAK-VALUE" not in repr(raised.value) + str(raised.value.args)


def test_the_mapper_works_on_real_rusaint_types():
    rusaint = pytest.importorskip("rusaint")
    real_semester = rusaint.SemesterGrade(
        year=2025,
        semester=rusaint.SemesterType.ONE,
        attempted_credits=18.0,
        earned_credits=18.0,
        pf_earned_credits=0.0,
        grade_points_average=3.5,
        grade_points_sum=63.0,
        arithmetic_mean=87.0,
        semester_rank=rusaint.UnsignedIntPair(first=12, second=140),
        general_rank=rusaint.UnsignedIntPair(first=30, second=400),
        academic_probation=False,
        consult=False,
        flunked=False,
    )
    real_summary = rusaint.GradeSummary(
        attempted_credits=60.0, earned_credits=57.0, grade_points_sum=210.5, grade_points_average=3.5, arithmetic_mean=88.0, pf_earned_credits=3.0
    )
    overview = mapper.to_overview([real_semester], real_summary, real_summary)
    assert overview.semesters[0].semester == "ONE"
    assert overview.semesters[0].semester_rank.total == 140

    def real_class(score):
        return rusaint.ClassGrade(
            year=2025,
            semester=rusaint.SemesterType.ONE,
            code="ZZ0001",
            class_name="invented",
            grade_points=3.0,
            score=score,
            rank="A+",
            professor="invented",
            detail=None,
        )

    scores = [rusaint.ClassScore.SCORE(88.5), rusaint.ClassScore.PASS(), rusaint.ClassScore.FAILED(), rusaint.ClassScore.EMPTY()]
    assert [mapper.to_class(real_class(s)).score for s in scores] == ["88.5", "P", "F", None]
