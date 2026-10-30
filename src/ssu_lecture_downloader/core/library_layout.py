"""The download folder layout: names, collisions, per-lecture paths and the dedup index.

Layout (design 07):

    <download root>/<course>/<NN주차 | module name | 기타>/<lecture stem>/
        <stem>.mp4  <stem>.srt  <stem> 자막.txt  <stem> 음성.mp3  .ssu/{item.json,transcript.md,transcript.json}
    <download root>/<course>/<week>/<material title>.<ext>  +  <week>/.ssu/materials.json

`LibraryLayout` decisions are pure functions of (LMS item, disk state). Nothing here moves, renames
or deletes a file: files of the old flat layout (`<title> [<content id>].<ext>`) are only read, through
`LibraryIndex`, so a lecture that exists in either layout is never downloaded or transcribed again.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

from ssu_lecture_downloader.core.errors import LibraryConflict, PathTooLong
from ssu_lecture_downloader.core.library_meta import (
    AUDIO_EXTENSIONS,
    META_DIR_NAME,
    VIDEO_EXTENSIONS,
    ItemMeta,
    LegacyFiles,
    MaterialMeta,
    parse_legacy_name,
    read_item_meta,
    read_materials_meta,
    reserve_material_copy,
    upsert_material_meta,
    write_item_meta,
)

logger = logging.getLogger(__name__)

OTHER_WEEK_FOLDER = '기타'
FALLBACK_STEM = '강의'
FALLBACK_MATERIAL_STEM = '자료'
AUDIO_SUFFIX = ' 음성'
TEXT_SUFFIX = ' 자막.txt'
TRANSCRIPT_MD = 'transcript.md'
TRANSCRIPT_JSON = 'transcript.json'
ROLES = ('video', 'audio', 'srt', 'text', 'md', 'json')
# Extensions whose meaning is "the file type is not known": the LMS `file_name` extension wins (design P10 / O2).
GENERIC_EXTENSIONS = frozenset({'', 'file', 'bin', 'content', 'attachment', 'unknown', 'material', 'octet-stream'})

_UNSAFE_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]+')
_WHITESPACE = re.compile(r'\s+')
_WEEK_IN_NAME = re.compile(r'(\d{1,2})\s*주\s*차')
_WEEK_IN_ENGLISH = re.compile(r'(?i)\bweek\s*(\d{1,2})\b')
_MAX_WEEK_NUMBER = 999
_WINDOWS_RESERVED = frozenset({'CON', 'PRN', 'AUX', 'NUL', *(f'COM{n}' for n in range(1, 10)), *(f'LPT{n}' for n in range(1, 10))})
# Longest decoration a name gets after the stem: ' (99)' for a collision, ' 자막.txt' / ' 음성.mp3' for an output.
_COLLISION_SUFFIX_CHARS = len(' (99)')
_LONGEST_OUTPUT_SUFFIX_CHARS = len(TEXT_SUFFIX)


# --------------------------------------------------------------------------- name rules


def _cut_bytes(text: str, max_bytes: int) -> str:
    """`text` cut to at most `max_bytes` UTF-8 bytes, on a character boundary."""
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode('utf-8', errors='ignore')


@dataclass(frozen=True)
class PathRules:
    """What a platform allows in a file or folder name (`platform.path_rules()`).

    macOS: 255 bytes per name. Windows adds reserved device names and a total path budget. Rules for
    Windows are designed and tested now; the service only activates them with the Windows platform.
    """

    max_name_bytes: int = 255
    stem_max_bytes: int = 150
    folder_max_bytes: int = 120
    reserved_names: frozenset[str] = frozenset()
    max_path_chars: int | None = None
    min_stem_chars: int = 20

    def safe_name(self, text: str, *, fallback: str = FALLBACK_STEM, max_bytes: int | None = None) -> str:
        """NFC, unsafe characters -> `_`, whitespace collapsed, dots/spaces/underscores trimmed, byte-capped."""
        limit = min(max_bytes or self.max_name_bytes, self.max_name_bytes)
        cleaned = _UNSAFE_CHARS.sub('_', unicodedata.normalize('NFC', text))
        cleaned = _WHITESPACE.sub(' ', cleaned).strip(' ._')
        cleaned = _cut_bytes(cleaned, limit).strip(' ._')
        if not cleaned:
            return fallback
        if self.reserved_names:
            head, dot, tail = cleaned.partition('.')
            if head.strip().upper() in self.reserved_names:
                cleaned = _cut_bytes(f'{head}_', limit) + dot + tail
        return cleaned

    def stem(self, title: str, *, fallback: str = FALLBACK_STEM) -> str:
        return self.safe_name(title, fallback=fallback, max_bytes=self.stem_max_bytes)

    def fit_stem(self, stem: str, *, base_dir: Path, week_folder: str) -> str:
        """Shorten `stem` until the longest file planned for it fits `max_path_chars` (no-op without a budget)."""
        if self.max_path_chars is None:
            return stem
        # Longest path planned for the stem: <base>/<week>/<stem> (99)/<stem> (99) 자막.txt.part (the stem is in both names).
        fixed = len(str(base_dir)) + len(week_folder) + 3 + 2 * _COLLISION_SUFFIX_CHARS + _LONGEST_OUTPUT_SUFFIX_CHARS + len('.part')
        allowed = (self.max_path_chars - fixed) // 2  # the stem is in the folder name and in the file name
        if allowed < self.min_stem_chars:
            raise PathTooLong(f'path budget of {self.max_path_chars} characters cannot be met')
        if len(stem) <= allowed:
            return stem
        return stem[:allowed].strip(' ._') or stem[: self.min_stem_chars]


MACOS_RULES = PathRules()
WINDOWS_RULES = PathRules(reserved_names=_WINDOWS_RESERVED, max_path_chars=240)
DEFAULT_RULES = MACOS_RULES


def _week_number(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isascii() and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    return number if 0 <= number <= _MAX_WEEK_NUMBER else None


def _format_week(number: int) -> str:
    return f'{number:02d}주차'


def week_number_in_text(text: str | None) -> int | None:
    """The week number named in `text` (`5주차`, `5 주차`, `Week 5`), else None."""
    if not text:
        return None
    normalized = unicodedata.normalize('NFC', text)
    for pattern in (_WEEK_IN_NAME, _WEEK_IN_ENGLISH):
        match = pattern.search(normalized)
        if match:
            return int(match.group(1))
    return None


def week_folder(week_position: object, module_name: str | None, rules: PathRules = DEFAULT_RULES) -> str:
    """`NN주차` from the LMS week, else the number in the module name, else the module name itself, else `기타` (Q1)."""
    number = _week_number(week_position)
    if number is None:
        number = week_number_in_text(module_name)
    if number is not None:
        return _format_week(number)
    name = rules.safe_name(module_name or '', fallback='', max_bytes=rules.folder_max_bytes)
    return name or OTHER_WEEK_FOLDER


def lecture_stem(title: str | None, rules: PathRules = DEFAULT_RULES) -> str:
    return rules.stem(title or '', fallback=FALLBACK_STEM)


VIDEO_EXTENSION_ALIASES = {
    'everlec': 'mp4',
    'movie': 'mp4',
    'readystream': 'mp4',
    'zoom': 'mp4',
    'video': 'mp4',
    'mp4': 'mp4',
}


def item_kind_for_extension(extension: str | None) -> str:
    """`lecture` for an extension the media server uses for video (aliases included), else `material`."""
    ext = (extension or '').strip().lower().lstrip('.').rsplit('/', 1)[-1]  # aliases such as `readystream` are longer than a file extension
    return 'lecture' if ext in VIDEO_EXTENSION_ALIASES or ext in VIDEO_EXTENSIONS else 'material'


def clean_extension(extension: str | None, default: str = '') -> str:
    text = (extension or '').strip().lower().lstrip('.')
    if '/' in text:
        text = text.rsplit('/', 1)[-1]
    return text if text.isascii() and text.isalnum() and len(text) <= 10 else default


def material_extension(content_type: str | None, file_name: str | None) -> str:
    """Extension of a material: the LMS content type, or the `file_name` extension when the type is generic (O2)."""
    declared = clean_extension(content_type)
    if declared not in GENERIC_EXTENSIONS:
        return declared
    base = (file_name or '').rsplit('/', 1)[-1].strip()
    stem, dot, extension = base.rpartition('.')
    named = clean_extension(extension) if dot and stem else ''
    if named and named not in GENERIC_EXTENSIONS:
        return named
    return declared or 'file'


def visible_names(stem: str, video_ext: str = 'mp4', audio_ext: str = 'mp3') -> dict[str, str]:
    """The four visible file names of a lecture (no content id in any of them)."""
    return {
        'video': f'{stem}.{video_ext}',
        'srt': f'{stem}.srt',
        'text': f'{stem}{TEXT_SUFFIX}',
        'audio': f'{stem}{AUDIO_SUFFIX}.{audio_ext}',
    }


def collision_name(stem: str, number: int) -> str:
    return stem if number <= 1 else f'{stem} ({number})'


def _fold(name: str) -> str:
    return unicodedata.normalize('NFC', name).casefold()


def _order_key(value: object) -> tuple[int, int]:
    number = _week_number(value)
    if number is None and isinstance(value, float) and value.is_integer():
        number = int(value)
    return (0, number) if number is not None else (1, 0)


# --------------------------------------------------------------------------- per-course lock

_course_locks: dict[str, threading.RLock] = {}
_course_locks_guard = threading.Lock()


def course_lock(course_dir: Path) -> threading.RLock:
    """One lock per course folder: lecture folder creation and metadata writes take it."""
    key = str(course_dir)
    with _course_locks_guard:
        return _course_locks.setdefault(key, threading.RLock())


# --------------------------------------------------------------------------- lecture / material paths


@dataclass(frozen=True, eq=False)
class LecturePaths:
    """Where one lecture's files go (`targets`) and which of them already exist (new layout first, then legacy).

    `video`, `audio`, `srt`, `text`, `md`, `json` are the files to READ: the existing one, else the
    target. Writers use `targets` (new outputs always go to the new layout; a legacy file is never
    moved, renamed or deleted by a download job).
    """

    lecture_dir: Path
    stem: str
    targets: Mapping[str, Path]
    legacy: Mapping[str, Path] = field(default_factory=dict)
    meta: ItemMeta | None = None  # seed for `.ssu/item.json`; None: nothing to record (existing lecture, standalone)
    video_ext: str = 'mp4'
    audio_format: str = 'mp3'
    course_dir: Path | None = None

    # ---- resolution
    def current(self, role: str) -> Path:
        target = self.targets[role]
        if target.exists():
            return target
        if role == 'video':
            for extension in sorted(VIDEO_EXTENSIONS):
                candidate = self.lecture_dir / f'{self.stem}.{extension}'
                if candidate.exists():
                    return candidate
            old = self.legacy.get('video')
        elif role == 'audio':
            old = self.legacy.get(f'audio:{self.audio_format}')
        else:
            old = self.legacy.get(role)
        return old if old is not None and old.exists() else target

    def exists(self, role: str) -> bool:
        return self.current(role).exists()

    @property
    def video(self) -> Path:
        return self.current('video')

    @property
    def audio(self) -> Path:
        return self.current('audio')

    @property
    def srt(self) -> Path:
        return self.current('srt')

    @property
    def text(self) -> Path:
        return self.current('text')

    @property
    def md(self) -> Path:
        return self.current('md')

    @property
    def json(self) -> Path:
        return self.current('json')

    @property
    def transcript_exists(self) -> bool:
        # `<stem> 자막.txt` (or the legacy transcript text) is committed last, so it marks a complete set.
        return self.exists('text')

    def source_video(self) -> Path | None:
        path = self.current('video')
        return path if path.exists() else None

    # ---- variants
    def with_video_extension(self, extension: str | None) -> LecturePaths:
        ext = clean_extension(extension, 'mp4') or 'mp4'
        if ext == self.video_ext:
            return self
        return replace(self, video_ext=ext, targets={**self.targets, 'video': self.lecture_dir / f'{self.stem}.{ext}'})

    def with_audio_format(self, audio_format: str) -> LecturePaths:
        if audio_format == self.audio_format:
            return self
        return replace(self, audio_format=audio_format, targets={**self.targets, 'audio': self.lecture_dir / f'{self.stem}{AUDIO_SUFFIX}.{audio_format}'})

    def with_resolved_title(self, title: str | None) -> LecturePaths:
        if self.meta is None or not title or self.meta.resolved_title == title:
            return self
        return replace(self, meta=replace(self.meta, resolved_title=title))

    # ---- writing
    def prepare(self) -> None:
        """Create the lecture folder and (for a new lecture) its `.ssu/item.json`, under the course lock.

        Idempotent. Raises `LibraryConflict` when the planned folder now holds someone else's files.
        """
        directory = self.lecture_dir
        with course_lock(self.course_dir or directory.parent):
            created = False
            try:
                directory.mkdir(parents=True)
                created = True
            except FileExistsError:
                pass
            if self.meta is not None:
                if not created:
                    existing = read_item_meta(directory)
                    if existing is None and any(directory.iterdir()):
                        raise LibraryConflict(f'{directory.name} is not a lecture folder of this app')
                    if existing is not None and existing.content_id != self.meta.content_id:
                        raise LibraryConflict(f'{directory.name} belongs to another lecture')
                write_item_meta(directory, self.meta)
            for target in self.targets.values():
                if target.parent != directory:
                    target.parent.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True, eq=False)
class MaterialPaths:
    """Where one material (PDF, `.file`, Canvas attachment) goes: directly under its week folder."""

    week_dir: Path
    file_name: str
    content_id: str
    kind: str = 'material'
    title: str | None = None
    legacy: Path | None = None
    course_dir: Path | None = None
    # (week folder, file name, content id) -> does the course plan give that name to ANOTHER material? Set by CourseLayout.
    claimed_by_other: Callable[[str, str, str], bool] | None = None

    @property
    def target(self) -> Path:
        return self.week_dir / self.file_name

    @property
    def video(self) -> Path:  # what a download job writes
        return self.target

    @property
    def meta_name(self) -> str:
        return self.file_name

    def name_taken(self, file_name: str) -> bool:
        """Is `file_name` in this week folder reserved: another material's name or copy, one of my own copies, or a name the course plan gave to another material?"""
        wanted = _fold(file_name)
        for other, entry in read_materials_meta(self.week_dir).items():
            held = entry.copies if other == self.content_id else entry.names()  # my own recorded file is mine; my copies are not free either
            if wanted in {_fold(name) for name in held}:
                return True
        return bool(self.claimed_by_other and self.claimed_by_other(self.week_dir.name, file_name, self.content_id))

    def _mapped_here(self) -> bool:
        """Is the planned file name recorded in the week's materials.json for THIS content id?"""
        entry = read_materials_meta(self.week_dir).get(self.content_id)
        return entry is not None and _fold(entry.file_name) == _fold(self.file_name)

    def source_video(self) -> Path | None:
        """The material that is already here: the planned file when materials.json maps it to this id, else the old flat-layout file."""
        if self.target.exists() and self._mapped_here():
            return self.target
        return self.legacy if self.legacy is not None and self.legacy.exists() else None

    def prepare(self) -> None:
        """Create the week folder and record the material in `<week>/.ssu/materials.json` before its bytes arrive.

        Re-reads materials.json under the course lock: a name held by another content id (any case), or a file
        of that name nobody recorded for this id, is a conflict and is never written over.
        """
        with course_lock(self.course_dir or self.week_dir.parent):
            self.week_dir.mkdir(parents=True, exist_ok=True)
            entries = read_materials_meta(self.week_dir)
            wanted = _fold(self.file_name)
            if any(other != self.content_id and wanted in {_fold(name) for name in entry.names()} for other, entry in entries.items()):
                raise LibraryConflict(f'{self.file_name} belongs to another material')
            if self.target.exists() and not self._mapped_here():
                raise LibraryConflict(f'{self.file_name} is not a material of this app')
            recorded = entries.get(self.content_id)
            if recorded is not None and _fold(recorded.file_name) != wanted and (self.week_dir / recorded.file_name).exists():
                reserve_material_copy(self.week_dir, self.content_id, self.file_name)  # an extra copy (rename): reserved, the recorded file stays authoritative
                return
            upsert_material_meta(self.week_dir, self.content_id, MaterialMeta(file_name=self.file_name, kind=self.kind, title=self.title))


# --------------------------------------------------------------------------- index


@dataclass(frozen=True)
class LectureEntry:
    lecture_dir: Path
    meta: ItemMeta


@dataclass(frozen=True)
class MaterialEntry:
    week_dir: Path
    file_name: str
    kind: str
    title: str | None
    copies: tuple[str, ...] = ()

    @property
    def path(self) -> Path:
        return self.week_dir / self.file_name


class LibraryIndex:
    """What exists in one course folder, in the new layout and (read-only) in the old flat layout.

    One walk of depth 2: `<course>/<week>/<lecture>/.ssu/item.json`, `<course>/<week>/.ssu/materials.json` and
    the files directly inside `<course>` (parsed by the legacy `[content id]` name parser). Symlinks are
    not followed; unreadable or invalid metadata is ignored.
    """

    def __init__(self, course_dir: Path) -> None:
        self.course_dir = course_dir
        self.lectures: dict[str, LectureEntry] = {}
        self.materials: dict[str, MaterialEntry] = {}
        self.legacy: dict[str, LegacyFiles] = {}
        self.ignored = 0

    @classmethod
    def scan(cls, course_dir: Path) -> LibraryIndex:
        index = cls(course_dir)
        try:
            children = sorted(os.scandir(course_dir), key=lambda entry: entry.name)
        except OSError:
            return index
        for child in children:
            try:
                if child.is_symlink():
                    continue
                if child.is_dir(follow_symlinks=False):
                    if not child.name.startswith('.'):
                        index._scan_week(Path(child.path))
                elif child.is_file(follow_symlinks=False):
                    index._add_legacy(child.name, Path(child.path))
            except OSError:
                index.ignored += 1
        logger.debug(
            'library index scan: course=%s lectures=%d materials=%d legacy_ids=%d ignored=%d',
            course_dir.name, len(index.lectures), len(index.materials), len(index.legacy), index.ignored,
        )
        return index

    def _scan_week(self, week_dir: Path) -> None:
        for content_id, entry in sorted(read_materials_meta(week_dir).items()):
            self.materials.setdefault(content_id, MaterialEntry(week_dir, entry.file_name, entry.kind, entry.title, entry.copies))
        try:
            children = sorted(os.scandir(week_dir), key=lambda entry: entry.name)
        except OSError:
            return
        for child in children:
            try:
                if child.name.startswith('.') or child.is_symlink() or not child.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            meta = read_item_meta(Path(child.path))
            if meta is None:
                self.ignored += 1
                continue
            self.lectures.setdefault(meta.content_id, LectureEntry(Path(child.path), meta))

    def _add_legacy(self, name: str, path: Path) -> None:
        parsed = parse_legacy_name(name)
        if parsed is None:
            return
        files = self.legacy.setdefault(parsed.content_id, LegacyFiles())
        if parsed.duplicate is not None:
            files.duplicates.append(path)
            return
        role = parsed.role
        if role == 'audio':
            files.audio.setdefault(parsed.suffix.lstrip('.'), path)
        files.roles.setdefault(role, path)
        files.entries.append((parsed.suffix, path))

    # ---- queries
    def legacy_files(self, content_id: str | None) -> LegacyFiles | None:
        return self.legacy.get(content_id) if content_id else None

    def legacy_paths(self, content_id: str | None) -> dict[str, Path]:
        """Old flat-layout files of `content_id` keyed by role as `LecturePaths.legacy` expects."""
        files = self.legacy_files(content_id)
        if files is None:
            return {}
        paths: dict[str, Path] = {}
        for role in ('video', 'srt', 'md', 'json'):
            if role in files.roles:
                paths[role] = files.roles[role]
        text = files.roles.get('text') or files.roles.get('text_legacy')
        if text is not None:
            paths['text'] = text
        for extension, path in files.audio.items():
            paths[f'audio:{extension}'] = path
        return paths

    def legacy_material(self, content_id: str | None, extension: str | None = None) -> Path | None:
        """The old flat-layout file of an id planned as a material: the planned extension first, then any file of that id.

        A material can carry a media or text extension (`notes [m1].txt`, a Canvas `.mp4`), which the name parser
        files under another role, so the role is not trusted here.
        """
        files = self.legacy_files(content_id)
        if files is None:
            return None
        wanted = f'.{clean_extension(extension)}' if extension else None
        for suffix, path in files.entries:
            if suffix == wanted:
                return path
        if 'material' in files.roles:
            return files.roles['material']
        return files.entries[0][1] if files.entries else None

    def lecture_files(self, entry: LectureEntry) -> dict[str, Path]:
        """Visible files that exist in a new-layout lecture folder, by role."""
        found: dict[str, Path] = {}
        stem = entry.meta.stem
        try:
            names = sorted(os.listdir(entry.lecture_dir))
        except OSError:
            return found
        for name in names:
            path = entry.lecture_dir / name
            if name == f'{stem}{TEXT_SUFFIX}':
                found.setdefault('text', path)
            elif name == f'{stem}.srt':
                found.setdefault('srt', path)
            elif name.startswith(f'{stem}{AUDIO_SUFFIX}.') and name.rsplit('.', 1)[-1] in AUDIO_EXTENSIONS:
                found.setdefault('audio', path)
            elif name.startswith(f'{stem}.') and name.rsplit('.', 1)[-1] in VIDEO_EXTENSIONS:
                found.setdefault('video', path)
        return found

    def downloaded_path(self, content_id: str) -> Path | None:
        """What `downloaded_path` shows: video, then audio, 자막.txt, material, srt; either layout, new layout first."""
        order = ('video', 'audio', 'text', 'material', 'srt')
        candidates: dict[str, Path] = {}
        lecture = self.lectures.get(content_id)
        if lecture is not None:
            candidates.update(self.lecture_files(lecture))
        material = self.materials.get(content_id)
        if material is not None and material.path.exists():
            candidates['material'] = material.path
        files = self.legacy_files(content_id)
        if files is not None:
            legacy = {
                'video': files.roles.get('video'),
                'audio': next(iter(files.audio.values()), None),
                'text': files.roles.get('text') or files.roles.get('text_legacy'),
                'material': files.roles.get('material'),
                'srt': files.roles.get('srt'),
            }
            for role, path in legacy.items():
                if path is not None:
                    candidates.setdefault(role, path)
        for role in order:
            if role in candidates:
                return candidates[role]
        if files is not None and files.duplicates:
            return sorted(files.duplicates)[0]
        return None


# --------------------------------------------------------------------------- planning one course


@dataclass(frozen=True)
class LibraryTarget:
    """One LMS content item to place: what the folder and file names are derived from."""

    key: str  # module_item_id as text (or `canvas-<file id>`)
    title: str
    kind: str = 'lecture'  # lecture | material | canvas_file
    content_id: str | None = None
    module_item_id: str | None = None
    week_position: object = None
    module_name: str | None = None
    lesson_position: object = None
    extension: str | None = None  # materials: expected extension (see `material_extension`)

    @property
    def identity(self) -> str:
        return self.content_id or f'item-{self.key}'


class CourseLayout:
    """Folder and file names for every placeable item of one course, assigned once, deterministically.

    Built single-threaded (the planning step) before items are resolved or downloaded in parallel, over
    ALL items of the course in (lesson_position, content_id) order, so a name never depends on which
    items a job selected or on which download finished first. Items already on disk keep their place.
    """

    def __init__(self, course_dir: Path, *, course_id: str, rules: PathRules = DEFAULT_RULES, index: LibraryIndex | None = None) -> None:
        self.course_dir = course_dir
        self.course_id = course_id
        self.rules = rules
        self.index = index if index is not None else LibraryIndex.scan(course_dir)
        self._lock = threading.RLock()
        self._claimed: set[tuple[str, str]] = set()
        for entry in self.index.lectures.values():
            self._claimed.add(self._dir_key(entry.lecture_dir))
        self._material_owner: dict[tuple[str, str], str] = {}
        for content_id, entry in self.index.materials.items():
            self._claimed.add((_fold(entry.week_dir.name), _fold(entry.file_name)))
            self._material_owner[(_fold(entry.week_dir.name), _fold(entry.file_name))] = content_id
            for copy in entry.copies:
                self._claimed.add((_fold(entry.week_dir.name), _fold(copy)))
                self._material_owner[(_fold(entry.week_dir.name), _fold(copy))] = content_id
        self._lectures: dict[str, LecturePaths] = {}
        self._materials: dict[str, MaterialPaths] = {}
        self._by_identity: dict[str, LecturePaths | MaterialPaths] = {}
        self._targets: dict[str, LibraryTarget] = {}
        self.failures: dict[str, Exception] = {}
        self._recorded: dict[str, LecturePaths] = {}

    # ---- building
    @classmethod
    def plan(cls, course_dir: Path, targets: Iterable[LibraryTarget], *, course_id: str, rules: PathRules = DEFAULT_RULES, index: LibraryIndex | None = None) -> CourseLayout:
        layout = cls(course_dir, course_id=course_id, rules=rules, index=index)
        ordered = sorted(targets, key=lambda target: (_order_key(target.lesson_position), target.content_id or '', target.key))
        for target in ordered:
            layout.add(target)
        return layout

    def _dir_key(self, lecture_dir: Path) -> tuple[str, str]:
        return (_fold(lecture_dir.parent.name), _fold(lecture_dir.name))

    def add(self, target: LibraryTarget) -> None:
        with self._lock:
            self._targets[target.key] = target
            try:
                if target.kind == 'lecture':
                    self._lectures[target.key] = self._assign_lecture(target)
                else:
                    self._materials[target.key] = self._assign_material(target, target.extension or 'file')
            except PathTooLong as exc:
                self.failures[target.key] = exc

    def _assign_lecture(self, target: LibraryTarget) -> LecturePaths:
        identity = target.identity
        known = self._by_identity.get(identity)
        if isinstance(known, LecturePaths):
            return known
        index = self.index
        legacy = index.legacy_paths(identity)
        entry = index.lectures.get(identity)
        if entry is not None:
            paths = LecturePaths(
                lecture_dir=entry.lecture_dir,
                stem=entry.meta.stem,
                targets=_lecture_targets(entry.lecture_dir, entry.meta.stem, 'mp4', 'mp3'),
                legacy=legacy,
                meta=None,
                course_dir=self.course_dir,
            )
        else:
            week = week_folder(target.week_position, target.module_name, self.rules)
            base = self.rules.fit_stem(lecture_stem(target.title, self.rules), base_dir=self.course_dir, week_folder=week)
            number = 1
            while True:
                name = collision_name(base, number)
                directory = self.course_dir / week / name
                claim = (_fold(week), _fold(name))
                if claim not in self._claimed and not os.path.lexists(directory):
                    break
                number += 1
            self._claimed.add(claim)
            meta = ItemMeta(
                content_id=identity,
                stem=name,
                week_folder=week,
                course_id=self.course_id,
                module_item_id=target.module_item_id,
                kind='lecture',
                lms_title=target.title or None,
            )
            paths = LecturePaths(
                lecture_dir=directory,
                stem=name,
                targets=_lecture_targets(directory, name, 'mp4', 'mp3'),
                legacy=legacy,
                meta=meta,
                course_dir=self.course_dir,
            )
        self._by_identity[identity] = paths
        return paths

    def _assign_material(self, target: LibraryTarget, extension: str) -> MaterialPaths:
        extension = clean_extension(extension, 'file') or 'file'
        identity = target.identity
        known = self._by_identity.get(identity)
        if isinstance(known, MaterialPaths) and known.file_name.rsplit('.', 1)[-1] == extension:
            return known
        index = self.index
        legacy = index.legacy_material(identity, extension)
        entry = index.materials.get(identity)
        if entry is not None:
            paths = MaterialPaths(entry.week_dir, entry.file_name, identity, entry.kind, entry.title or target.title, legacy, self.course_dir, self.material_name_claimed)
        else:
            week = week_folder(target.week_position, target.module_name, self.rules)
            if target.kind == 'canvas_file' and target.week_position is None and not target.module_name:
                number = week_number_in_text(target.title)
                week = _format_week(number) if number is not None else OTHER_WEEK_FOLDER
            base = self.rules.fit_stem(self.rules.stem(target.title, fallback=FALLBACK_MATERIAL_STEM), base_dir=self.course_dir, week_folder=week)
            number = 1
            while True:
                name = f'{collision_name(base, number)}.{extension}'
                claim = (_fold(week), _fold(name))
                if claim not in self._claimed and not os.path.lexists(self.course_dir / week / name):
                    break
                number += 1
            self._claimed.add(claim)
            self._material_owner[claim] = identity
            paths = MaterialPaths(self.course_dir / week, name, identity, 'canvas_file' if target.kind == 'canvas_file' else 'material', target.title or None, legacy, self.course_dir, self.material_name_claimed)
        self._by_identity[identity] = paths
        return paths

    def material_name_claimed(self, week_folder: str, file_name: str, identity: str) -> bool:
        with self._lock:
            owner = self._material_owner.get((_fold(week_folder), _fold(file_name)))
            return owner is not None and owner != identity

    # ---- queries
    def lecture(self, key: str) -> LecturePaths | None:
        with self._lock:
            return self._lectures.get(key)

    def material(self, key: str, resolved_extension: str | None = None) -> MaterialPaths | None:
        """The material planned for `key`.

        `resolved_extension` is what the media server reported: a specific one that differs from the
        planned extension re-plans the name; a generic one (`file`) keeps the extension taken from the LMS `file_name`.
        """
        with self._lock:
            paths = self._materials.get(key)
            target = self._targets.get(key)
            wanted = clean_extension(resolved_extension)
            if paths is None or target is None or not wanted or wanted in GENERIC_EXTENSIONS:
                return paths
            if paths.file_name.rsplit('.', 1)[-1] == wanted or paths.target.exists() or key in self.failures:
                return paths
            try:
                paths = self._assign_material(target, wanted)
            except PathTooLong as exc:
                self.failures[key] = exc
                return self._materials.get(key)
            self._materials[key] = paths
            return paths

    def ensure(self, target: LibraryTarget) -> LecturePaths | MaterialPaths:
        """The paths of `target`, planning it now when it was not part of the course plan."""
        with self._lock:
            known = self._lectures.get(target.key) if target.kind == 'lecture' else self._materials.get(target.key)
            if known is None:
                self.add(target)
            if target.key in self.failures:
                raise self.failures[target.key]
            return self._lectures[target.key] if target.kind == 'lecture' else self._materials[target.key]

    def ensure_kind(self, key: str, kind: str, *, title: str, content_id: str | None, extension: str | None) -> LecturePaths | MaterialPaths:
        """Plan `key` as a `lecture` or a `material` after the media server revealed what it really is.

        Keeps the week, module and order of the planned item when there is one (a module item typed as
        something else in the module list); otherwise the item goes to `기타` under the title given.
        """
        with self._lock:
            known = self._targets.get(key)
            target = (
                replace(known, kind=kind, extension=extension if kind != 'lecture' else None)
                if known is not None
                else LibraryTarget(key=key, title=title, kind=kind, content_id=content_id, module_item_id=key, extension=extension)
            )
            return self.ensure(target)

    def bind_identity(self, key: str, content_id: str | None) -> LecturePaths | None:
        """Switch a planned lecture to the content id the media server reported, when the module list had none or another.

        The folder keeps its place unless the new id is already a lecture of this course (then that one is used).
        Old flat-layout files are looked up by the reported id (that is what their `[id]` names hold).
        """
        with self._lock:
            paths = self._lectures.get(key)
            if paths is None or not content_id:
                return paths
            if paths.meta is None and content_id in self.index.lectures:
                return paths
            entry = self.index.lectures.get(content_id)
            legacy = self.index.legacy_paths(content_id)
            if entry is not None and (paths.meta is None or paths.meta.content_id != content_id):
                paths = LecturePaths(entry.lecture_dir, entry.meta.stem, _lecture_targets(entry.lecture_dir, entry.meta.stem, 'mp4', 'mp3'), legacy, None, course_dir=self.course_dir)
            else:
                meta = paths.meta if paths.meta is None else replace(paths.meta, content_id=content_id)
                paths = replace(paths, legacy={**paths.legacy, **legacy} if legacy else paths.legacy, meta=meta)
            self._lectures[key] = paths
            return paths

    def record(self, key: str, paths: LecturePaths) -> None:
        with self._lock:
            self._recorded[key] = paths

    def recorded(self) -> dict[str, LecturePaths]:
        with self._lock:
            return dict(self._recorded)


def _lecture_targets(lecture_dir: Path, stem: str, video_ext: str, audio_format: str) -> dict[str, Path]:
    names = visible_names(stem, video_ext, audio_format)
    meta_dir = lecture_dir / META_DIR_NAME
    return {
        'video': lecture_dir / names['video'],
        'audio': lecture_dir / names['audio'],
        'srt': lecture_dir / names['srt'],
        'text': lecture_dir / names['text'],
        'md': meta_dir / TRANSCRIPT_MD,
        'json': meta_dir / TRANSCRIPT_JSON,
    }


def explicit_lecture_paths(
    lecture_dir: Path, *, stem: str, video: Path, audio: Path, srt: Path, text: Path, md: Path, json: Path, audio_format: str = 'mp3'
) -> LecturePaths:
    """Paths that are all given by the caller (the standalone/fallback derivation); nothing is recorded in `.ssu`."""
    return LecturePaths(
        lecture_dir=lecture_dir,
        stem=stem,
        targets={'video': video, 'audio': audio, 'srt': srt, 'text': text, 'md': md, 'json': json},
        video_ext=video.suffix.lstrip('.') or 'mp4',
        audio_format=audio_format,
    )
