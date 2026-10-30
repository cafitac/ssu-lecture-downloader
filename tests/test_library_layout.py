"""Folder and file names of the week -> lecture layout: pure rules, collisions, platform rules.

Everything runs on temp trees; nothing here touches a real download folder.
"""

from __future__ import annotations

import json
import random
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ssu_lecture_downloader.core.library_layout import (
    MACOS_RULES,
    WINDOWS_RULES,
    CourseLayout,
    LecturePaths,
    LibraryTarget,
    PathTooLong,
    collision_name,
    lecture_stem,
    material_extension,
    visible_names,
    week_folder,
)
from ssu_lecture_downloader.platforms.macos import MacOSPlatform
from ssu_lecture_downloader.platforms.windows import WindowsPlatform

# --------------------------------------------------------------------------- AC1: week folder


@pytest.mark.parametrize(
    ('week_position', 'module_name', 'expected'),
    [
        (5, '', '05주차'),
        (12, '12주차', '12주차'),
        ('7', '', '07주차'),
        (' 7 ', '', '07주차'),
        (5.0, '', '05주차'),
        (0, '', '00주차'),
        (100, '', '100주차'),
        (None, '5주차: 행렬', '05주차'),
        (None, '5 주차', '05주차'),
        (None, '제 12주차 시험', '12주차'),
        (None, 'Week 3', '03주차'),
        (None, 'week3 review', '03주차'),
        (None, unicodedata.normalize('NFD', '5주차: 행렬'), '05주차'),
        # the LMS week wins over a different number in the name
        (4, '5주차', '04주차'),
        # no number anywhere: the module's own name (Q1), else the catch-all
        (None, '오리엔테이션', '오리엔테이션'),
        (None, '중간/기말: 안내', '중간_기말_ 안내'),
        (None, '', '기타'),
        (None, None, '기타'),
        (None, '  ./  ', '기타'),
        ('abc', '', '기타'),
        (-1, '', '기타'),
        (True, '', '기타'),
        (5.5, '', '기타'),
        ('９', '', '기타'),  # full-width digits are not a week number
    ],
)
def test_week_folder_table(week_position, module_name, expected):
    assert week_folder(week_position, module_name) == expected


def test_week_folder_caps_a_long_module_name_on_a_character_boundary():
    folder = week_folder(None, '가' * 200)
    assert len(folder.encode()) <= MACOS_RULES.folder_max_bytes
    assert set(folder) == {'가'}


# --------------------------------------------------------------------------- AC1: stems and names


@pytest.mark.parametrize(
    ('title', 'expected'),
    [
        ('선대_5_2', '선대_5_2'),
        ('a/b:c*d?e"f<g>h|i', 'a_b_c_d_e_f_g_h_i'),
        ('line\nbreak\tand\x00nul', 'line_break_and_nul'),
        ('  .. 점과 공백 ..  ', '점과 공백'),
        ('여러   칸     공백', '여러 칸 공백'),
        ('', '강의'),
        (' / ', '강의'),
        (None, '강의'),
    ],
)
def test_lecture_stem_sanitises_titles(title, expected):
    assert lecture_stem(title) == expected


def test_stem_is_normalised_to_nfc_so_the_same_title_in_nfd_is_one_name():
    nfd = unicodedata.normalize('NFD', '한글 제목')
    assert nfd != '한글 제목'
    assert lecture_stem(nfd) == '한글 제목'
    assert unicodedata.is_normalized('NFC', lecture_stem(nfd))


def test_a_300_byte_korean_title_is_cut_on_a_character_boundary_and_every_name_fits_255_bytes():
    title = '가' * 100 + '나' * 100  # 600 bytes
    stem = lecture_stem(title)
    assert len(stem.encode()) <= 150
    assert title.startswith(stem)  # a clean prefix, no broken character
    stem_with_copy = collision_name(stem, 99)
    names = visible_names(stem_with_copy)
    for name in (stem_with_copy, *names.values()):
        assert len(name.encode()) <= 255, name


@pytest.mark.parametrize('title', ['가' * 60, '😀' * 60, 'a' * 400, '가a' * 200])
def test_stem_cap_never_splits_a_character(title):
    stem = lecture_stem(title)
    assert len(stem.encode()) <= 150
    stem.encode().decode('utf-8')  # raises on a broken sequence


def test_visible_names_have_the_four_plain_names_and_no_content_id():
    names = visible_names('선대_5_2', 'mp4', 'mp3')
    assert names == {'video': '선대_5_2.mp4', 'srt': '선대_5_2.srt', 'text': '선대_5_2 자막.txt', 'audio': '선대_5_2 음성.mp3'}
    layout = _layout_over(Path('/unused'), [_lecture('1', '선대_5_2', content_id='68e66b6c1b816')])
    paths = layout.lecture('1')
    assert paths is not None
    assert all('68e66b6c1b816' not in str(path) for path in paths.targets.values())


def test_transcript_md_and_json_are_hidden_inside_the_lecture_folder():
    layout = _layout_over(Path('/unused'), [_lecture('1', '선대_5_2', week=5)])
    paths = layout.lecture('1')
    assert paths is not None
    assert paths.targets['md'] == paths.lecture_dir / '.ssu' / 'transcript.md'
    assert paths.targets['json'] == paths.lecture_dir / '.ssu' / 'transcript.json'
    assert paths.targets['text'].name == '선대_5_2 자막.txt'


# --------------------------------------------------------------------------- AC1: platform rules


@pytest.mark.parametrize('name', ['CON', 'aux', 'Nul', 'com1', 'LPT9', 'aux.txt', 'con.tar.gz'])
def test_windows_rules_rename_reserved_device_names(name):
    safe = WINDOWS_RULES.stem(name)
    assert safe.split('.')[0].upper() not in {'CON', 'AUX', 'NUL', 'COM1', 'LPT9'}
    assert safe.startswith(name.split('.')[0] + '_')
    assert MACOS_RULES.stem(name) == name  # macOS has no reserved names


def test_windows_rules_keep_names_that_only_contain_a_reserved_word():
    assert WINDOWS_RULES.stem('console') == 'console'
    assert WINDOWS_RULES.stem('CON 강의') == 'CON 강의'


@pytest.mark.parametrize('title', ['끝에 점.', '끝에 점...', '끝에 공백 ', '끝에 점과 공백. .'])
def test_trailing_dots_and_spaces_are_trimmed_on_every_platform(title):
    for rules in (MACOS_RULES, WINDOWS_RULES):
        assert not rules.stem(title).endswith(('.', ' '))


def test_windows_path_budget_shortens_the_stem_and_never_the_week_folder(tmp_path):
    base = tmp_path / 'course'
    stem = WINDOWS_RULES.fit_stem('가' * 100, base_dir=base, week_folder='05주차')
    longest = base / '05주차' / stem / f'{stem} (99) 자막.txt'
    assert len(str(longest)) <= 240
    assert len(stem) >= WINDOWS_RULES.min_stem_chars


def test_windows_path_budget_keeps_a_short_stem_untouched(tmp_path):
    assert WINDOWS_RULES.fit_stem('짧은 제목', base_dir=Path('C:/x'), week_folder='05주차') == '짧은 제목'


def test_windows_path_budget_that_cannot_be_met_is_reported(tmp_path):
    deep = Path('C:/' + '/'.join(['가' * 40] * 5))
    with pytest.raises(PathTooLong):
        WINDOWS_RULES.fit_stem('제목', base_dir=deep, week_folder='05주차')


def test_macos_has_no_path_budget():
    assert MACOS_RULES.max_path_chars is None
    assert MACOS_RULES.fit_stem('x' * 100, base_dir=Path('/' + 'd' * 500), week_folder='05주차') == 'x' * 100


def test_platforms_expose_their_path_rules():
    assert MacOSPlatform().path_rules() is MACOS_RULES
    assert WindowsPlatform().path_rules() is WINDOWS_RULES  # pure data: works while the rest of the adapter is a placeholder


def test_windows_rules_are_used_by_a_layout_that_is_given_them(tmp_path):
    layout = CourseLayout.plan(tmp_path / 'c', [_lecture('1', 'CON', week=1)], course_id='1', rules=WINDOWS_RULES)
    paths = layout.lecture('1')
    assert paths is not None and paths.stem == 'CON_'
    assert layout.failures == {}


def test_a_layout_records_a_path_budget_failure_per_item_instead_of_failing_the_course(tmp_path):
    deep = tmp_path.joinpath(*(['가' * 40] * 5))
    layout = CourseLayout.plan(deep, [_lecture('1', '제목', week=1)], course_id='1', rules=WINDOWS_RULES)
    assert isinstance(layout.failures['1'], PathTooLong)
    assert layout.lecture('1') is None


# --------------------------------------------------------------------------- materials


@pytest.mark.parametrize(
    ('content_type', 'file_name', 'expected'),
    [
        ('pdf', 'week1.pdf', 'pdf'),
        ('PDF', None, 'pdf'),
        ('application/pdf', None, 'pdf'),
        ('file', 'week1.pdf', 'pdf'),  # O2: a generic type takes the LMS file_name extension
        ('file', 'week1', 'file'),
        ('file', None, 'file'),
        ('', 'slides.PPTX', 'pptx'),
        (None, '.hidden', 'file'),
        ('file', 'a/b/c.zip', 'zip'),
        ('file', 'name.with space', 'file'),
    ],
)
def test_material_extension_uses_the_file_name_when_the_type_is_generic(content_type, file_name, expected):
    assert material_extension(content_type, file_name) == expected


# --------------------------------------------------------------------------- AC2: collisions


def _lecture(key: str, title: str, *, week: object = 5, lesson: object = None, content_id: str | None = None, module_name: str | None = None) -> LibraryTarget:
    return LibraryTarget(
        key=key, title=title, kind='lecture', content_id=content_id if content_id is not None else f'cid-{key}', module_item_id=key,
        week_position=week, module_name=module_name, lesson_position=lesson,
    )


def _layout_over(course_dir: Path, targets: list[LibraryTarget]) -> CourseLayout:
    return CourseLayout.plan(course_dir, targets, course_id='7')


def _dirs(layout: CourseLayout, *keys: str) -> list[str]:
    result = []
    for key in keys:
        paths = layout.lecture(key)
        assert paths is not None
        result.append(f'{paths.lecture_dir.parent.name}/{paths.lecture_dir.name}')
    return result


def test_two_lectures_with_one_title_in_a_week_get_a_numbered_second_folder(tmp_path):
    course = tmp_path / 'c'
    layout = _layout_over(course, [_lecture('1', '선대_5_2', lesson=1), _lecture('2', '선대_5_2', lesson=2)])

    assert _dirs(layout, '1', '2') == ['05주차/선대_5_2', '05주차/선대_5_2 (2)']
    second = layout.lecture('2')
    assert second is not None and second.targets['video'].name == '선대_5_2 (2).mp4'  # the stem follows the folder
    assert second.targets['text'].name == '선대_5_2 (2) 자막.txt'


def test_numbering_follows_lesson_order_then_content_id_not_the_order_items_arrive(tmp_path):
    course = tmp_path / 'c'
    targets = [
        _lecture('a', '같은 제목', lesson=3, content_id='zzz'),
        _lecture('b', '같은 제목', lesson=1, content_id='mmm'),
        _lecture('c', '같은 제목', lesson=1, content_id='aaa'),
        _lecture('d', '같은 제목', lesson=None, content_id='000'),
    ]
    # ranking: c (lesson 1, id aaa) < b (lesson 1, id mmm) < a (lesson 3) < d (no lesson)
    for seed in range(12):
        shuffled = list(targets)
        random.Random(seed).shuffle(shuffled)
        layout = _layout_over(course, shuffled)
        assert _dirs(layout, 'c', 'b', 'a', 'd') == ['05주차/같은 제목', '05주차/같은 제목 (2)', '05주차/같은 제목 (3)', '05주차/같은 제목 (4)'], seed


def test_same_title_in_different_weeks_does_not_collide(tmp_path):
    layout = _layout_over(tmp_path / 'c', [_lecture('1', '복습', week=1), _lecture('2', '복습', week=2)])
    assert _dirs(layout, '1', '2') == ['01주차/복습', '02주차/복습']


def test_a_content_id_listed_twice_is_one_lecture_folder(tmp_path):
    layout = _layout_over(tmp_path / 'c', [_lecture('1', '재사용', content_id='shared', week=1), _lecture('2', '재사용', content_id='shared', week=2)])
    first, second = layout.lecture('1'), layout.lecture('2')
    assert first is not None and second is not None and first.lecture_dir == second.lecture_dir


def test_a_foreign_folder_is_never_reused_so_the_new_lecture_gets_a_number(tmp_path):
    course = tmp_path / 'c'
    foreign = course / '05주차' / '선대_5_2'
    foreign.mkdir(parents=True)
    (foreign / 'my-notes.txt').write_text('user file')
    (course / '05주차' / '선대_5_2 (2)').mkdir()  # even an empty one without metadata is foreign

    layout = _layout_over(course, [_lecture('1', '선대_5_2')])

    assert _dirs(layout, '1') == ['05주차/선대_5_2 (3)']
    assert (foreign / 'my-notes.txt').read_text() == 'user file'


def test_a_folder_with_another_lectures_metadata_is_not_reused(tmp_path):
    course = tmp_path / 'c'
    first = _layout_over(course, [_lecture('1', '제목', content_id='one')]).lecture('1')
    assert first is not None
    first.prepare()

    layout = _layout_over(course, [_lecture('1', '제목', content_id='one'), _lecture('2', '제목', content_id='two')])

    assert _dirs(layout, '1', '2') == ['05주차/제목', '05주차/제목 (2)']


def test_a_lecture_already_on_disk_stays_where_it_is_when_the_lms_moves_it(tmp_path):
    course = tmp_path / 'c'
    original = _layout_over(course, [_lecture('1', '이사한 강의', week=4, content_id='moved')]).lecture('1')
    assert original is not None
    original.prepare()

    after_move = _layout_over(course, [_lecture('1', '이름이 바뀐 강의', week=5, content_id='moved')]).lecture('1')

    assert after_move is not None
    assert after_move.lecture_dir == original.lecture_dir == course / '04주차' / '이사한 강의'
    assert after_move.stem == '이사한 강의'  # no automatic rename (R12)
    assert after_move.meta is None  # nothing new to record


def test_case_only_differences_collide_on_a_case_insensitive_volume(tmp_path):
    layout = _layout_over(tmp_path / 'c', [_lecture('1', 'Intro', lesson=1), _lecture('2', 'intro', lesson=2)])
    assert _dirs(layout, '1', '2') == ['05주차/Intro', '05주차/intro (2)']


def test_parallel_preparation_of_same_title_lectures_never_shares_a_folder(tmp_path):
    course = tmp_path / 'c'
    targets = [_lecture(str(n), '병렬 강의', lesson=n) for n in range(1, 21)]
    layout = _layout_over(course, targets)
    planned = [layout.lecture(str(n)) for n in range(1, 21)]
    assert all(paths is not None for paths in planned)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda paths: paths.prepare(), planned))  # type: ignore[union-attr]

    folders = sorted(path.name for path in (course / '05주차').iterdir())
    assert len(folders) == 20 and len(set(folders)) == 20
    # a second planning pass over the folders just written finds every lecture where it is
    again = _layout_over(course, targets)
    assert [again.lecture(str(n)).lecture_dir for n in range(1, 21)] == [paths.lecture_dir for paths in planned]  # type: ignore[union-attr]


def test_selection_does_not_change_names_because_the_whole_course_is_planned(tmp_path):
    course = tmp_path / 'c'
    everything = [_lecture('1', '같은 제목', lesson=1), _lecture('2', '같은 제목', lesson=2)]
    layout = _layout_over(course, everything)
    # a job that selects only lecture 2 still gets the "(2)" planned for it
    assert _dirs(layout, '2') == ['05주차/같은 제목 (2)']


# --------------------------------------------------------------------------- material names


def _material(key: str, title: str, *, ext: str = 'pdf', week: object = 5, content_id: str | None = None, kind: str = 'material', module_name: str | None = None) -> LibraryTarget:
    return LibraryTarget(key=key, title=title, kind=kind, content_id=content_id or f'm-{key}', module_item_id=key, week_position=week, module_name=module_name, extension=ext)


def test_materials_sit_directly_in_the_week_folder_and_same_titles_are_numbered(tmp_path):
    layout = _layout_over(tmp_path / 'c', [_material('1', '강의자료', content_id='a'), _material('2', '강의자료', content_id='b')])
    first, second = layout.material('1'), layout.material('2')
    assert first is not None and second is not None
    assert first.target == tmp_path / 'c' / '05주차' / '강의자료.pdf'
    assert second.target == tmp_path / 'c' / '05주차' / '강의자료 (2).pdf'


def test_a_material_resolved_to_a_specific_type_is_renamed_but_a_generic_type_keeps_the_file_name_extension(tmp_path):
    layout = _layout_over(tmp_path / 'c', [_material('1', '자료', ext='pdf')])
    assert layout.material('1', 'file').file_name == '자료.pdf'  # generic: keep the LMS file_name extension
    assert layout.material('1', 'pptx').file_name == '자료.pptx'


def test_a_material_name_taken_by_an_unknown_file_gets_a_number(tmp_path):
    week = tmp_path / 'c' / '05주차'
    week.mkdir(parents=True)
    (week / '자료.pdf').write_text('the student\'s own file')
    layout = _layout_over(tmp_path / 'c', [_material('1', '자료')])
    assert layout.material('1').file_name == '자료 (2).pdf'


def test_canvas_attachment_goes_to_the_week_of_its_page_item_then_the_week_in_its_title_then_the_catch_all(tmp_path):
    course = tmp_path / 'c'
    with_fields = _layout_over(course, [_material('c1', '첨부', kind='canvas_file', week=3, content_id='canvas-1')])
    with_module_name = _layout_over(course, [_material('c2', '첨부', kind='canvas_file', week=None, module_name='Week 4', content_id='canvas-2')])
    from_title = _layout_over(course, [_material('c3', 'UI디자인_1주차_202602', kind='canvas_file', week=None, content_id='canvas-3')])
    nowhere = _layout_over(course, [_material('c4', '첨부', kind='canvas_file', week=None, content_id='canvas-4')])

    assert with_fields.material('c1').week_dir.name == '03주차'
    assert with_module_name.material('c2').week_dir.name == '04주차'
    assert from_title.material('c3').week_dir.name == '01주차'
    assert nowhere.material('c4').week_dir.name == '기타'


# --------------------------------------------------------------------------- explicit files


def test_prepare_writes_item_json_with_every_schema_field(tmp_path):
    paths = _layout_over(tmp_path / 'c', [_lecture('9', '메타 강의', week=2, content_id='cid-9', module_name='2주차')]).lecture('9')
    assert paths is not None
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
    assert isinstance(paths, LecturePaths)


@pytest.mark.parametrize('hostile', ['../../etc/passwd', '..\\..\\x', '/absolute', 'a/../../b', '\x00', '..', '.', '~/x', 'C:\\Windows'])
def test_hostile_titles_and_module_names_never_leave_their_folder(tmp_path, hostile):
    layout = CourseLayout.plan(
        tmp_path / 'c',
        [
            _lecture('1', hostile, module_name=hostile, week=None),
            _material('2', hostile, ext='../../evil', week=None, module_name=hostile),
        ],
        course_id='7',
    )
    lecture, material = layout.lecture('1'), layout.material('2')
    assert lecture is not None and material is not None
    for path in (*lecture.targets.values(), material.target):
        assert (tmp_path / 'c') in path.parents
        assert '..' not in path.relative_to(tmp_path / 'c').parts
    assert material.file_name.rsplit('.', 1)[-1].isalnum()
