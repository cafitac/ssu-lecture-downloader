"""Dedup over both layouts: the index of a course folder, and downloads through the real service + pipeline.

Part 1 builds course folders by hand and asks `LibraryIndex` what it sees (new layout, old flat layout,
hostile metadata). Part 2 runs the artifact pipeline on a fake LMS, fake ffmpeg and the fake speech
engine, on temp trees only. No test touches a real download folder; waiting is done on job completion
(thread join), never on sleeps.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from test_api import FakeCredentialStore
from test_artifact_pipeline import events, finish
from test_service import FakeHttp, FakeResponse, ok_login

from ssu_lecture_downloader.core import postprocess_media, stt
from ssu_lecture_downloader.core.download import find_downloaded_files
from ssu_lecture_downloader.core.jobs import Job, JobRegistry, JobRunner
from ssu_lecture_downloader.core.library_layout import LibraryIndex
from ssu_lecture_downloader.core.library_meta import parse_legacy_name, read_item_meta
from ssu_lecture_downloader.core.lms_service import SsuLectureService
from ssu_lecture_downloader.core.postprocess_media import AudioExtractionResult
from ssu_lecture_downloader.core.stt_remote import RemoteSTTService
from ssu_lecture_downloader.core.stt_remote_config import RemoteSTTSettingsStore
from ssu_lecture_downloader.platforms.macos import MacOSPlatform
from ssu_lecture_downloader.service.api_models import ArtifactDownloadRequest
from ssu_lecture_downloader.service.app_context import AppContext, build_app_context
from ssu_lecture_downloader.service.artifacts import ArtifactPipeline
from ssu_lecture_downloader.service.credentials import CredentialPair

# --------------------------------------------------------------------------- helpers for part 1


def item_json(content_id: str, stem: str, week: str = '05주차', **overrides: Any) -> dict[str, Any]:
    return {
        'schema': 1, 'content_id': content_id, 'module_item_id': '1', 'course_id': '7', 'kind': 'lecture', 'lms_title': stem,
        'resolved_title': None, 'stem': stem, 'week_folder': week, 'created_by': 'download', 'app_version': '0.1.0',
        'layout_version': 1, 'created_at': '2026-10-30T00:00:00Z', 'updated_at': '2026-10-30T00:00:00Z', **overrides,
    }


def make_lecture(course: Path, content_id: str, stem: str, *, week: str = '05주차', files: tuple[str, ...] = (), meta: dict[str, Any] | None = None) -> Path:
    lecture = course / week / stem
    (lecture / '.ssu').mkdir(parents=True)
    (lecture / '.ssu' / 'item.json').write_text(json.dumps(meta if meta is not None else item_json(content_id, stem, week)), encoding='utf-8')
    for name in files:
        (lecture / name).write_bytes(b'data')
    return lecture


def touch(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_bytes(b'x')


# --------------------------------------------------------------------------- legacy name parser


@pytest.mark.parametrize(
    ('name', 'content_id', 'suffix', 'role', 'duplicate'),
    [
        ('선대_5_2 [68e66b6c1b816].mp4', '68e66b6c1b816', '.mp4', 'video', None),
        ('선대_6_1 [68feeb3f3b6dd].srt', '68feeb3f3b6dd', '.srt', 'srt', None),
        ('선대_6_1 [68feeb3f3b6dd].transcript.txt', '68feeb3f3b6dd', '.transcript.txt', 'text', None),
        ('선대_6_1 [68feeb3f3b6dd].transcript.md', '68feeb3f3b6dd', '.transcript.md', 'md', None),
        ('선대_6_1 [68feeb3f3b6dd].transcript.json', '68feeb3f3b6dd', '.transcript.json', 'json', None),
        ('선대_5_2 [68e66b6c1b816].txt', '68e66b6c1b816', '.txt', 'text_legacy', None),
        ('7주차 1강 감성지능 리더십 [65dbf62460d5a] (2).mp4', '65dbf62460d5a', '.mp4', 'video', 2),
        ('감성 [660650405b034].file', '660650405b034', '.file', 'material', None),
        ('UI디자인_1주차_202602 [canvas-4732412].pdf', 'canvas-4732412', '.pdf', 'material', None),
        ('녹음 [abc].MP3', 'abc', '.mp3', 'audio', None),
        ('제목 [대괄호] 안의 [abc].mp4', 'abc', '.mp4', 'video', None),  # the last [...] group wins
    ],
)
def test_legacy_names_are_attributed_to_a_content_id_and_role(name, content_id, suffix, role, duplicate):
    parsed = parse_legacy_name(name)
    assert parsed is not None
    assert (parsed.content_id, parsed.suffix, parsed.role, parsed.duplicate) == (content_id, suffix, role, duplicate)


@pytest.mark.parametrize(
    'name',
    ['notes.docx', 'no marker.mp4', 'lecture [content].mp4', '[abc].mp4', '제목 [abc].mp4.part', '제목 [a/b].mp4', '제목 [abc]mp4', '.DS_Store', '제목 [abc].toolongextension'],
)
def test_names_without_a_usable_marker_are_not_attributable(name):
    assert parse_legacy_name(name) is None


# --------------------------------------------------------------------------- index: new layout


def test_index_finds_lectures_through_item_json_wherever_the_folder_sits(tmp_path):
    course = tmp_path / 'c'
    make_lecture(course, 'cid-1', '첫 강의', files=('첫 강의.mp4', '첫 강의.srt', '첫 강의 자막.txt', '첫 강의 음성.mp3'))
    make_lecture(course, 'cid-2', '둘째 강의', week='기타')

    index = LibraryIndex.scan(course)

    assert set(index.lectures) == {'cid-1', 'cid-2'}
    entry = index.lectures['cid-1']
    assert entry.lecture_dir == course / '05주차' / '첫 강의'
    assert set(index.lecture_files(entry)) == {'video', 'srt', 'text', 'audio'}
    assert index.downloaded_path('cid-1') == course / '05주차' / '첫 강의' / '첫 강의.mp4'
    assert index.downloaded_path('cid-2') is None  # an empty lecture folder is not "downloaded"


def test_index_reads_material_entries_and_ignores_broken_ones(tmp_path):
    course = tmp_path / 'c'
    week = course / '05주차'
    touch(week, '자료.pdf', '메모.pdf')
    (week / '.ssu').mkdir()
    (week / '.ssu' / 'materials.json').write_text(json.dumps({
        'schema': 1,
        'entries': {
            'm-1': {'file_name': '자료.pdf', 'kind': 'material', 'title': '자료', 'created_by': 'download', 'updated_at': 'x'},
            'canvas-9': {'file_name': '메모.pdf', 'kind': 'canvas_file'},
            'bad-1': {'file_name': '../escape.pdf'},
            'bad-2': {'file_name': 'a/b.pdf'},
            'bad-3': {'file_name': '.hidden'},
            'bad-4': 'not an object',
        },
    }), encoding='utf-8')

    index = LibraryIndex.scan(course)

    assert set(index.materials) == {'m-1', 'canvas-9'}
    assert index.downloaded_path('m-1') == week / '자료.pdf'
    assert index.downloaded_path('canvas-9') == week / '메모.pdf'
    assert index.materials['canvas-9'].kind == 'canvas_file'


def test_a_material_entry_whose_file_is_missing_is_not_downloaded(tmp_path):
    course = tmp_path / 'c'
    (course / '05주차' / '.ssu').mkdir(parents=True)
    (course / '05주차' / '.ssu' / 'materials.json').write_text(json.dumps({'schema': 1, 'entries': {'m-1': {'file_name': '없음.pdf'}}}), encoding='utf-8')

    assert LibraryIndex.scan(course).downloaded_path('m-1') is None


# --------------------------------------------------------------------------- index: hostile metadata (AC3)


@pytest.mark.parametrize(
    'content',
    [
        '{not json',
        '[]',
        '"text"',
        json.dumps({**item_json('x', 'a'), 'schema': 2}),
        json.dumps({**item_json('x', 'a'), 'schema': '1'}),
        json.dumps({**item_json('x', 'a'), 'content_id': ''}),
        json.dumps({**item_json('x', 'a'), 'content_id': 5}),
        json.dumps({**item_json('x', 'a'), 'stem': '../x'}),
        json.dumps({**item_json('x', 'a'), 'stem': 'a/b'}),
        json.dumps({**item_json('x', 'a'), 'stem': '..'}),
        json.dumps({**item_json('x', 'a'), 'stem': '/etc'}),
        json.dumps({**item_json('x', 'a'), 'week_folder': '../../x'}),
        json.dumps({**item_json('x', 'a'), 'stem': 'a' * 300}),
        json.dumps({**item_json('x', 'a'), 'padding': 'p' * (70 * 1024)}),
    ],
    ids=lambda value: value[:30],
)
def test_corrupt_oversized_or_path_escaping_item_json_is_ignored_and_the_folder_is_foreign(tmp_path, content):
    course = tmp_path / 'c'
    lecture = course / '05주차' / 'a'
    (lecture / '.ssu').mkdir(parents=True)
    (lecture / '.ssu' / 'item.json').write_text(content, encoding='utf-8')

    index = LibraryIndex.scan(course)

    assert index.lectures == {}
    assert read_item_meta(lecture) is None


def test_metadata_that_is_a_symlink_or_lives_behind_one_is_not_followed(tmp_path):
    course = tmp_path / 'c'
    outside = tmp_path / 'outside'
    make_lecture(outside, 'stolen', 'victim')
    week = course / '05주차'
    week.mkdir(parents=True)
    (week / 'linked-lecture').symlink_to(outside / '05주차' / 'victim', target_is_directory=True)
    lecture = week / 'plain'
    lecture.mkdir()
    (lecture / '.ssu').symlink_to(outside / '05주차' / 'victim' / '.ssu', target_is_directory=True)
    lecture2 = week / 'file-link'
    (lecture2 / '.ssu').mkdir(parents=True)
    (lecture2 / '.ssu' / 'item.json').symlink_to(outside / '05주차' / 'victim' / '.ssu' / 'item.json')
    (course / 'linked-week').symlink_to(outside / '05주차', target_is_directory=True)

    index = LibraryIndex.scan(course)

    assert index.lectures == {}


def test_the_scan_does_not_look_deeper_than_week_and_lecture(tmp_path):
    course = tmp_path / 'c'
    deep = course / '05주차' / '강의' / '하위' / '더 깊이'
    (deep / '.ssu').mkdir(parents=True)
    (deep / '.ssu' / 'item.json').write_text(json.dumps(item_json('deep', '더 깊이')), encoding='utf-8')
    touch(course / '05주차' / '강의', '그냥 파일 [legacy-in-lecture].mp4')

    index = LibraryIndex.scan(course)

    assert index.lectures == {} and index.legacy == {}


def test_two_lecture_folders_claiming_one_content_id_resolve_to_the_first_by_name(tmp_path):
    course = tmp_path / 'c'
    make_lecture(course, 'same', 'b 강의')
    make_lecture(course, 'same', 'a 강의')
    assert LibraryIndex.scan(course).lectures['same'].lecture_dir.name == 'a 강의'


# --------------------------------------------------------------------------- index: legacy fallback (read only)


def test_legacy_files_are_found_by_their_marker_for_every_role(tmp_path):
    course = tmp_path / 'c'
    touch(
        course,
        '선대_5_2 [a1].mp4', '선대_5_2 [a1].srt', '선대_5_2 [a1].mp3', '선대_5_2 [a1].transcript.txt', '선대_5_2 [a1].transcript.md',
        '선대_5_2 [a1].transcript.json', '선대_6_1 [b2].txt', '선대_6_1 [b2].srt', '감성 [c3].file', '복사본 [a1] (2).mp4',
        '받다만 [d4].mp4.part', 'notes.docx', '.DS_Store',
    )

    index = LibraryIndex.scan(course)

    assert set(index.legacy) == {'a1', 'b2', 'c3'}
    assert set(index.legacy_paths('a1')) == {'video', 'srt', 'text', 'md', 'json', 'audio:mp3'}
    assert index.legacy_paths('a1')['text'].name.endswith('.transcript.txt')
    assert index.legacy_paths('b2')['text'].name == '선대_6_1 [b2].txt'  # a legacy .txt satisfies the transcript role
    assert index.legacy_material('c3') == course / '감성 [c3].file'
    assert index.legacy_files('a1').duplicates == [course / '복사본 [a1] (2).mp4']  # kept apart: never a role file


def test_a_transcript_txt_wins_over_a_plain_legacy_txt_of_the_same_id(tmp_path):
    course = tmp_path / 'c'
    touch(course, 'T [a1].txt', 'T [a1].transcript.txt')
    assert LibraryIndex.scan(course).legacy_paths('a1')['text'].name == 'T [a1].transcript.txt'


def test_downloaded_path_prefers_video_then_audio_then_text_then_material_then_srt(tmp_path):
    course = tmp_path / 'c'
    touch(course, 'v [id1].mp4', 'v [id1].srt', 'a [id2].mp3', 'a [id2].srt', 't [id3].transcript.txt', 't [id3].srt', 'm [id4].pdf', 's [id5].srt')
    index = LibraryIndex.scan(course)

    assert index.downloaded_path('id1').name == 'v [id1].mp4'
    assert index.downloaded_path('id2').name == 'a [id2].mp3'
    assert index.downloaded_path('id3').name == 't [id3].transcript.txt'
    assert index.downloaded_path('id4').name == 'm [id4].pdf'
    assert index.downloaded_path('id5').name == 's [id5].srt'
    assert index.downloaded_path('missing') is None


def test_the_new_layout_wins_over_a_legacy_file_of_the_same_lecture(tmp_path):
    course = tmp_path / 'c'
    touch(course, '옛 이름 [cid].mp4')
    make_lecture(course, 'cid', '새 이름', files=('새 이름.mp4',))
    assert LibraryIndex.scan(course).downloaded_path('cid') == course / '05주차' / '새 이름' / '새 이름.mp4'


def test_a_lone_copy_marked_n_still_counts_as_present_but_only_as_the_last_resort(tmp_path):
    course = tmp_path / 'c'
    touch(course, '복사본 [cid] (2).mp4')
    assert LibraryIndex.scan(course).downloaded_path('cid') == course / '복사본 [cid] (2).mp4'


def test_find_downloaded_files_keeps_its_contract_for_both_layouts_and_skips_unfinished_parts(tmp_path):
    course = tmp_path / '자료/구조'.replace('/', '_')
    touch(course, '1주차 [abc123].mp4', '2주차 [zzz999].mp4.part')
    make_lecture(course, 'new-1', '새 강의', files=('새 강의 자막.txt',))

    found = find_downloaded_files(tmp_path, '자료/구조', ['abc123', 'zzz999', 'new-1', '', 'nothing'])

    assert found == {'abc123': str(course / '1주차 [abc123].mp4'), 'new-1': str(course / '05주차' / '새 강의' / '새 강의 자막.txt')}
    assert find_downloaded_files(tmp_path, 'no such course', ['abc123']) == {}
    assert find_downloaded_files(tmp_path, '자료/구조', []) == {}


def test_scanning_never_writes_to_the_folder(tmp_path):
    course = tmp_path / 'c'
    touch(course, 'v [id1].mp4')
    make_lecture(course, 'cid', '강의')
    before = sorted((str(p.relative_to(tmp_path)), p.stat().st_mtime_ns, p.stat().st_ino) for p in tmp_path.rglob('*'))

    LibraryIndex.scan(course)
    find_downloaded_files(tmp_path, 'c', ['id1', 'cid'])

    assert before == sorted((str(p.relative_to(tmp_path)), p.stat().st_mtime_ns, p.stat().st_ino) for p in tmp_path.rglob('*'))


# --------------------------------------------------------------------------- part 2: a fake LMS behind the real service


@dataclass
class Lec:
    item_id: int
    title: str
    content_id: str
    week: int | None = 5
    module_name: str = '5주차'
    lesson: int = 1
    commons_title: str | None = None  # what the media server calls it (the module list title wins when both exist)
    kind: str = 'video'  # video | pdf | file
    file_name: str | None = None


class LayoutHttp(FakeHttp):
    """FakeHttp whose course has the given lectures; every download answers with 4 bytes."""

    def __init__(self, lectures: list[Lec]) -> None:
        super().__init__()
        self.lectures = lectures
        self.by_content_id = {lec.content_id: lec for lec in lectures}
        self.media_downloads = 0

    def modules(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, int | None], list[Lec]] = {}
        for lec in self.lectures:
            grouped.setdefault((lec.module_name, lec.week), []).append(lec)
        modules = []
        for number, ((name, week), members) in enumerate(grouped.items(), start=1):
            items = []
            for lec in members:
                inner: dict[str, Any] = {'content_id': lec.content_id, 'total_file_size': 4}
                if lec.kind == 'video':
                    inner.update(content_type='mp4', duration=60)
                else:
                    inner.update(content_type='file' if lec.kind == 'file' else 'pdf', file_name=lec.file_name)
                items.append({
                    'id': lec.item_id + 1000, 'module_item_id': lec.item_id, 'title': lec.title, 'content_type': 'attendance_item',
                    'completed': False, 'content_data': {'lesson_position': lec.lesson, 'item_content_data': inner},
                })
            module: dict[str, Any] = {'id': number, 'name': name, 'module_items': items}
            if week is not None:
                module['week_position'] = week
            modules.append(module)
        return modules

    def get(self, step, url, **kwargs):
        if step == 'list_course_modules':
            self.calls.append(('GET', step, url, kwargs))
            return FakeResponse(self.modules())
        if step == 'get_commons_content':
            self.calls.append(('GET', step, url, kwargs))
            content_id = parse_qs(urlparse(url).query)['content_id'][0]
            lec = self.by_content_id[content_id]
            return FakeResponse({'result': {'content_id': content_id, 'title': lec.commons_title or lec.title, 'download_url': 'https://commons.ssu.ac.kr/download?redacted=1', 'total_file_size': 4}})
        if step == 'download_media':
            self.media_downloads += 1
        return super().get(step, url, **kwargs)


COURSE = '자료/구조'
COURSE_DIR_NAME = '자료_구조'


class LayoutRig:
    """Real service + real pipeline on a fake LMS, fake ffmpeg and the built-in fake speech engine."""

    def __init__(self, tmp_path: Path, monkeypatch, lectures: list[Lec]) -> None:
        self.root = tmp_path
        self.course_dir = tmp_path / COURSE_DIR_NAME
        self.http = LayoutHttp(lectures)
        self.service = SsuLectureService(http=self.http, login_func=ok_login)
        remote = RemoteSTTService(RemoteSTTSettingsStore(tmp_path / 'stt.json', writer=MacOSPlatform()))
        self.ctx: AppContext = build_app_context(
            service=self.service, credentials=FakeCredentialStore(CredentialPair(username='u', password='p')),
            runner=JobRunner(), platform=MacOSPlatform(), default_output_dir=tmp_path, remote_stt=remote,
        )
        self.extractions: list[tuple[Path, Path]] = []
        monkeypatch.setattr(postprocess_media, 'extract_audio', self._extract)
        monkeypatch.setattr(postprocess_media, 'ensure_playable_mp4', self._ensure_mp4)
        self.registry = JobRegistry()

    def _extract(self, request, on_progress=None, should_cancel=None):
        output = request.output_path or postprocess_media.plan_audio_output_path(request.input_path, request.output_dir, request.format)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b'audio')
        self.extractions.append((request.input_path, output))
        return AudioExtractionResult(ok=True, input_path=request.input_path, output_path=output, file_size=5)

    @staticmethod
    def _ensure_mp4(path, *, overwrite=True, on_progress=None, should_cancel=None):
        return postprocess_media.VideoReconstructionResult(ok=True, input_path=path, output_path=path, file_size=4, reconstructed=False)

    def run(self, outputs: list[str], ids: list[int], **overrides: Any) -> dict[str, Any]:
        request = ArtifactDownloadRequest(
            **{'course_id': 42, 'course_name': COURSE, 'module_item_ids': ids, 'outputs': outputs, 'stt_provider': 'fake', **overrides},
        )

        def work(job: Job):
            return ArtifactPipeline(self.ctx, job, request, outputs, ('u', 'p'), self.root).run()

        return finish(self.ctx.runner.start(self.registry, work))

    def tree(self) -> list[str]:
        return sorted(str(path.relative_to(self.course_dir)) for path in self.course_dir.rglob('*')) if self.course_dir.exists() else []

    def files(self) -> list[str]:
        return [name for name in self.tree() if (self.course_dir / name).is_file()]


@pytest.fixture
def rig_for(tmp_path, monkeypatch):
    def build(*lectures: Lec) -> LayoutRig:
        return LayoutRig(tmp_path, monkeypatch, list(lectures))

    return build


def stages(snapshot: dict[str, Any], *wanted: str) -> list[str]:
    return [event['stage'] for event in events(snapshot) if event['stage'] in wanted]


WORK_STAGES = ('downloading', 'extracting_audio', 'transcribing')


# ---- AC3: what a job leaves in the lecture folder


def test_an_artifact_job_writes_the_visible_files_and_the_hidden_metadata(rig_for):
    rig = rig_for(Lec(111, '선대_5_2', 'cid-a'))

    snapshot = rig.run(['mp4', 'mp3', 'txt'], [111])

    assert snapshot['status'] == 'succeeded'
    lecture = rig.course_dir / '05주차' / '선대_5_2'
    assert rig.files() == [
        '05주차/선대_5_2/.ssu/item.json', '05주차/선대_5_2/.ssu/transcript.json', '05주차/선대_5_2/.ssu/transcript.md',
        '05주차/선대_5_2/선대_5_2 음성.mp3', '05주차/선대_5_2/선대_5_2 자막.txt', '05주차/선대_5_2/선대_5_2.mp4', '05주차/선대_5_2/선대_5_2.srt',
    ]
    item = json.loads((lecture / '.ssu' / 'item.json').read_text(encoding='utf-8'))
    assert item['schema'] == 1 and item['content_id'] == 'cid-a' and item['module_item_id'] == '111' and item['course_id'] == '42'
    assert (item['kind'], item['stem'], item['week_folder'], item['created_by']) == ('lecture', '선대_5_2', '05주차', 'download')
    assert item['lms_title'] == '선대_5_2' and item['resolved_title'] == '선대_5_2'  # Q3: names come from the list title
    assert 'cid-a' not in ' '.join(rig.files())  # no content id in any visible name
    assert rig.http.media_downloads == 1


def test_the_transcript_text_is_the_last_file_committed_and_a_failure_before_it_is_redone(rig_for, monkeypatch):
    rig = rig_for(Lec(111, '선대_5_2', 'cid-a'))
    real_write = stt._write_text_atomically
    written: list[str] = []
    fail_on_text = {'on': True}

    def write(path: Path, content: str) -> None:
        if path.name.endswith(' 자막.txt') and fail_on_text['on']:
            raise OSError('disk full')
        real_write(path, content)
        written.append(path.name)

    monkeypatch.setattr(stt, '_write_text_atomically', write)

    broken = rig.run(['txt'], [111])

    lecture = rig.course_dir / '05주차' / '선대_5_2'
    assert broken['status'] == 'failed' and broken['error_code'] == 'transcript_write_failed'
    assert not (lecture / '선대_5_2 자막.txt').exists()
    assert written == ['선대_5_2.srt', 'transcript.md', 'transcript.json']  # everything but the marker

    fail_on_text['on'] = False
    written.clear()
    redone = rig.run(['txt'], [111])

    assert redone['status'] == 'succeeded'
    assert stages(redone, 'transcribing') == ['transcribing'], 'the leftovers of the broken run did not count as a finished transcript'
    assert written == ['선대_5_2.srt', 'transcript.md', 'transcript.json', '선대_5_2 자막.txt']


# ---- AC4: no repeated work, in either layout


def test_a_second_job_on_new_layout_lectures_downloads_and_transcribes_nothing(rig_for):
    rig = rig_for(Lec(111, '강의 1', 'cid-1', lesson=1), Lec(112, '강의 2', 'cid-2', lesson=2))
    first = rig.run(['mp4', 'mp3', 'txt'], [111, 112])
    assert first['status'] == 'succeeded' and len(stages(first, 'downloading')) == 2
    before = rig.tree()
    inodes = {name: (rig.course_dir / name).stat().st_ino for name in rig.files()}

    second = rig.run(['mp4', 'mp3', 'txt'], [111, 112])

    assert second['status'] == 'succeeded'
    assert stages(second, *WORK_STAGES) == []
    assert rig.tree() == before and inodes == {name: (rig.course_dir / name).stat().st_ino for name in rig.files()}
    assert rig.http.media_downloads == 2


def test_lectures_that_exist_only_in_the_old_flat_layout_are_not_downloaded_or_transcribed_again(rig_for):
    rig = rig_for(Lec(111, '선대_5_2', 'cid-a', lesson=1), Lec(112, '선대_6_1', 'cid-b', lesson=2), Lec(113, '선대_6_2', 'cid-c', lesson=3))
    touch(rig.course_dir, '선대_5_2 [cid-a].mp4', '선대_5_2 [cid-a].mp3', '선대_5_2 [cid-a].transcript.txt')
    touch(rig.course_dir, '선대_6_1 [cid-b].mp4', '선대_6_1 [cid-b].mp3', '선대_6_1 [cid-b].txt')  # the oldest transcript name
    touch(rig.course_dir, '옛 제목 [cid-c].mp4', '옛 제목 [cid-c].mp3', '옛 제목 [cid-c].transcript.txt')  # saved under another title
    before = {name: (rig.course_dir / name).stat().st_ino for name in rig.files()}

    snapshot = rig.run(['mp4', 'mp3', 'txt'], [111, 112, 113])

    assert snapshot['status'] == 'succeeded'
    assert stages(snapshot, *WORK_STAGES) == []
    assert rig.http.media_downloads == 0
    assert rig.files() == sorted(before)  # nothing new, nothing renamed or moved
    assert {name: (rig.course_dir / name).stat().st_ino for name in rig.files()} == before


def test_native_plan_reports_both_layouts_as_skipped(rig_for):
    rig = rig_for(Lec(111, '새 구조 강의', 'cid-a', lesson=1), Lec(112, '옛 구조 강의', 'cid-b', lesson=2))
    rig.run(['mp4'], [111])
    touch(rig.course_dir, '옛 구조 강의 [cid-b].mp4')

    for item_id in (111, 112):
        plan = rig.service.native_download_plan('u', 'p', course_id=42, module_item_id=item_id, course_name=COURSE, output_dir=rig.root, conflict_strategy='skip')
        assert plan.result.skipped is True and plan.native_download is None
    assert rig.http.media_downloads == 1


def test_a_native_plan_for_a_new_lecture_prepares_the_folder_the_shell_writes_into(rig_for):
    rig = rig_for(Lec(111, '네이티브 강의', 'cid-n'))

    plan = rig.service.native_download_plan('u', 'p', course_id=42, module_item_id=111, course_name=COURSE, output_dir=rig.root, conflict_strategy='skip')

    target = Path(plan.native_download.output_path)
    assert target == rig.course_dir / '05주차' / '네이티브 강의' / '네이티브 강의.mp4'
    assert read_item_meta(target.parent).content_id == 'cid-n'  # the shell only writes bytes; the dedup record already exists
    assert not target.exists()


# ---- AC5: outputs without a video, and a legacy video used in place


@pytest.mark.parametrize(
    ('outputs', 'expected'),
    [
        (['mp3'], ['선대_5_2 음성.mp3']),
        (['txt'], ['선대_5_2 자막.txt', '선대_5_2.srt']),
        (['mp3', 'txt'], ['선대_5_2 음성.mp3', '선대_5_2 자막.txt', '선대_5_2.srt']),
    ],
)
def test_outputs_without_mp4_leave_no_video_in_the_lecture_folder(rig_for, outputs, expected):
    rig = rig_for(Lec(111, '선대_5_2', 'cid-a'))

    snapshot = rig.run(outputs, [111])

    assert snapshot['status'] == 'succeeded'
    visible = sorted(path.name for path in (rig.course_dir / '05주차' / '선대_5_2').iterdir() if not path.name.startswith('.'))
    assert visible == sorted(expected)
    assert (rig.course_dir / '05주차' / '선대_5_2' / '.ssu' / 'item.json').is_file()


def test_txt_only_with_a_legacy_video_writes_the_new_folder_and_leaves_the_legacy_video_alone(rig_for):
    rig = rig_for(Lec(111, '선대_5_2', 'cid-a'))
    legacy = rig.course_dir / '선대_5_2 [cid-a].mp4'
    touch(rig.course_dir, legacy.name)
    before = legacy.stat()

    snapshot = rig.run(['txt'], [111])

    assert snapshot['status'] == 'succeeded'
    lecture = rig.course_dir / '05주차' / '선대_5_2'
    assert (lecture / '선대_5_2 자막.txt').is_file() and (lecture / '선대_5_2.srt').is_file()
    assert not (lecture / '선대_5_2.mp4').exists()
    after = legacy.stat()
    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (before.st_ino, before.st_size, before.st_mtime_ns)
    assert rig.extractions == [(legacy, lecture / '선대_5_2 음성.mp3')]  # the legacy video is the source, in place
    assert rig.http.media_downloads == 0
    assert not (lecture / '선대_5_2 음성.mp3').exists(), 'intermediate audio is removed again as before'


# ---- AC2 end to end: same titles through parallel downloads


def test_two_same_title_lectures_downloaded_in_parallel_get_stable_numbered_folders(rig_for):
    rig = rig_for(Lec(112, '선대_5_2', 'cid-b', lesson=2), Lec(111, '선대_5_2', 'cid-a', lesson=1))

    snapshot = rig.run(['mp4'], [111, 112], max_parallel_downloads=4)

    assert snapshot['status'] == 'succeeded'
    assert (rig.course_dir / '05주차' / '선대_5_2' / '선대_5_2.mp4').is_file()
    assert (rig.course_dir / '05주차' / '선대_5_2 (2)' / '선대_5_2 (2).mp4').is_file()
    assert read_item_meta(rig.course_dir / '05주차' / '선대_5_2').content_id == 'cid-a'
    assert read_item_meta(rig.course_dir / '05주차' / '선대_5_2 (2)').content_id == 'cid-b'

    # a job that selects only the second lecture still addresses its own folder
    again = rig.run(['mp4'], [112])
    assert again['status'] == 'succeeded' and stages(again, 'downloading') == []


def test_a_foreign_folder_with_the_planned_name_pushes_the_new_lecture_to_a_numbered_folder(rig_for):
    rig = rig_for(Lec(111, '선대_5_2', 'cid-a'))
    foreign = rig.course_dir / '05주차' / '선대_5_2'
    touch(foreign, '내 메모.txt')

    snapshot = rig.run(['mp4'], [111])

    assert snapshot['status'] == 'succeeded'
    assert sorted(path.name for path in foreign.iterdir()) == ['내 메모.txt']
    assert (rig.course_dir / '05주차' / '선대_5_2 (2)' / '선대_5_2 (2).mp4').is_file()


def test_a_lecture_keeps_its_folder_when_the_lms_later_moves_it_to_another_week(rig_for):
    lecture = Lec(111, '이사가는 강의', 'cid-a', week=4, module_name='4주차')
    rig = rig_for(lecture)
    rig.run(['mp4'], [111])
    lecture.week, lecture.module_name = 5, '5주차'
    rig.service.clear_cache()

    again = rig.run(['mp4'], [111])

    assert again['status'] == 'succeeded' and stages(again, 'downloading') == []
    assert (rig.course_dir / '04주차' / '이사가는 강의' / '이사가는 강의.mp4').is_file()
    assert not (rig.course_dir / '05주차').exists()


def test_week_folder_comes_from_the_module_name_when_the_lms_gives_no_week_number(rig_for):
    rig = rig_for(
        Lec(111, '번호 없는 모듈', 'cid-a', week=None, module_name='오리엔테이션', lesson=1),
        Lec(112, '이름 속 번호', 'cid-b', week=None, module_name='Week 3', lesson=2),
        Lec(113, '모듈 이름 없음', 'cid-c', week=None, module_name='', lesson=3),
    )

    rig.run(['mp4'], [111, 112, 113])

    assert sorted(path.name for path in rig.course_dir.iterdir()) == ['03주차', '기타', '오리엔테이션']


# ---- AC6: materials


def test_a_material_lands_in_its_week_folder_with_an_entry_and_is_skipped_the_second_time(rig_for):
    rig = rig_for(Lec(111, '강의 1', 'cid-v', lesson=1), Lec(112, '강의자료', 'cid-pdf', lesson=2, kind='pdf', file_name='week5.pdf'))

    first = rig.service.batch_download_lectures('u', 'p', course_id=42, course_name=COURSE, output_dir=rig.root, dry_run=False, module_item_ids=[112], conflict_strategy='skip')
    again = rig.service.batch_download_lectures('u', 'p', course_id=42, course_name=COURSE, output_dir=rig.root, dry_run=False, module_item_ids=[112], conflict_strategy='skip')

    assert first.items[0].written is True
    assert first.items[0].output_path == str(rig.course_dir / '05주차' / '강의자료.pdf')
    assert (rig.course_dir / '05주차' / '강의자료.pdf').read_bytes() == b'1234'
    entries = json.loads((rig.course_dir / '05주차' / '.ssu' / 'materials.json').read_text(encoding='utf-8'))['entries']
    assert entries['cid-pdf']['file_name'] == '강의자료.pdf' and entries['cid-pdf']['kind'] == 'material'
    assert again.items[0].skipped is True and again.items[0].written is False
    assert rig.http.media_downloads == 1
    assert find_downloaded_files(rig.root, COURSE, ['cid-pdf']) == {'cid-pdf': str(rig.course_dir / '05주차' / '강의자료.pdf')}


def test_a_file_material_takes_its_extension_from_the_lms_file_name(rig_for):
    rig = rig_for(Lec(112, '감성지능 자료', 'cid-file', kind='file', file_name='감성지능.pptx'))

    result = rig.service.download_lecture('u', 'p', course_id=42, module_item_id=112, course_name=COURSE, output_dir=rig.root, dry_run=False)

    assert Path(result.output_path) == rig.course_dir / '05주차' / '감성지능 자료.pptx'  # not `.file` (O2)
    assert Path(result.output_path).read_bytes() == b'1234'


def test_a_file_material_without_a_usable_file_name_keeps_the_generic_extension(rig_for):
    rig = rig_for(Lec(112, '이름 없는 자료', 'cid-file', kind='file', file_name=None))

    result = rig.service.download_lecture('u', 'p', course_id=42, module_item_id=112, course_name=COURSE, output_dir=rig.root, dry_run=True)

    assert Path(result.output_path).name == '이름 없는 자료.file'


def test_same_title_materials_in_a_week_are_numbered_by_lesson_order(rig_for):
    rig = rig_for(
        Lec(112, '자료', 'cid-b', lesson=2, kind='pdf', file_name='b.pdf'),
        Lec(111, '자료', 'cid-a', lesson=1, kind='pdf', file_name='a.pdf'),
    )

    result = rig.service.batch_download_lectures('u', 'p', course_id=42, course_name=COURSE, output_dir=rig.root, dry_run=True, module_item_ids=[111, 112])

    assert {item.module_item_id: Path(item.output_path).name for item in result.items} == {111: '자료.pdf', 112: '자료 (2).pdf'}


# ---- the internal plan handed to the pipeline (API payload unchanged)


def test_the_batch_result_carries_library_paths_but_the_payload_does_not(rig_for):
    from ssu_lecture_downloader.service.payloads import batch_payload

    rig = rig_for(Lec(111, '선대_5_2', 'cid-a'))

    result = rig.service.batch_download_lectures('u', 'p', course_id=42, course_name=COURSE, output_dir=rig.root, dry_run=True, module_item_ids=[111])

    paths = result.library_paths['111']
    assert paths.lecture_dir == rig.course_dir / '05주차' / '선대_5_2'
    assert paths.targets['video'] == paths.lecture_dir / '선대_5_2.mp4'
    payload = batch_payload(result, [])
    assert 'library_paths' not in payload and set(payload) == {
        'ok', 'error_code', 'dry_run', 'course_id', 'course_name', 'total_count', 'success_count', 'failure_count', 'items', 'progress',
    }
    assert rig.tree() == [], 'planning writes nothing'


def test_planning_a_course_changes_no_byte_of_an_existing_tree(rig_for):
    rig = rig_for(Lec(111, '선대_5_2', 'cid-a'), Lec(112, '선대_6_1', 'cid-b', lesson=2))
    touch(rig.course_dir, '선대_5_2 [cid-a].mp4')
    make_lecture(rig.course_dir, 'cid-b', '선대_6_1', files=('선대_6_1.mp4',))
    snapshot_before = sorted((str(p), p.stat().st_mtime_ns, p.stat().st_ino, p.stat().st_size) for p in rig.root.rglob('*'))

    rig.service.batch_download_lectures('u', 'p', course_id=42, course_name=COURSE, output_dir=rig.root, dry_run=True, conflict_strategy='skip')

    assert snapshot_before == sorted((str(p), p.stat().st_mtime_ns, p.stat().st_ino, p.stat().st_size) for p in rig.root.rglob('*'))


# ---- AC14: the standalone audio job keeps its naming; an explicit output path is honoured


def test_extract_audio_writes_the_explicit_output_path_and_the_standalone_naming_is_unchanged(tmp_path, monkeypatch):
    import subprocess

    from ssu_lecture_downloader.core.postprocess_media import AudioExtractionRequest, extract_audio

    video = tmp_path / '선대_5_2.mp4'
    video.write_bytes(b'video')
    monkeypatch.setattr(postprocess_media, 'bundled_or_system_ffmpeg', lambda: 'ffmpeg')

    def fake_run(command, **kwargs):
        Path(command[-1]).write_bytes(b'audio')
        return subprocess.CompletedProcess(command, 0, '', '')

    monkeypatch.setattr(postprocess_media, '_run_ffmpeg', lambda command, should_cancel=None, **kw: fake_run(command))

    standalone = extract_audio(AudioExtractionRequest(input_path=video, format='mp3'))
    explicit_target = tmp_path / '05주차' / '선대_5_2' / '선대_5_2 음성.mp3'
    explicit = extract_audio(AudioExtractionRequest(input_path=video, format='mp3', output_path=explicit_target))

    assert standalone.output_path == tmp_path / 'transcripts' / '선대_5_2.mp3'
    assert explicit.ok is True and explicit.output_path == explicit_target and explicit_target.read_bytes() == b'audio'
