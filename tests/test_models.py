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
        "upcoming_lecture_count": 0,
        "next_task_deadline_at": None,
        "overdue_task_count": 0,
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



def _now():
    from datetime import datetime, timezone

    return datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


def _video(title, *, completed=False, unlock=None, due=None, late=None, lock=None):
    return LectureItem(module_id=1, module_name='m', item_id=title, title=title, completed=completed, unlock_at=unlock, due_at=due, late_at=late, lock_at=lock)


def _task(title, kind='assignment', *, completed=False, unlock=None, due=None, lock=None, url=None):
    from ssu_lecture_downloader.models import CourseContentItem

    return CourseContentItem(
        module_id=1, module_name='m', item_id=title, module_item_id=title, title=title, kind=kind, completed=completed,
        unlock_at=unlock, due_at=due, lock_at=lock, url=url,
    )


def test_lectures_that_are_not_open_yet_are_upcoming_not_unwatched():
    lectures = [
        _video('opened', unlock='2026-10-12T00:00:00Z', due='2026-10-20T14:59:59Z'),
        _video('opens later', unlock='2026-10-19T00:00:00Z', due='2026-10-24T14:59:59Z'),
        _video('opens next week', unlock='2026-10-22T00:00:00Z', due='2026-10-29T14:59:59Z'),
        _video('no unlock time', due='2026-10-30T14:59:59Z'),
        _video('opened and watched', completed=True, unlock='2026-10-01T00:00:00Z'),
    ]

    summary = CourseSummary.from_lectures(id=1, name='c', module_count=1, lectures=lectures, now=_now())

    assert summary.lecture_count == 5
    assert summary.uncompleted_lecture_count == 2  # 'opened' + 'no unlock time'
    assert summary.upcoming_lecture_count == 2
    assert summary.next_deadline_at == '2026-10-20T14:59:59Z'  # not-yet-open deadlines are ignored


def test_not_yet_open_lectures_are_never_missed():
    lectures = [_video('future', unlock='2026-10-20T00:00:00Z', due='2026-10-01T00:00:00Z')]

    summary = CourseSummary.from_lectures(id=1, name='c', module_count=1, lectures=lectures, now=_now())

    assert summary.missed_lecture_count == 0
    assert summary.next_deadline_at is None
    assert summary.upcoming_lecture_count == 1


def test_course_summary_reports_task_deadlines_and_overdue_tasks():
    tasks = [
        _task('done', completed=True, due='2026-10-16T14:59:59Z'),
        _task('later', due='2026-10-30T14:59:59Z', lock='2026-11-01T14:59:59Z'),
        _task('soon quiz', 'quiz', due='2026-10-17T14:59:00Z'),
        _task('late window', due='2026-10-10T14:59:59Z', lock='2026-10-18T14:59:59Z'),
        _task('overdue', due='2026-10-01T14:59:59Z', lock='2026-10-08T14:59:59Z'),
        _task('overdue quiz', 'quiz', due='2026-10-05T14:59:00Z'),
        _task('no deadline'),
        _task('a board', 'board', due='2026-10-01T00:00:00Z'),
    ]

    summary = CourseSummary.from_lectures(id=1, name='c', module_count=1, lectures=[], tasks=tasks, now=_now())

    assert summary.next_task_deadline_at == '2026-10-17T14:59:00Z'
    assert summary.overdue_task_count == 2
    assert summary.next_deadline_at is None  # lecture fields keep their meaning
    assert summary.missed_lecture_count == 0


def test_deadline_rows_cover_open_late_upcoming_and_all_missed_items():
    from ssu_lecture_downloader.models import build_deadline_items, sort_deadline_rows

    items = [
        _task('open', due='2026-10-20T14:59:59Z', lock='2026-10-27T14:59:59Z', url='https://lms/a'),
        _task('late', due='2026-10-14T14:59:59Z', lock='2026-10-18T14:59:59Z'),
        _task('upcoming', unlock='2026-10-22T00:00:00Z', due='2026-10-25T14:59:59Z'),
        _task('missed recently', 'quiz', due='2026-10-10T14:59:00Z'),
        _task('missed long ago', due='2026-09-01T14:59:59Z'),
        _task('completed', completed=True, due='2026-10-20T14:59:59Z'),
        _task('no deadline'),
        _task('a page', 'page', due='2026-10-20T14:59:59Z'),
    ]
    rows = build_deadline_items(course_id=7, course_name='과목', content_items=items, now=_now())
    result = sort_deadline_rows(rows)

    assert [(item.title, item.state) for item in result] == [
        ('late', 'late'),
        ('open', 'open'),
        ('upcoming', 'upcoming'),
        ('missed recently', 'missed'),
        ('missed long ago', 'missed'),
    ]
    first = result[1].model_dump()
    assert first == {
        'course_id': 7, 'course_name': '과목', 'kind': 'assignment', 'title': 'open', 'module_item_id': 'open', 'url': 'https://lms/a',
        'unlock_at': None, 'due_at': '2026-10-20T14:59:59Z', 'late_at': None, 'lock_at': '2026-10-27T14:59:59Z', 'completed': False, 'duration': None, 'state': 'open',
    }


def test_deadline_rows_carry_the_video_length_but_never_for_tasks():
    from ssu_lecture_downloader.models import CourseContentItem, build_deadline_items

    video = CourseContentItem(
        module_id=1, module_name='m', item_id='v', module_item_id=11, title='1주차', kind='video', duration=1520.5,
        due_at='2026-10-20T14:59:59Z',
    )
    unknown_length = video.model_copy(update={'item_id': 'w', 'module_item_id': 12, 'duration': None})
    task = CourseContentItem(
        module_id=1, module_name='m', item_id='t', module_item_id=13, title='과제', kind='assignment', duration=99,
        due_at='2026-10-20T14:59:59Z',
    )

    rows = build_deadline_items(course_id=7, course_name='과목', content_items=[video, unknown_length, task], now=_now())

    assert [item.duration for _, item in rows] == [1520.5, None, None]
