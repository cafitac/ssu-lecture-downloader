from pathlib import Path

from ssu_lecture_downloader.core.download import (
    DownloadPlan,
    download_to_plan,
    plan_download_path,
    safe_filename,
)


def test_safe_filename_removes_path_separators_and_control_characters():
    assert safe_filename('03-1.데이터/종류:구성\n*.mp4') == '03-1.데이터_종류_구성_.mp4'


def test_safe_filename_uses_fallback_when_title_has_no_safe_characters():
    assert safe_filename(' /\n\t ') == 'lecture'


def test_plan_download_path_places_the_lecture_in_its_own_folder_below_the_week(tmp_path: Path):
    plan = plan_download_path(
        output_dir=tmp_path,
        course_name='데이터사이언스/입문',
        lecture_title='03-1.데이터의 종류와 구성',
        content_id='66e3a7823e883',
        extension='mp4',
        week_position=3,
    )

    lecture_dir = tmp_path / '데이터사이언스_입문' / '03주차' / '03-1.데이터의 종류와 구성'
    assert plan == DownloadPlan(output_dir=tmp_path, course_dir=lecture_dir, file_path=lecture_dir / '03-1.데이터의 종류와 구성.mp4')
    assert '66e3a7823e883' not in str(plan.file_path)


def test_plan_download_path_without_a_week_uses_the_module_name_then_the_catch_all_folder(tmp_path: Path):
    named = plan_download_path(output_dir=tmp_path, course_name='c', lecture_title='강의 1', content_id='a', extension='mp4', module_name='오리엔테이션')
    unnamed = plan_download_path(output_dir=tmp_path, course_name='c', lecture_title='강의 1', content_id='b', extension='mp4')

    assert named.file_path == tmp_path / 'c' / '오리엔테이션' / '강의 1' / '강의 1.mp4'
    assert unnamed.file_path == tmp_path / 'c' / '기타' / '강의 1' / '강의 1.mp4'


def test_plan_download_path_normalizes_commons_videoish_extensions_to_mp4(tmp_path: Path):
    for extension in ('readystream', 'everlec', 'movie', 'video', 'zoom'):
        plan = plan_download_path(
            output_dir=tmp_path,
            course_name='국제상무론',
            lecture_title='계약의 기본조건 2',
            content_id='63b26d5c58048',
            extension=extension,
        )

        assert plan.file_path == tmp_path / '국제상무론' / '기타' / '계약의 기본조건 2' / '계약의 기본조건 2.mp4'


def test_plan_download_path_uses_numbered_suffix_next_to_an_existing_copy(tmp_path: Path):
    first = plan_download_path(output_dir=tmp_path, course_name='course', lecture_title='lecture', content_id='abc', extension='mp4')
    first.library.prepare()
    first.file_path.write_text('already here')

    again = plan_download_path(output_dir=tmp_path, course_name='course', lecture_title='lecture', content_id='abc', extension='mp4')

    assert again.course_dir == first.course_dir  # same lecture folder: the copy is named `<stem> (2).mp4` inside it
    assert again.file_path == first.course_dir / 'lecture (2).mp4'


def test_plan_download_path_can_surface_existing_file_for_overwrite_or_skip(tmp_path: Path):
    first = plan_download_path(output_dir=tmp_path, course_name='course', lecture_title='lecture', content_id='abc', extension='mp4')
    first.library.prepare()
    first.file_path.write_text('already here')

    overwrite = plan_download_path(output_dir=tmp_path, course_name='course', lecture_title='lecture', content_id='abc', extension='mp4', conflict_strategy='overwrite')
    skip = plan_download_path(output_dir=tmp_path, course_name='course', lecture_title='lecture', content_id='abc', extension='mp4', conflict_strategy='skip')

    for plan in (overwrite, skip):
        assert plan.file_path == first.file_path
        assert plan.file_exists is True
    assert overwrite.conflict_strategy == 'overwrite'


def test_overwrite_writes_the_new_layout_and_never_into_an_old_flat_file(tmp_path: Path):
    course_dir = tmp_path / 'course'
    course_dir.mkdir()
    old = course_dir / 'lecture [abc].mp4'
    old.write_text('old bytes')

    plan = plan_download_path(output_dir=tmp_path, course_name='course', lecture_title='lecture', content_id='abc', extension='mp4', conflict_strategy='overwrite')

    assert plan.file_path == course_dir / '기타' / 'lecture' / 'lecture.mp4'
    assert plan.file_exists is False
    assert old.read_text() == 'old bytes'


def test_download_to_plan_creates_course_dir_and_writes_response_bytes(tmp_path: Path):
    class FakeResponse:
        status_code = 200
        content = b'video-bytes'
        headers = {'content-type': 'application/octet-stream'}

        def raise_for_status(self):
            return None

    class FakeHttp:
        def __init__(self):
            self.calls = []

        def get(self, step, url, **kwargs):
            self.calls.append((step, url, kwargs))
            return FakeResponse()

    plan = DownloadPlan(output_dir=tmp_path, course_dir=tmp_path / 'course', file_path=tmp_path / 'course' / 'lecture.mp4')
    http = FakeHttp()

    result = download_to_plan(http=http, download_url='https://commons.ssu.ac.kr/download', plan=plan, referer='https://commons.ssu.ac.kr/em/id')

    assert result == plan.file_path
    assert plan.file_path.read_bytes() == b'video-bytes'
    assert http.calls == [
        (
            'download_media',
            'https://commons.ssu.ac.kr/download',
            {'headers': {'Referer': 'https://commons.ssu.ac.kr/em/id'}, 'allow_redirects': True, 'timeout': 10800, 'stream': True},
        )
    ]


def test_skip_reuses_a_file_saved_earlier_under_another_title(tmp_path):
    course_dir = tmp_path / 'UI디자인'
    course_dir.mkdir()
    earlier = course_dir / 'Ui_6주차_03 [6ac8fceb2f43d].mp4'
    earlier.write_bytes(b'video')

    plan = plan_download_path(output_dir=tmp_path, course_name='UI디자인', lecture_title='5주차_03_정리하기', content_id='6ac8fceb2f43d', extension='mp4', conflict_strategy='skip')
    assert plan.file_path == earlier
    assert plan.file_exists is True

    fresh = plan_download_path(output_dir=tmp_path, course_name='UI디자인', lecture_title='5주차_02_웹접근성', content_id='aaaaaaaaaaaaa', extension='mp4', conflict_strategy='skip')
    assert fresh.file_path == course_dir / '기타' / '5주차_02_웹접근성' / '5주차_02_웹접근성.mp4'
    assert fresh.file_exists is False
