from ssu_lecture_downloader.models import CourseSummary, LectureItem


def test_course_summary_counts_uncompleted_lectures_from_typed_items():
    lectures = [
        LectureItem(module_id=1, module_name="1주차", item_id=10, module_item_id=100, title="완료", completed=True, duration=60, content_type="mp4"),
        LectureItem(module_id=1, module_name="1주차", item_id=11, module_item_id=101, title="미완료", completed=False, duration=120, content_type="everlec"),
    ]

    summary = CourseSummary.from_course_and_modules(
        course={"id": 123, "name": "테스트 과목"},
        modules=[{"id": 1, "name": "1주차"}],
        lectures=lectures,
    )

    assert summary.model_dump() == {
        "id": 123,
        "name": "테스트 과목",
        "module_count": 1,
        "lecture_count": 2,
        "uncompleted_lecture_count": 1,
    }


def test_lecture_item_accepts_missing_optional_duration_and_content_type():
    item = LectureItem(module_id=1, module_name="1주차", item_id=10, module_item_id=None, title="강의", completed=False)

    assert item.duration is None
    assert item.content_type is None
