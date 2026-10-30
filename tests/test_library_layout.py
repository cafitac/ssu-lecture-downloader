"""Folder and file names of the week -> lecture layout: pure rules, collisions, platform rules.

Tables are literal dicts of distinct cases; each case stands for a different branch of the rule.
Everything runs on temp trees; nothing here touches a real download folder.
"""

from __future__ import annotations

import json
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ssu_lecture_downloader.core.download import plan_library_download
from ssu_lecture_downloader.core.errors import LibraryConflict, PathTooLong
from ssu_lecture_downloader.core.library_layout import (
    CourseLayout,
    LibraryTarget,
    MaterialPaths,
    collision_name,
    item_kind_for_extension,
    lecture_stem,
    material_extension,
    visible_names,
    week_folder,
)
from ssu_lecture_downloader.platforms.macos import MacOSPlatform
from ssu_lecture_downloader.platforms.windows import WindowsPlatform

MAC = MacOSPlatform().path_rules()
WINDOWS = WindowsPlatform().path_rules()  # pure data: works while the rest of the Windows adapter is a placeholder

# --------------------------------------------------------------------------- names


def test_week_folder_rules():
    cases = {
        (5, ''): '05주차',
        (' 7 ', ''): '07주차',  # digit string
        (5.0, ''): '05주차',
        (0, ''): '00주차',
        (100, ''): '100주차',
        (4, '5주차'): '04주차',  # the LMS week beats a different number in the name
        (None, '5 주차'): '05주차',
        (None, 'week3 review'): '03주차',
        (None, unicodedata.normalize('NFD', '5주차: 행렬')): '05주차',
        (None, '중간/기말: 안내'): '중간_기말_ 안내',  # Q1: no number -> the module's own name
        (None, ''): '기타',
        (None, None): '기타',
        ('abc', ''): '기타',
        (-1, ''): '기타',
        (True, ''): '기타',
        (5.5, ''): '기타',
        ('９', ''): '기타',  # full-width digits are not a week number
    }
    assert {key: week_folder(*key) for key in cases} == cases
    assert set(week_folder(None, '가' * 200)) == {'가'} and len(week_folder(None, '가' * 200).encode()) <= MAC.folder_max_bytes


def test_stems_are_sanitised_nfc_trimmed_and_byte_capped_on_every_platform():
    cases = {
        '선대_5_2': '선대_5_2',
        'a/b:c*d?e"f<g>h|i': 'a_b_c_d_e_f_g_h_i',
        'line\nbreak\x00nul': 'line_break_nul',
        '  .. 점과 공백 ..  ': '점과 공백',
        '끝에 점...': '끝에 점',
        '여러   칸': '여러 칸',
        ' / ': '강의',
        unicodedata.normalize('NFD', '한글 제목'): '한글 제목',
    }
    for rules in (MAC, WINDOWS):
        assert {title: lecture_stem(title, rules) for title in cases} == cases
    assert unicodedata.is_normalized('NFC', lecture_stem(unicodedata.normalize('NFD', '한글')))
    for title in ('가' * 100 + '나' * 100, '😀' * 60, 'a' * 400, '가a' * 200):
        stem = lecture_stem(title)
        assert len(stem.encode()) <= 150 and title.startswith(stem)  # a clean prefix, no broken character
        names = visible_names(collision_name(stem, 99))
        assert all(len(name.encode()) <= 255 for name in (collision_name(stem, 99), *names.values()))


def test_visible_names_hide_ids_and_the_transcript_md_json_in_the_hidden_folder():
    assert visible_names('선대_5_2', 'mp4', 'mp3') == {'video': '선대_5_2.mp4', 'srt': '선대_5_2.srt', 'text': '선대_5_2 자막.txt', 'audio': '선대_5_2 음성.mp3'}
    paths = _layout_over(Path('/unused'), [_lecture('1', '선대_5_2', content_id='68e66b6c1b816')]).lecture('1')
    assert all('68e66b6c1b816' not in str(path) for path in paths.targets.values())
    assert paths.targets['md'] == paths.lecture_dir / '.ssu' / 'transcript.md'
    assert paths.targets['json'] == paths.lecture_dir / '.ssu' / 'transcript.json'


def test_windows_rules_rename_reserved_names_only():
    for name in ('CON', 'aux.txt', 'com1', 'con.tar.gz', 'Nul', 'LPT9'):
        safe = WINDOWS.stem(name)
        assert safe.split('.')[0].upper() not in {'CON', 'AUX', 'COM1', 'NUL', 'LPT9'} and safe.startswith(name.split('.')[0] + '_')
        assert MAC.stem(name) == name
    assert WINDOWS.stem('console') == 'console' and WINDOWS.stem('CON 강의') == 'CON 강의'


def test_windows_path_budget(tmp_path):
    base = tmp_path / 'course'
    stem = WINDOWS.fit_stem('가' * 100, base_dir=base, week_folder='05주차')
    longest = base / '05주차' / f'{stem} (99)' / f'{stem} (99) 자막.txt.part'  # collision number in folder and file, unfinished write
    assert len(str(longest)) <= 240 and len(stem) >= WINDOWS.min_stem_chars
    assert WINDOWS.fit_stem('짧은 제목', base_dir=Path('C:/x'), week_folder='05주차') == '짧은 제목'
    with pytest.raises(PathTooLong):
        WINDOWS.fit_stem('제목', base_dir=Path('C:/' + '/'.join(['가' * 40] * 5)), week_folder='05주차')
    assert MAC.fit_stem('x' * 100, base_dir=Path('/' + 'd' * 500), week_folder='05주차') == 'x' * 100


def test_a_layout_with_windows_rules_renames_reserved_stems_and_records_budget_failures_per_item(tmp_path):
    ok = CourseLayout.plan(tmp_path / 'c', [_lecture('1', 'CON', week=1)], course_id='1', rules=WINDOWS)
    assert ok.lecture('1').stem == 'CON_'
    deep = tmp_path.joinpath(*(['가' * 40] * 5))
    too_long = CourseLayout.plan(deep, [_lecture('1', '제목', week=1)], course_id='1', rules=WINDOWS)
    assert isinstance(too_long.failures['1'], PathTooLong) and too_long.lecture('1') is None


def test_material_and_item_kind_follow_the_reported_type_then_the_file_name():
    extensions = {
        ('pdf', 'week1.pdf'): 'pdf',
        ('application/pdf', None): 'pdf',
        ('file', 'week1.pdf'): 'pdf',  # O2: a generic type takes the LMS file_name extension
        ('file', 'week1'): 'file',
        ('', 'slides.PPTX'): 'pptx',
        (None, '.hidden'): 'file',
        ('file', 'a/b/c.zip'): 'zip',
        ('file', 'name.with space'): 'file',  # not a usable extension
    }
    assert {key: material_extension(*key) for key in extensions} == extensions
    assert [item_kind_for_extension(e) for e in ('mp4', 'readystream', '.MOV', 'webm')] == ['lecture'] * 4
    assert [item_kind_for_extension(e) for e in ('pdf', 'file', '', None, 'txt')] == ['material'] * 5


@pytest.mark.parametrize('hostile', ['../../etc/passwd', '..\\..\\x', '/absolute', '\x00', '..', 'C:\\Windows'])
def test_hostile_titles_module_names_and_extensions_never_leave_their_folder(tmp_path, hostile):
    layout = CourseLayout.plan(
        tmp_path / 'c', [_lecture('1', hostile, module_name=hostile, week=None), _material('2', hostile, ext='../../evil', week=None, module_name=hostile)], course_id='7'
    )
    lecture, material = layout.lecture('1'), layout.material('2')
    for path in (*lecture.targets.values(), material.target):
        assert (tmp_path / 'c') in path.parents and '..' not in path.relative_to(tmp_path / 'c').parts
    assert material.file_name.rsplit('.', 1)[-1].isalnum()


# --------------------------------------------------------------------------- collisions


def _lecture(key: str, title: str, *, week: object = 5, lesson: object = None, content_id: str | None = None, module_name: str | None = None) -> LibraryTarget:
    return LibraryTarget(
        key=key, title=title, kind='lecture', content_id=content_id if content_id is not None else f'cid-{key}', module_item_id=key,
        week_position=week, module_name=module_name, lesson_position=lesson,
    )


def _material(key: str, title: str, *, ext: str = 'pdf', week: object = 5, content_id: str | None = None, kind: str = 'material', module_name: str | None = None) -> LibraryTarget:
    return LibraryTarget(key=key, title=title, kind=kind, content_id=content_id or f'm-{key}', module_item_id=key, week_position=week, module_name=module_name, extension=ext)


def _layout_over(course_dir: Path, targets: list[LibraryTarget]) -> CourseLayout:
    return CourseLayout.plan(course_dir, targets, course_id='7', rules=MAC)


def _dirs(layout: CourseLayout, *keys: str) -> list[str]:
    return [f'{layout.lecture(key).lecture_dir.parent.name}/{layout.lecture(key).lecture_dir.name}' for key in keys]


def test_same_title_lectures_are_numbered_by_lesson_then_content_id_whatever_the_arrival_order(tmp_path):
    targets = [
        _lecture('a', '같은 제목', lesson=3, content_id='zzz'),
        _lecture('b', '같은 제목', lesson=1, content_id='mmm'),
        _lecture('c', '같은 제목', lesson=1, content_id='aaa'),
        _lecture('d', '같은 제목', lesson=None, content_id='000'),
        _lecture('e', 'Intro', lesson=1),
        _lecture('f', 'intro', lesson=2),  # case-insensitive volume: the same name
        _lecture('g', '같은 제목', week=6),  # another week does not collide
    ]
    for order in (targets, list(reversed(targets))):
        layout = _layout_over(tmp_path / 'c', order)
        assert _dirs(layout, 'c', 'b', 'a', 'd', 'e', 'f', 'g') == [
            '05주차/같은 제목', '05주차/같은 제목 (2)', '05주차/같은 제목 (3)', '05주차/같은 제목 (4)', '05주차/Intro', '05주차/intro (2)', '06주차/같은 제목',
        ]
    second = layout.lecture('b')
    assert second.targets['video'].name == '같은 제목 (2).mp4' and second.targets['text'].name == '같은 제목 (2) 자막.txt'  # the stem follows the folder


def test_a_content_id_listed_twice_is_one_lecture_folder(tmp_path):
    layout = _layout_over(tmp_path / 'c', [_lecture('1', '재사용', content_id='shared', week=1), _lecture('2', '재사용', content_id='shared', week=2)])
    assert layout.lecture('1').lecture_dir == layout.lecture('2').lecture_dir


def test_a_folder_that_is_not_this_lectures_is_never_reused(tmp_path):
    course = tmp_path / 'c'
    foreign = course / '05주차' / '선대_5_2'
    foreign.mkdir(parents=True)
    (foreign / 'my-notes.txt').write_text('user file')
    (course / '05주차' / '선대_5_2 (2)').mkdir()  # even an empty one without metadata is foreign
    other = _layout_over(course, [_lecture('9', '제목', content_id='one')]).lecture('9')
    other.prepare()

    layout = _layout_over(course, [_lecture('1', '선대_5_2'), _lecture('2', '제목', content_id='one'), _lecture('3', '제목', content_id='two')])

    assert _dirs(layout, '1', '2', '3') == ['05주차/선대_5_2 (3)', '05주차/제목', '05주차/제목 (2)']
    assert (foreign / 'my-notes.txt').read_text() == 'user file'


def test_a_lecture_already_on_disk_stays_where_it_is_when_the_lms_moves_or_renames_it(tmp_path):
    course = tmp_path / 'c'
    original = _layout_over(course, [_lecture('1', '이사한 강의', week=4, content_id='moved')]).lecture('1')
    original.prepare()

    after = _layout_over(course, [_lecture('1', '이름이 바뀐 강의', week=5, content_id='moved')]).lecture('1')

    assert after.lecture_dir == original.lecture_dir == course / '04주차' / '이사한 강의'
    assert after.stem == '이사한 강의' and after.meta is None  # no automatic rename (R12), nothing new to record


def test_parallel_preparation_of_same_title_lectures_never_shares_a_folder_and_the_next_pass_finds_them(tmp_path):
    course = tmp_path / 'c'
    targets = [_lecture(str(n), '병렬 강의', lesson=n) for n in range(1, 21)]
    planned = [_layout_over(course, targets).lecture(str(n)) for n in range(1, 21)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda paths: paths.prepare(), planned))

    assert len({path.name for path in (course / '05주차').iterdir()}) == 20
    again = _layout_over(course, targets)
    assert [again.lecture(str(n)).lecture_dir for n in range(1, 21)] == [paths.lecture_dir for paths in planned]


# --------------------------------------------------------------------------- materials


def test_materials_sit_in_the_week_folder_are_numbered_and_never_take_an_unknown_files_name(tmp_path):
    week = tmp_path / 'c' / '05주차'
    week.mkdir(parents=True)
    (week / '메모.pdf').write_text('the student\'s own file')
    layout = _layout_over(tmp_path / 'c', [_material('1', '강의자료', content_id='a'), _material('2', '강의자료', content_id='b'), _material('3', '메모')])
    assert [layout.material(key).target for key in '123'] == [week / '강의자료.pdf', week / '강의자료 (2).pdf', week / '메모 (2).pdf']


def test_a_material_resolved_to_a_specific_type_is_renamed_but_a_generic_type_keeps_the_file_name_extension(tmp_path):
    layout = _layout_over(tmp_path / 'c', [_material('1', '자료', ext='pdf')])
    assert layout.material('1', 'file').file_name == '자료.pdf'
    assert layout.material('1', 'pptx').file_name == '자료.pptx'


def test_canvas_attachment_goes_to_the_week_of_its_page_item_then_the_week_in_its_title_then_the_catch_all(tmp_path):
    def week_of(**fields):
        title = fields.pop('title', '첨부')
        return _layout_over(tmp_path / 'c', [_material('c', title, kind='canvas_file', content_id='canvas-1', **fields)]).material('c').week_dir.name

    assert week_of(week=3) == '03주차'
    assert week_of(week=None, module_name='Week 4') == '04주차'
    assert week_of(week=None, title='UI디자인_1주차_202602') == '01주차'
    assert week_of(week=None) == '기타'


def test_prepare_refuses_a_material_name_held_by_another_id_or_by_an_unrecorded_file(tmp_path):
    course = tmp_path / 'c'
    mine = _layout_over(course, [_material('1', '자료', content_id='mine')]).material('1')
    mine.prepare()
    mine.target.write_bytes(b'mine')
    other_case = MaterialPaths(mine.week_dir, '자료.PDF', 'theirs', 'material', None, None, course)
    with pytest.raises(LibraryConflict):
        other_case.prepare()

    foreign = _layout_over(course, [_material('3', '메모', content_id='new')]).material('3')
    foreign.target.write_bytes(b'student file')  # appeared after planning
    assert plan_library_download(output_dir=tmp_path, library=foreign, extension='pdf', conflict_strategy='skip').file_exists is False
    with pytest.raises(LibraryConflict):
        foreign.prepare()
    assert foreign.target.read_bytes() == b'student file'
    assert plan_library_download(output_dir=tmp_path, library=mine, extension='pdf', conflict_strategy='skip').file_exists is True


# --------------------------------------------------------------------------- item.json


def test_prepare_writes_item_json_with_every_schema_field(tmp_path):
    paths = _layout_over(tmp_path / 'c', [_lecture('9', '메타 강의', week=2, content_id='cid-9', module_name='2주차')]).lecture('9')
    paths.prepare()

    item = json.loads((paths.lecture_dir / '.ssu' / 'item.json').read_text(encoding='utf-8'))
    assert set(item) == {
        'schema', 'content_id', 'module_item_id', 'course_id', 'kind', 'lms_title', 'resolved_title', 'stem', 'week_folder',
        'created_by', 'app_version', 'layout_version', 'created_at', 'updated_at',
    }
    assert item['schema'] == 1 and item['layout_version'] == 1
    assert (item['content_id'], item['module_item_id'], item['course_id'], item['kind']) == ('cid-9', '9', '7', 'lecture')
    assert (item['stem'], item['week_folder'], item['created_by'], item['lms_title']) == ('메타 강의', '02주차', 'download', '메타 강의')
    assert item['created_at'].endswith('Z') and item['updated_at'].endswith('Z')

