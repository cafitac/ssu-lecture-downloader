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
        "next_deadline_at": None,
        "next_deadline_is_late": False,
        "missed_lecture_count": 0,
    }


def test_lecture_item_accepts_missing_optional_duration_and_content_type():
    item = LectureItem(module_id=1, module_name="1주차", item_id=10, module_item_id=None, title="강의", completed=False)

    assert item.duration is None
    assert item.content_type is None


def test_course_summary_reports_the_next_open_deadline_and_missed_lectures():
    from datetime import datetime, timezone

    from ssu_lecture_downloader.models import CourseSummary, LectureItem

    def lecture(title, completed, due, late=None, lock=None):
        return LectureItem(module_id=1, module_name='m', item_id=title, title=title, completed=completed, due_at=due, late_at=late, lock_at=lock)

    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    lectures = [
        lecture('done', True, '2026-10-16T14:59:59Z'),
        lecture('late window', False, '2026-10-12T14:59:59Z', late='2026-10-19T14:59:59Z'),
        lecture('upcoming', False, '2026-10-22T14:59:59Z', lock='2026-10-22T14:59:59Z'),
        lecture('missed', False, '2026-10-01T14:59:59Z', late='2026-10-08T14:59:59Z'),
    ]
    summary = CourseSummary.from_lectures(id=1, name='c', module_count=1, lectures=lectures, now=now)

    assert summary.next_deadline_at == '2026-10-19T14:59:59Z'
    assert summary.next_deadline_is_late is True
    assert summary.missed_lecture_count == 1
    assert summary.uncompleted_lecture_count == 3
