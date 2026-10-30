"""Tidy-up of the old flat layout: preview, apply, undo, crash recovery (AC7-AC10).

Temp trees only. The LMS is a fake catalog; the end-to-end test runs the real service and pipeline on the
fake LMS of `test_library_index`. Nothing waits on time: progress throttling uses an injected clock, jobs are
joined by thread.
"""

from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
import stat
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from test_library_index import COURSE, Lec, rig_for, stages, touch  # noqa: F401  (rig_for is a fixture)

from ssu_lecture_downloader.core import library_migration
from ssu_lecture_downloader.core.errors import TidyRunNotFound
from ssu_lecture_downloader.core.download import find_downloaded_files
from ssu_lecture_downloader.core.library_meta import read_item_meta
from ssu_lecture_downloader.core.library_migration import (
    CatalogCourse,
    MigrationRunner,
    count_legacy_files,
    legacy_summary,
    migrations_dir,
    read_journal,
)
from ssu_lecture_downloader.core.models import CourseContentItem

# --------------------------------------------------------------------------- fixtures


def video(item_id: int, title: str, content_id: str, *, week: int | None = 5, module: str = '5주차', lesson: int = 1) -> CourseContentItem:
    return CourseContentItem(
        module_id=1, module_name=module, week_position=week, lesson_position=lesson, item_id=None, module_item_id=item_id, title=title,
        content_type='attendance_item', inner_content_type='mp4', kind='video', content_id=content_id, downloadable=True, resolvable=True,
    )


def material(item_id: int, title: str, content_id: str, *, inner: str = 'pdf', file_name: str | None = None, week: int = 5, lesson: int = 9) -> CourseContentItem:
    return CourseContentItem(
        module_id=1, module_name=f'{week}주차', week_position=week, lesson_position=lesson, item_id=None, module_item_id=item_id, title=title,
        content_type='attendance_item', inner_content_type=inner, kind='material', content_id=content_id, file_name=file_name, downloadable=True, resolvable=True,
    )


class FakeCatalog:
    """The current LMS courses: folder name -> content items."""

    def __init__(self, courses: dict[str, list[CourseContentItem]]) -> None:
        self._courses = courses

    def courses(self) -> list[CatalogCourse]:
        return [CatalogCourse(str(number), name) for number, name in enumerate(self._courses, start=100)]

    def content_items(self, course: CatalogCourse) -> list[CourseContentItem]:
        return self._courses[course.name]


def write_files(root: Path, names: list[str]) -> None:
    """Empty-ish files with distinct content and a fixed old mtime, so a snapshot sees every change."""
    for number, name in enumerate(names):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f'{name}|{number}'.encode())
        os.utime(path, ns=(1_700_000_000_000_000_000 + number, 1_700_000_000_000_000_000 + number))


def snapshot(root: Path) -> dict[str, tuple[Any, ...]]:
    """Every name under the root with type, size, mtime and inode (directories: type only)."""
    found: dict[str, tuple[Any, ...]] = {}
    for current, directories, files in os.walk(root):
        for name in [*directories, *files]:
            path = Path(current) / name
            info = os.lstat(path)
            kind = 'dir' if stat.S_ISDIR(info.st_mode) else 'link' if stat.S_ISLNK(info.st_mode) else 'file'
            found[path.relative_to(root).as_posix()] = (kind,) if kind == 'dir' else (kind, info.st_size, info.st_mtime_ns, info.st_ino)
    return found


def file_identities(root: Path) -> Counter[tuple[int, int]]:
    """(inode, size) of every file except the app's own metadata (materials.json is rewritten atomically by design)."""
    return Counter((info[3], info[1]) for name, info in snapshot(root).items() if info[0] == 'file' and not name.endswith('materials.json'))


LA = '선형대수'
EQ = '감성지능리더십'
UI = 'UI디자인'
LEGACY_TREE = [
    # lecture with legacy .txt only as transcript, srt, video
    f'{LA}/선대_5_2 [cid-a].mp4', f'{LA}/선대_5_2 [cid-a].srt', f'{LA}/선대_5_2 [cid-a].txt',
    # no video, new-style transcript files, and the old .txt next to .transcript.txt
    f'{LA}/선대_6_1 [cid-b].srt', f'{LA}/선대_6_1 [cid-b].transcript.txt', f'{LA}/선대_6_1 [cid-b].transcript.md',
    f'{LA}/선대_6_1 [cid-b].transcript.json', f'{LA}/선대_6_1 [cid-b].txt', f'{LA}/선대_6_1 [cid-b].mp3',
    # no longer in the LMS list (Q6)
    f'{LA}/사라진 강의 [gone-1].mp4', f'{LA}/사라진 강의 [gone-1].transcript.txt',
    # stays: partial download, unknown file, user folder
    f'{LA}/선대_7_1 [cid-c].mp4.part', f'{LA}/notes.docx', f'{LA}/내 메모/메모.txt', f'{LA}/.DS_Store',
    # rename duplicate (Q5), materials with a generic extension
    f'{EQ}/7주차 1강 감성지능 리더십 [cid-d].mp4', f'{EQ}/7주차 1강 감성지능 리더십 [cid-d] (2).mp4',
    f'{EQ}/5주차 타인 인식 [cid-e].file', f'{EQ}/자료 [cid-f].pdf',
    # a week that already holds a material in the new layout
    f'{EQ}/05주차/기존 자료.pdf',
    # canvas attachments (week from the title, else 기타), a lecture saved under another title, two lectures of one title
    f'{UI}/UI디자인_1주차_202602 [canvas-4732412].pdf', f'{UI}/부록 [canvas-1].pdf', f'{UI}/옛 제목 [cid-g].mp4',
    f'{UI}/같은 제목 [cid-h1].mp4', f'{UI}/같은 제목 [cid-h2].mp4',
    # a course folder the LMS no longer lists
    '옛 학기 과목/이전 강의 [old-1].mp4', '옛 학기 과목/이전 강의 [old-1].transcript.txt',
]

CATALOG = {
    LA: [video(1, '선대_5_2', 'cid-a'), video(2, '선대_6_1', 'cid-b', week=6, module='6주차'), video(3, '선대_7_1', 'cid-c', week=7, module='7주차')],
    EQ: [video(11, '7주차 1강 감성지능 리더십', 'cid-d', week=7, module='7주차'), material(12, '5주차 타인 인식', 'cid-e', inner='file', file_name='a.pdf'), material(13, '자료', 'cid-f')],
    UI: [video(21, '새 제목', 'cid-g', week=3, module='3주차'), video(22, '같은 제목', 'cid-h1', week=2, module='2주차', lesson=1), video(23, '같은 제목', 'cid-h2', week=2, module='2주차', lesson=2)],
}

# source -> (destination, role); the mapping table of design 07
EXPECTED_MOVES = {
    f'{LA}/선대_5_2 [cid-a].mp4': (f'{LA}/05주차/선대_5_2/선대_5_2.mp4', 'video'),
    f'{LA}/선대_5_2 [cid-a].srt': (f'{LA}/05주차/선대_5_2/선대_5_2.srt', 'srt'),
    f'{LA}/선대_5_2 [cid-a].txt': (f'{LA}/05주차/선대_5_2/선대_5_2 자막.txt', 'text'),
    f'{LA}/선대_6_1 [cid-b].srt': (f'{LA}/06주차/선대_6_1/선대_6_1.srt', 'srt'),
    f'{LA}/선대_6_1 [cid-b].transcript.txt': (f'{LA}/06주차/선대_6_1/선대_6_1 자막.txt', 'text'),
    f'{LA}/선대_6_1 [cid-b].transcript.md': (f'{LA}/06주차/선대_6_1/.ssu/transcript.md', 'md'),
    f'{LA}/선대_6_1 [cid-b].transcript.json': (f'{LA}/06주차/선대_6_1/.ssu/transcript.json', 'json'),
    f'{LA}/선대_6_1 [cid-b].mp3': (f'{LA}/06주차/선대_6_1/선대_6_1 음성.mp3', 'audio'),
    f'{LA}/사라진 강의 [gone-1].mp4': (f'{LA}/기타/사라진 강의/사라진 강의.mp4', 'video'),
    f'{LA}/사라진 강의 [gone-1].transcript.txt': (f'{LA}/기타/사라진 강의/사라진 강의 자막.txt', 'text'),
    f'{EQ}/7주차 1강 감성지능 리더십 [cid-d].mp4': (f'{EQ}/07주차/7주차 1강 감성지능 리더십/7주차 1강 감성지능 리더십.mp4', 'video'),
    f'{EQ}/7주차 1강 감성지능 리더십 [cid-d] (2).mp4': (f'{EQ}/07주차/7주차 1강 감성지능 리더십/7주차 1강 감성지능 리더십 (2).mp4', 'duplicate'),
    f'{EQ}/5주차 타인 인식 [cid-e].file': (f'{EQ}/05주차/5주차 타인 인식.pdf', 'material'),
    f'{EQ}/자료 [cid-f].pdf': (f'{EQ}/05주차/자료.pdf', 'material'),
    f'{UI}/UI디자인_1주차_202602 [canvas-4732412].pdf': (f'{UI}/01주차/UI디자인_1주차_202602.pdf', 'material'),
    f'{UI}/부록 [canvas-1].pdf': (f'{UI}/기타/부록.pdf', 'material'),
    f'{UI}/옛 제목 [cid-g].mp4': (f'{UI}/03주차/새 제목/새 제목.mp4', 'video'),
    f'{UI}/같은 제목 [cid-h1].mp4': (f'{UI}/02주차/같은 제목/같은 제목.mp4', 'video'),
    f'{UI}/같은 제목 [cid-h2].mp4': (f'{UI}/02주차/같은 제목 (2)/같은 제목 (2).mp4', 'video'),
}
EXPECTED_SKIPS = {
    f'{LA}/선대_6_1 [cid-b].txt': 'duplicate_transcript',
    f'{LA}/선대_7_1 [cid-c].mp4.part': 'partial_download',
    f'{LA}/notes.docx': 'unknown_file',
    f'{LA}/내 메모': 'not_a_regular_file',
    f'{LA}/바로가기': 'not_a_regular_file',
    '옛 학기 과목': 'course_not_matched',
}


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / 'library'
    write_files(root, LEGACY_TREE)
    (root / LA / '바로가기').symlink_to(tmp_path)  # a link out of the library: never followed, never moved
    ssu = root / EQ / '05주차' / '.ssu'
    ssu.mkdir()
    (ssu / 'materials.json').write_text(json.dumps({'schema': 1, 'entries': {'cid-m0': {'file_name': '기존 자료.pdf', 'kind': 'material', 'title': '기존 자료'}}}))
    return root


def planned(runner: MigrationRunner, root: Path):
    return runner.plan(root, FakeCatalog(CATALOG))


def by_source(plan, root: Path) -> dict[str, tuple[str, str]]:
    return {move.source.relative_to(root).as_posix(): (move.destination.relative_to(root).as_posix(), move.role) for move in plan.moves}


def journal_of(root: Path, run_id: str) -> list[dict[str, Any]]:
    return read_journal(migrations_dir(root) / f'{run_id}.jsonl')


# --------------------------------------------------------------------------- AC7: preview


def test_the_preview_lists_the_expected_moves_and_skips_and_changes_nothing(tree):
    before = snapshot(tree)

    plan = planned(MigrationRunner(), tree)

    assert by_source(plan, tree) == EXPECTED_MOVES
    assert {skip.path: skip.reason for skip in plan.skips} == EXPECTED_SKIPS
    assert snapshot(tree) == before  # no name, size, mtime or inode changed; no folder or journal appeared
    assert plan.payload()['moves'][0].keys() == {'course', 'week_folder', 'lecture_stem', 'from', 'to', 'role'}


def test_legacy_summary_counts_attributable_files_per_course_without_the_lms(tree):
    summary = legacy_summary(tree)

    assert {course['folder']: course['legacy_file_count'] for course in summary['courses']} == {LA: 10, EQ: 4, UI: 5, '옛 학기 과목': 2}  # the old .txt next to a .transcript.txt always stays: not counted
    assert summary['legacy_file_count'] == 21 and summary['course_count'] == 4
    assert count_legacy_files(tree / LA) == 10


# --------------------------------------------------------------------------- AC8: apply


def test_apply_moves_exactly_the_preview_journals_first_and_a_second_preview_has_nothing_to_move(tree):
    runner = MigrationRunner()
    plan = planned(runner, tree)
    before = file_identities(tree)

    result = runner.apply(plan)

    assert result.moved == len(EXPECTED_MOVES) and result.undo_available
    for source, (destination, _) in EXPECTED_MOVES.items():
        assert not os.path.lexists(tree / source) and (tree / destination).is_file()
    after = file_identities(tree)
    assert not before - after  # nothing deleted or replaced: every (inode, size) is still there
    lines = journal_of(tree, result.run_id)
    intents = {line['seq']: line for line in lines if line['op'] == 'intent'}
    dones = [line for line in lines if line['op'] == 'done']
    assert len(intents) == len(dones) == result.moved and all(line['ref'] < line['seq'] and line['ref'] in intents for line in dones)
    item = read_item_meta(tree / LA / '05주차' / '선대_5_2')
    assert (item.content_id, item.created_by, item.week_folder, item.stem, item.course_id) == ('cid-a', 'migration', '05주차', '선대_5_2', '100')
    assert read_item_meta(tree / LA / '기타' / '사라진 강의').module_item_id is None
    week = json.loads((tree / EQ / '05주차' / '.ssu' / 'materials.json').read_text())['entries']
    assert set(week) == {'cid-m0', 'cid-e', 'cid-f'} and week['cid-e']['created_by'] == 'migration'

    conflict = f'{LA}/선대_5_2 [cid-a].mp4'
    write_files(tree, [conflict])  # same role already exists in the new layout
    alias = tree / 'course-alias'
    alias.symlink_to(tree / LA, target_is_directory=True)
    assert count_legacy_files(alias) == 0
    alias.unlink()
    assert count_legacy_files(tree / LA) == count_legacy_files(tree / EQ) == count_legacy_files(tree / UI) == 0  # what stays is not offered again
    again = planned(runner, tree)

    assert again.moves == [] and {skip.path: skip.reason for skip in again.skips} == {**EXPECTED_SKIPS, conflict: 'destination_exists'}
    write_files(tree, [f'{LA}/선대_5_2 [cid-a].mp3'])  # a late old file of an already tidied lecture joins its folder
    second = runner.apply(planned(runner, tree))
    assert second.moved == 1 and (tree / LA / '05주차' / '선대_5_2' / '선대_5_2 음성.mp3').is_file()
    assert [run['run_id'] for run in runner.runs(tree)] == [second.run_id, result.run_id]
    write_files(tree, [f'{EQ}/자료 [cid-f] (2).txt'])  # transcript-like material copies still move
    assert count_legacy_files(tree / EQ) == 1
    assert runner.apply(planned(runner, tree)).moved == 1


def test_nfd_course_migration_respects_volume_normalization(tmp_path):
    course = unicodedata.normalize('NFD', '선형대수')
    (tmp_path / course).mkdir()
    names = [f'{course}/강의 [cid-a].mp4', f'{course}/강의 [cid-a].transcript.md']
    write_files(tmp_path, names)
    before = {name: (tmp_path / name).read_bytes() for name in names}
    runner = MigrationRunner()
    plan = runner.plan(tmp_path, FakeCatalog({LA: [video(1, '강의', 'cid-a')]}))
    normalized = tmp_path / unicodedata.normalize('NFC', course)
    if not normalized.exists() or not os.path.samestat(normalized.stat(), (tmp_path / course).stat()):
        assert plan.moves == []
        assert {skip.path: skip.reason for skip in plan.skips} == {name: 'course_not_matched' for name in names}
        assert count_legacy_files(tmp_path / course) == 0
        assert legacy_summary(tmp_path) == {
            'ok': True, 'root': str(tmp_path), 'legacy_file_count': 0, 'course_count': 0, 'courses': [],
        }
        result = runner.apply(plan)
        assert result.moved == 0 and result.run_id is None  # nothing moved, so no journal and nothing to undo
        assert {name: (tmp_path / name).read_bytes() for name in names} == before
        assert sorted(path.name for path in (tmp_path / course).iterdir()) == sorted(Path(name).name for name in names)
        return
    assert count_legacy_files(tmp_path / course) == 2
    assert legacy_summary(tmp_path)['legacy_file_count'] == 2
    result = runner.apply(plan)
    assert result.moved == 2
    path = migrations_dir(tmp_path) / f'{result.run_id}.jsonl'
    lines = read_journal(path)
    done = next(line for line in lines if line['op'] == 'done')
    path.write_text(''.join(json.dumps(line) + '\n' for line in lines if line is not done))
    runner._recover_journal(tmp_path, path)
    assert sum(line['op'] == 'done' for line in read_journal(path)) == 2
    undone = runner.undo(tmp_path, result.run_id)
    assert undone.restored == 2 and undone.skips == []
    assert {name: (tmp_path / name).read_bytes() for name in names} == before
    assert runner.runs(tmp_path)[0]['undo_available'] is False


def test_apply_skips_what_changed_since_the_preview_and_moves_the_rest(tree):
    runner = MigrationRunner()
    plan = planned(runner, tree)
    foreign = tree / LA / '05주차' / '선대_5_2'
    foreign.mkdir(parents=True)
    (foreign / '메모.txt').write_bytes(b'someone else')  # a folder of that name appeared after the preview: never written into
    os.utime(tree / LA / '선대_6_1 [cid-b].mp3', ns=(1, 1))  # same bytes, other mtime
    resized = tree / EQ / '5주차 타인 인식 [cid-e].file'
    old_mtime = resized.stat().st_mtime_ns
    resized.write_bytes(b'longer than before')
    os.utime(resized, ns=(old_mtime, old_mtime))  # other bytes, same mtime
    (tree / EQ / '7주차 1강 감성지능 리더십 [cid-d].mp4').unlink()
    (tree / EQ / '05주차' / '자료.pdf').write_bytes(b'someone else')  # the destination of a material appeared

    result = runner.apply(plan)

    assert {skip.path: skip.reason for skip in result.skips if skip.path not in EXPECTED_SKIPS} == {
        f'{LA}/선대_5_2 [cid-a].mp4': 'destination_exists',
        f'{LA}/선대_5_2 [cid-a].srt': 'destination_exists',
        f'{LA}/선대_5_2 [cid-a].txt': 'destination_exists',
        f'{LA}/선대_6_1 [cid-b].mp3': 'source_changed',
        f'{EQ}/5주차 타인 인식 [cid-e].file': 'source_changed',
        f'{EQ}/7주차 1강 감성지능 리더십 [cid-d].mp4': 'source_missing',
        f'{EQ}/자료 [cid-f].pdf': 'destination_exists',
    }
    assert result.moved == len(EXPECTED_MOVES) - 7
    assert os.listdir(foreign) == ['메모.txt'] and (tree / EQ / '05주차' / '자료.pdf').read_bytes() == b'someone else'
    assert (tree / LA / '선대_5_2 [cid-a].mp4').is_file()


@pytest.mark.parametrize('crash_at', ['after_link', 'after_unlink'])
def test_a_crash_in_the_middle_is_finished_by_the_next_preview_without_duplicates(tree, monkeypatch, crash_at):
    class Crash(BaseException):
        pass

    runner = MigrationRunner()
    plan = planned(runner, tree)
    before = file_identities(tree)
    real = library_migration.exclusive_move
    calls: list[str] = []

    def crashing(source, destination):
        calls.append(source.name)
        if len(calls) < 5:
            return real(source, destination)
        os.link(source, destination)
        if crash_at == 'after_unlink':
            os.unlink(source)
        raise Crash

    monkeypatch.setattr(library_migration, 'exclusive_move', crashing)
    with pytest.raises(Crash):
        runner.apply(plan)
    monkeypatch.setattr(library_migration, 'exclusive_move', real)

    resumed = planned(runner, tree)
    assert len(resumed.moves) == len(EXPECTED_MOVES) - 4 - (crash_at == 'after_unlink')  # only what remains
    runner.apply(resumed)

    for source, (destination, _) in EXPECTED_MOVES.items():
        assert not os.path.lexists(tree / source) and (tree / destination).is_file()
    assert not before - file_identities(tree) and all(count == 1 for count in file_identities(tree).values())  # no file exists twice
    assert planned(runner, tree).moves == []


def test_volumes_without_hard_links_use_check_then_rename_and_still_never_overwrite(tree, monkeypatch):
    def no_links(source, destination, **kwargs):
        raise OSError(errno.EPERM, 'Operation not permitted')

    runner = MigrationRunner()
    plan = planned(runner, tree)
    before = file_identities(tree)
    monkeypatch.setattr(os, 'link', no_links)

    result = runner.apply(plan)

    assert result.moved == len(EXPECTED_MOVES) and not before - file_identities(tree)  # renamed in place: same inodes
    occupied, other = tree / 'a.txt', tree / 'b.txt'
    occupied.write_bytes(b'first')
    other.write_bytes(b'second')
    assert library_migration.exclusive_move(other, occupied) == 'exists'
    assert occupied.read_bytes() == b'first' and other.read_bytes() == b'second'


def test_a_move_never_removes_the_only_name_of_a_file_and_takes_its_new_name_back_when_the_old_one_cannot_go(tmp_path):
    folder = tmp_path / 'case'
    folder.mkdir()
    (folder / 'Foo.mp4').write_bytes(b'only copy')
    if (folder / 'foo.mp4').exists():  # a case-insensitive volume: the "destination" is the file itself
        assert library_migration.exclusive_move(folder / 'Foo.mp4', folder / 'foo.mp4') == 'exists'
        assert (folder / 'Foo.mp4').read_bytes() == b'only copy'
    locked = tmp_path / 'locked'
    locked.mkdir()
    (locked / 'a.mp4').write_bytes(b'x')
    (tmp_path / 'elsewhere').mkdir()
    locked.chmod(0o500)  # the file can be linked from here but its name cannot be removed
    try:
        if os.access(locked, os.W_OK):
            pytest.skip('directory permissions are not enforced for this user')
        with pytest.raises(PermissionError):
            library_migration.exclusive_move(locked / 'a.mp4', tmp_path / 'elsewhere' / 'a.mp4')
    finally:
        locked.chmod(0o700)
    assert (locked / 'a.mp4').read_bytes() == b'x' and os.listdir(tmp_path / 'elsewhere') == []


def test_progress_is_throttled_to_ten_files_or_half_a_second(tmp_path):
    root = tmp_path / 'library'
    write_files(root, [f'강의/제목{number} [cid-{number}].mp4' for number in range(25)])
    catalog = FakeCatalog({'강의': [video(number, f'제목{number}', f'cid-{number}', lesson=number) for number in range(25)]})
    now = [0.0]
    ticks: list[tuple[int, int]] = []

    def record(event: dict[str, Any]) -> None:
        assert event['stage'] == 'tidy_moving'
        ticks.append((event['done'], event['total']))
        if event['done'] == 10:
            now[0] += 0.5  # half a second passes: the very next file reports too

    runner = MigrationRunner(clock=lambda: now[0])
    runner.apply(runner.plan(root, catalog), on_progress=record)

    assert ticks == [(0, 25), (10, 25), (11, 25), (21, 25), (25, 25)]


def test_a_cancelled_apply_stops_between_files_and_leaves_a_consistent_journal(tree):
    runner = MigrationRunner()
    plan = planned(runner, tree)
    counted = iter(range(1000))

    result = runner.apply(plan, should_cancel=lambda: next(counted) >= 6)

    assert result.moved == 6
    lines = journal_of(tree, result.run_id)
    assert sum(line['op'] == 'done' for line in lines) == 6 and sum(line['op'] == 'intent' for line in lines) == 6
    assert len(planned(runner, tree).moves) == len(EXPECTED_MOVES) - 6


# --------------------------------------------------------------------------- AC9: undo


def library_state(root: Path) -> dict[str, tuple[Any, ...]]:
    """Everything the user sees: folders, files with size/mtime/inode; app metadata files and the journal folder left out."""
    return {name: info for name, info in snapshot(root).items() if not name.startswith('.ssu') and not name.endswith('materials.json')}


def test_undo_puts_everything_back_removes_only_what_the_run_made_and_a_second_undo_does_nothing(tree):
    runner = MigrationRunner()
    original = library_state(tree)
    previous_materials = (tree / EQ / '05주차' / '.ssu' / 'materials.json').read_bytes()
    run = runner.apply(planned(runner, tree))

    checks = iter(range(10_000))

    undone = runner.undo(tree, run.run_id, should_cancel=lambda: next(checks) >= run.moved)  # asked to stop only after the last file

    assert undone.restored == run.moved and undone.skips == []
    assert library_state(tree) == original  # same names, inodes, mtimes; the folders the run created are gone, the others stay
    assert (tree / EQ / '05주차' / '.ssu' / 'materials.json').read_bytes() == previous_materials
    assert runner.runs(tree)[0]['undo_available'] is False
    assert runner.undo(tree, run.run_id).restored == 0
    assert planned(runner, tree).payload()['move_count'] == len(EXPECTED_MOVES)  # and the folder can be tidied again


def test_undo_leaves_changed_files_and_occupied_places_and_keeps_new_downloads_in_the_folder(tree):
    runner = MigrationRunner()
    run = runner.apply(planned(runner, tree))
    lecture = tree / LA / '05주차' / '선대_5_2'
    (lecture / '선대_5_2.srt').write_bytes(b'edited after the move')
    (tree / UI / '01주차' / 'UI디자인_1주차_202602.pdf').write_bytes(b'edited after the move')
    write_files(tree, [f'{LA}/선대_5_2 [cid-a].mp4'])  # the old name is taken again
    (lecture / '새로 받은 파일.mp4').write_bytes(b'new download')
    materials = tree / EQ / '05주차' / '.ssu' / 'materials.json'
    later = json.loads(materials.read_text())
    later['entries']['cid-later'] = {'file_name': '나중 자료.pdf', 'kind': 'material'}
    materials.write_text(json.dumps(later))  # a download after the run recorded one more material

    undone = runner.undo(tree, run.run_id)

    assert {skip.path: skip.reason for skip in undone.skips} == {
        f'{LA}/05주차/선대_5_2/선대_5_2.srt': 'source_changed',
        f'{UI}/01주차/UI디자인_1주차_202602.pdf': 'source_changed',
        f'{LA}/05주차/선대_5_2/선대_5_2.mp4': 'destination_exists',
    }
    assert undone.restored == run.moved - 3
    assert 'canvas-4732412' in json.loads((tree / UI / '01주차' / '.ssu' / 'materials.json').read_text())['entries']  # a material is still there: its week keeps the record
    assert (lecture / '새로 받은 파일.mp4').read_bytes() == b'new download' and read_item_meta(lecture).content_id == 'cid-a'  # the folder is still a lecture
    assert 'cid-later' in json.loads(materials.read_text())['entries']  # metadata changed since the run is left as it is
    assert (lecture / '선대_5_2 자막.txt').exists() is False and (tree / LA / '선대_5_2 [cid-a].txt').is_file()


def test_a_tampered_journal_cannot_reach_outside_the_library(tree, tmp_path):
    outside = tmp_path / 'outside.txt'
    outside.write_text('mine')
    (tree / 'link').symlink_to(tmp_path)
    directory = migrations_dir(tree)
    directory.mkdir(parents=True)
    run_id = '20261030T000000Z-abcdef'
    pdf, docx = tree / EQ / '자료 [cid-f].pdf', tree / LA / 'notes.docx'
    materials = tree / EQ / '05주차' / '.ssu' / 'materials.json'
    pdf_before, docx_before, materials_before = pdf.read_bytes(), docx.read_bytes(), materials.read_bytes()
    docx_info = os.lstat(docx)
    user_done = {'op': 'done', 'size': docx_info.st_size, 'mtime_ns': docx_info.st_mtime_ns, 'inode': docx_info.st_ino}
    info = os.lstat(outside)  # the journal even knows the outside file's exact size, mtime and inode
    base = {'op': 'done', 'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'inode': info.st_ino}
    rows = [
        {'seq': 1, 'op': 'header'},
        {**base, 'seq': 2, 'source': '../outside.txt', 'destination': 'library/x.txt'},
        {**base, 'seq': 3, 'source': 'a/b.txt', 'destination': '/etc/hosts'},
        {**base, 'seq': 4, 'source': 'link/new.txt', 'destination': 'link/outside.txt'},
        {'seq': 5, 'op': 'meta_written', 'destination': 'link/outside.txt', 'sha256': 'x'},
        {'seq': 6, 'op': 'mkdir', 'destination': '../'},
        # in-root user files named by their real size, mtime, inode and hash
        {**user_done, 'seq': 7, 'source': f'{LA}/zz.docx', 'destination': f'{LA}/notes.docx'},
        {'seq': 8, 'op': 'meta_written', 'destination': f'{EQ}/자료 [cid-f].pdf', 'sha256': hashlib.sha256(pdf.read_bytes()).hexdigest()},
        {'seq': 9, 'op': 'meta_written', 'destination': f'{LA}/notes.docx', 'sha256': hashlib.sha256(docx.read_bytes()).hexdigest(), 'previous_b64': base64.b64encode(b'replaced').decode()},
        {'seq': 10, 'op': 'meta_written', 'destination': f'{EQ}/05주차/.ssu/materials.json', 'sha256': hashlib.sha256(materials.read_bytes()).hexdigest(), 'previous_b64': 'A' * 400_000},
    ]
    (directory / f'{run_id}.jsonl').write_text('\n'.join(json.dumps(row) for row in rows) + '\nnot json\n')

    result = MigrationRunner().undo(tree, run_id)

    assert result.restored == 0 and len(result.skips) == 4
    assert outside.read_text() == 'mine' and not (tmp_path / 'new.txt').exists()
    assert (pdf.read_bytes(), docx.read_bytes(), materials.read_bytes()) == (pdf_before, docx_before, materials_before) and not (tree / LA / 'zz.docx').exists()
    for bad in ('../x', 'x', '20261030T000000Z-abcdeg'):
        with pytest.raises(TidyRunNotFound):
            MigrationRunner().undo(tree, bad)


# --------------------------------------------------------------------------- AC10: after the tidy-up


def test_after_a_tidy_up_the_next_artifact_job_does_nothing_and_the_course_detail_finds_the_files(rig_for):
    rig = rig_for(Lec(111, '선대_5_2', 'cid-a', lesson=1), Lec(112, '선대_6_1', 'cid-b', lesson=2), Lec(113, '자료', 'cid-m', kind='pdf', file_name='a.pdf', lesson=3))
    touch(rig.course_dir, '옛 제목 [cid-a].mp4', '옛 제목 [cid-a].srt', '옛 제목 [cid-a].mp3', '옛 제목 [cid-a].txt', '선대_6_1 [cid-b].mp4', '선대_6_1 [cid-b].mp3', '선대_6_1 [cid-b].transcript.txt')
    touch(rig.course_dir, '자료 [cid-m].pdf')
    items = rig.service.get_course_detail('u', 'p', course_id=42, course_name=COURSE).content_items
    runner = MigrationRunner()

    result = runner.apply(runner.plan(rig.root, FakeCatalog({COURSE: items})))

    assert result.moved == 8 and result.skips == []
    lecture = rig.course_dir / '05주차' / '선대_5_2'
    assert (lecture / '선대_5_2.mp4').is_file() and (lecture / '선대_5_2.srt').is_file()  # a player loads the subtitle by the shared stem
    downloaded = find_downloaded_files(rig.root, COURSE, ['cid-a', 'cid-b', 'cid-m'])
    assert downloaded['cid-a'] == str(lecture / '선대_5_2.mp4') and downloaded['cid-m'] == str(rig.course_dir / '05주차' / '자료.pdf')
    tree_before = rig.tree()

    job = rig.run(['mp4', 'mp3', 'txt'], [111, 112])

    assert job['status'] == 'succeeded' and stages(job, 'downloading', 'extracting_audio', 'transcribing') == []
    assert rig.http.media_downloads == 0 and rig.extractions == [] and rig.tree() == tree_before
