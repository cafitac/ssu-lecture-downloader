"""Tidy-up of the old flat download layout: preview, apply, undo (design 05 flows 2-4, 07 § Migration run).

A tidy-up moves the files an earlier version saved as `<course>/<title> [<content id>].<ext>` into the
week -> lecture layout. The rules it follows:

- Planning only reads (lstat, listdir, LMS lists). Nothing in the folder changes, not even a timestamp.
- Applying never deletes and never replaces a path: a move is `link` + `unlink`, which fails when the
  destination exists (the fallback for volumes without hard links is an existence check + `rename`).
  Before every file the size, `mtime_ns`, inode and the free destination are checked again; a file that
  no longer matches the preview is skipped, never forced.
- Every move is journaled first (`<root>/.ssu/migrations/<run_id>.jsonl`, fsynced `intent` line before,
  `done` line after), so a crash is repaired by the next preview/apply and a run can be undone.
- Undo reads the journal backwards, restores only files that are unchanged since the move, and removes
  only empty folders it created plus the app metadata it wrote (and only while that metadata is
  byte-identical to what the run wrote). User files are never deleted.

Everything read from the download folder is untrusted: journal lines are bounded and validated, paths
must stay under the root, symlinks are not followed.
"""

from __future__ import annotations

import base64
import binascii
import errno
import hashlib
import json
import logging
import os
import re
import secrets
import stat
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol, cast

from ssu_lecture_downloader import __version__
from ssu_lecture_downloader.core.download import safe_filename
from ssu_lecture_downloader.core.errors import PathTooLong, TidyRunNotFound
from ssu_lecture_downloader.core.library_layout import (
    AUDIO_SUFFIX,
    CourseLayout,
    LecturePaths,
    LibraryIndex,
    LibraryTarget,
    MaterialPaths,
    clean_extension,
)
from ssu_lecture_downloader.core.library_meta import (
    LAYOUT_VERSION,
    META_DIR_NAME,
    ITEM_FILE_NAME,
    MATERIALS_FILE_NAME,
    ItemMeta,
    ensure_meta_dir,
    LegacyName,
    MaterialMeta,
    parse_legacy_name,
    upsert_material_meta,
    utc_now,
    write_item_meta,
)
from ssu_lecture_downloader.core.lms_service import _course_layout

logger = logging.getLogger(__name__)

PLAN_TTL_SECONDS = 30 * 60
PROGRESS_STAGE = 'tidy_moving'
PROGRESS_MIN_SECONDS = 0.5
PROGRESS_MIN_FILES = 10
JOURNAL_PARTS = (META_DIR_NAME, 'migrations')
RUN_ID_PATTERN = re.compile(r'^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$')
MAX_JOURNAL_BYTES = 64 * 1024 * 1024
MAX_JOURNAL_LINE_BYTES = 1024 * 1024
MAX_META_BYTES = 256 * 1024

# Reasons a file stays where it is (design 09 § 그대로 두는 이유). `in_use` and `path_too_long` are the Windows
# reasons: defined, but nothing raises `in_use` on macOS. `move_failed` is added here for an unexpected OS error
# on one file, so that file is skipped instead of the whole run failing half way.
DESTINATION_EXISTS = 'destination_exists'
UNKNOWN_FILE = 'unknown_file'
PARTIAL_DOWNLOAD = 'partial_download'
COURSE_NOT_MATCHED = 'course_not_matched'
DUPLICATE_TRANSCRIPT = 'duplicate_transcript'
SOURCE_CHANGED = 'source_changed'
SOURCE_MISSING = 'source_missing'
NOT_A_REGULAR_FILE = 'not_a_regular_file'
IN_USE = 'in_use'
PATH_TOO_LONG = 'path_too_long'
MOVE_FAILED = 'move_failed'

LECTURE_ROLES = frozenset({'video', 'audio', 'srt', 'text', 'text_legacy', 'md', 'json'})
_ROLE_ORDER = {'video': 0, 'audio': 1, 'srt': 2, 'text': 3, 'text_legacy': 3, 'md': 4, 'json': 5, 'material': 6}
# link() errors that mean "this volume has no hard links": fall back to check + rename.
_LINK_UNSUPPORTED = frozenset(code for code in (errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EMLINK, errno.ENOSYS) if code)


def _monotonic() -> float:
    return time.monotonic()


def _nfc(text: str) -> str:
    return unicodedata.normalize('NFC', text)


# --------------------------------------------------------------------------- course catalog (LMS, read only)


@dataclass(frozen=True)
class CatalogCourse:
    course_id: str
    name: str


class CourseCatalog(Protocol):
    """The current LMS courses and their content items. Read only; supplied by the service layer."""

    def courses(self) -> Sequence[CatalogCourse]: ...

    def content_items(self, course: CatalogCourse) -> Sequence[Any]: ...


# --------------------------------------------------------------------------- plan


@dataclass(frozen=True)
class PlannedSkip:
    path: str  # relative to the root
    reason: str


@dataclass(frozen=True)
class PlannedMove:
    source: Path
    destination: Path
    size: int
    mtime_ns: int
    inode: int
    content_id: str
    role: str
    course: str
    week_folder: str
    lecture_stem: str | None


@dataclass
class MoveGroup:
    """The moves that land in one lecture folder (or one material file) plus the metadata they need."""

    course_dir: Path
    directory: Path  # lecture folder, or the week folder of a material
    item: ItemMeta | None = None  # a lecture folder this plan creates: its `.ssu/item.json`
    material: tuple[str, MaterialMeta] | None = None  # (content id, entry) for the week's `.ssu/materials.json`
    moves: list[PlannedMove] = field(default_factory=list)
    meta_written: bool = False  # apply state: folders and metadata of the group exist
    dir_ours: bool = False  # apply state: this run created the lecture folder


@dataclass
class TidyPlan:
    plan_id: str
    root: Path
    created_at: float
    expires_at: str
    groups: list[MoveGroup]
    skips: list[PlannedSkip]

    @property
    def moves(self) -> list[PlannedMove]:
        return [move for group in self.groups for move in group.moves]

    def payload(self) -> dict[str, Any]:
        moves = self.moves
        return {
            'plan_id': self.plan_id,
            'expires_at': self.expires_at,
            'move_count': len(moves),
            'skip_count': len(self.skips),
            'moves': [
                {
                    'course': move.course,
                    'week_folder': move.week_folder,
                    'lecture_stem': move.lecture_stem,
                    'from': move.source.relative_to(self.root).as_posix(),
                    'to': move.destination.relative_to(self.root).as_posix(),
                    'role': move.role,
                }
                for move in moves
            ],
            'skips': [{'path': skip.path, 'reason': skip.reason} for skip in self.skips],
            'open_files_note': True,  # macOS moves open files fine; players keep playing until they reopen by path
        }


class TidyPlanStore:
    """In-memory plans: 30 minutes, single use."""

    def __init__(self, ttl_seconds: float = PLAN_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._plans: dict[str, TidyPlan] = {}
        self._lock = threading.Lock()

    def put(self, plan: TidyPlan) -> None:
        with self._lock:
            now = _monotonic()
            for stale in [key for key, kept in self._plans.items() if now - kept.created_at > self.ttl_seconds]:
                del self._plans[stale]
            self._plans[plan.plan_id] = plan

    def take(self, plan_id: str) -> TidyPlan | None:
        """The plan, removed from the store; None when unknown, already used or older than the TTL."""
        with self._lock:
            plan = self._plans.pop(plan_id, None)
        if plan is None or _monotonic() - plan.created_at > self.ttl_seconds:
            return None
        return plan


@dataclass(frozen=True)
class _Found:
    path: Path
    parsed: LegacyName
    size: int
    mtime_ns: int
    inode: int


def _real_subfolders(directory: Path) -> list[os.DirEntry[str]]:
    try:
        entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
    except OSError:
        return []
    return [entry for entry in entries if not entry.name.startswith('.') and _is_real_dir(entry)]


def _is_real_dir(entry: os.DirEntry[str]) -> bool:
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _list_course_folder(folder: Path) -> tuple[list[_Found], list[tuple[Path, str | None]]]:
    """Attributable legacy files directly inside `folder`, and everything else worth listing as (path, reason).

    A reason of None marks a sub-folder: it is only listed when it is not a week folder of the new layout.
    Hidden names and `.tmp` leftovers of atomic writes are ignored silently.
    """
    found: list[_Found] = []
    others: list[tuple[Path, str | None]] = []
    try:
        entries = sorted(os.scandir(folder), key=lambda entry: entry.name)
    except OSError:
        return found, others
    for entry in entries:
        name = entry.name
        path = Path(entry.path)
        if name.startswith('.') or name.endswith('.tmp'):
            continue
        try:
            if entry.is_symlink():
                others.append((path, NOT_A_REGULAR_FILE))
                continue
            if entry.is_dir(follow_symlinks=False):
                others.append((path, None))
                continue
            info = entry.stat(follow_symlinks=False)
        except OSError:
            others.append((path, NOT_A_REGULAR_FILE))
            continue
        if not stat.S_ISREG(info.st_mode):
            others.append((path, NOT_A_REGULAR_FILE))
        elif name.lower().endswith('.part'):
            others.append((path, PARTIAL_DOWNLOAD))
        else:
            parsed = parse_legacy_name(name)
            if parsed is None:
                others.append((path, UNKNOWN_FILE))
            else:
                found.append(_Found(path, parsed, info.st_size, info.st_mtime_ns, info.st_ino))
    return found, others


def _disk_only_leftovers(found: list[_Found], index: LibraryIndex) -> set[Path]:
    """Old files a preview will always leave (`duplicate_transcript`), knowable without the LMS: a copy `(n)` of a
    transcript file, the oldest `.txt` next to a `.transcript.txt`, or next to a transcript text already in the lecture folder."""
    leftovers: set[Path] = set()
    main_text = {f.parsed.content_id for f in found if f.parsed.role == 'text' and f.parsed.duplicate is None}
    for f in found:
        role, content_id = f.parsed.role, f.parsed.content_id
        if f.parsed.duplicate is not None and role in ('text', 'text_legacy', 'md', 'json'):
            leftovers.add(f.path)
        elif role == 'text_legacy':
            lecture = index.lectures.get(content_id)
            if content_id in main_text or (lecture is not None and 'text' in index.lecture_files(lecture)):
                leftovers.add(f.path)
    return leftovers


def count_legacy_files(course_dir: Path) -> int:
    """How many files directly inside `course_dir` a tidy-up could still move: old `[content id]` names, without the
    leftovers that need no LMS to know they stay (`.part` files, unknown names and duplicate transcripts)."""
    found, _ = _list_course_folder(course_dir)
    if not found:
        return 0
    return len(found) - len(_disk_only_leftovers(found, LibraryIndex.scan(course_dir)))


def legacy_summary(root: Path) -> dict[str, Any]:
    """Scan-only summary of the old-layout files per course folder (no login needed)."""
    courses = [
        {'folder': entry.name, 'legacy_file_count': count}
        for entry in _real_subfolders(root)
        if (count := count_legacy_files(Path(entry.path)))
    ]
    return {
        'ok': True,
        'root': str(root),
        'legacy_file_count': sum(course['legacy_file_count'] for course in courses),
        'course_count': len(courses),
        'courses': courses,
    }


def _same_inode(first: Path, second: Path) -> bool:
    try:
        return os.path.samestat(os.lstat(first), os.lstat(second))
    except OSError:
        return False


def _parents_are_plain(course_dir: Path, destination: Path) -> bool:
    """No existing folder between the course folder and the destination is a symlink or a file."""
    current = destination.parent
    while current != course_dir and course_dir in current.parents:
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            current = current.parent
            continue
        except OSError:
            return False
        if not stat.S_ISDIR(info.st_mode):
            return False
        current = current.parent
    return True


class _CoursePlan:
    """Planning state of one matched course folder: the layout the downloader would use for it."""

    def __init__(self, courses: Sequence[CatalogCourse], items: Sequence[Any], root: Path) -> None:
        self.root = root
        first = courses[0]
        self.layout: CourseLayout = _course_layout(items, course_id=first.course_id, course_name=first.name, output_dir=root)
        self.folder = self.layout.course_dir
        self.by_content: dict[str, Any] = {}
        for item in items:
            if item.content_id and item.module_item_id is not None:
                self.by_content.setdefault(str(item.content_id), item)


# --------------------------------------------------------------------------- exclusive move


def exclusive_move(source: Path, destination: Path) -> str:
    """Move `source` to a destination that must not exist. Returns `moved` or `exists`; raises OSError otherwise.

    `link` fails atomically when the destination exists, so nothing is ever replaced; the source name is
    removed only after the link succeeded, so a crash leaves two names of one inode and never loses data.
    Volumes without hard links (exFAT, some network shares) use an existence check + `rename` instead; the
    instant between the check and the rename is the documented, accepted gap (only another app creating
    exactly that name at that moment could be overwritten; this app holds the tidy lock meanwhile).
    A move across volumes is an error here, never a copy.
    """
    try:
        os.link(source, destination)
    except FileExistsError:
        # Two names of one inode: a half-finished earlier move (the old name is left to remove), unless the file
        # has only one name, i.e. the destination is the same entry spelled differently (case-insensitive volume).
        if _same_inode(source, destination) and os.lstat(source).st_nlink >= 2:
            os.unlink(source)
            return 'moved'
        return 'exists'
    except OSError as exc:
        if exc.errno not in _LINK_UNSUPPORTED:
            raise
        if os.path.lexists(destination):
            return 'exists'
        os.rename(source, destination)
        return 'moved'
    try:
        os.unlink(source)
    except OSError:
        try:  # the link was ours and the old name cannot go: take the new name back so nothing half-moved is left
            if _same_inode(source, destination):
                os.unlink(destination)
        except OSError:
            pass
        raise
    return 'moved'


# --------------------------------------------------------------------------- journal


def migrations_dir(root: Path) -> Path:
    return root.joinpath(*JOURNAL_PARTS)


def _existing_migrations_dir(root: Path) -> Path | None:
    """The journal folder when it is a plain folder under a plain `.ssu` (a symlink in either place is not followed)."""
    try:
        if any(stat.S_ISLNK(os.lstat(path).st_mode) or not stat.S_ISDIR(os.lstat(path).st_mode) for path in (root / META_DIR_NAME, migrations_dir(root))):
            return None
    except OSError:
        return None
    return migrations_dir(root)


def new_run_id() -> str:
    """`yyyymmddThhmmssZ-<ms as 3 hex><3 random hex>`: sorts by time to the millisecond, so "newest first" is a plain sort."""
    now = datetime.now(timezone.utc)
    return f'{now.strftime("%Y%m%dT%H%M%SZ")}-{now.microsecond // 1000:03x}{secrets.token_hex(2)[:3]}'


class Journal:
    """Append-only JSON lines, fsynced per line."""

    def __init__(self, path: Path, fd: int, seq: int) -> None:
        self.path = path
        self._fd = fd
        self._seq = seq

    @classmethod
    def create(cls, root: Path, run_id: str) -> Journal:
        ensure_meta_dir(root)  # refuses a symlinked `.ssu`
        directory = migrations_dir(root)
        directory.mkdir(exist_ok=True)
        if directory.is_symlink():
            raise OSError('migrations is not a plain folder')
        path = directory / f'{run_id}.jsonl'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
        return cls(path, fd, 0)

    @classmethod
    def reopen(cls, path: Path) -> Journal:
        last = max((int(line.get('seq', 0)) for line in read_journal(path) if isinstance(line.get('seq'), int)), default=0)
        return cls(path, os.open(path, os.O_WRONLY | os.O_APPEND), last)

    def append(self, op: str, **fields: Any) -> int:
        self._seq += 1
        line = {'seq': self._seq, 'op': op, 'at': utc_now(), **{key: value for key, value in fields.items() if value is not None}}
        os.write(self._fd, (json.dumps(line, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8'))
        os.fsync(self._fd)
        return self._seq

    def close(self) -> None:
        if self._fd >= 0:
            os.fsync(self._fd)
            os.close(self._fd)
            self._fd = -1


def read_journal(path: Path) -> list[dict[str, Any]]:
    """The valid lines of a journal. Oversized files or lines and lines that are not JSON objects are dropped."""
    try:
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_JOURNAL_BYTES:
            return []
        with open(path, 'rb') as handle:
            raw = handle.read(MAX_JOURNAL_BYTES)
    except OSError:
        return []
    lines: list[dict[str, Any]] = []
    for chunk in raw.split(b'\n'):
        if not chunk or len(chunk) > MAX_JOURNAL_LINE_BYTES:
            continue
        try:
            value = json.loads(chunk.decode('utf-8'))
        except ValueError:
            continue
        if isinstance(value, dict) and isinstance(value.get('op'), str):
            lines.append(value)
    return lines


def run_journal_path(root: Path, run_id: str) -> Path:
    """The journal file of a run; `TidyRunNotFound` for an id that is malformed or has no journal."""
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.match(run_id):
        raise TidyRunNotFound(run_id)
    directory = _existing_migrations_dir(root)
    path = None if directory is None else directory / f'{run_id}.jsonl'
    if path is None or not path.is_file() or path.is_symlink():
        raise TidyRunNotFound(run_id)
    return path


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _resolve_inside(root: Path, relative: object) -> Path | None:
    """`root / relative` when the journal's relative path stays under the root and crosses no symlink."""
    if not isinstance(relative, str) or not relative or relative.startswith('/') or '\x00' in relative or '\\' in relative:
        return None
    parts = relative.split('/')
    if any(part in ('', '.', '..') for part in parts):
        return None
    current = root
    for part in parts[:-1]:
        current = current / part
        try:
            if stat.S_ISLNK(os.lstat(current).st_mode):
                return None
        except OSError:
            return None
    return current / parts[-1]


def _visible(parts: list[str]) -> bool:
    return all(part and part not in ('.', '..') and not part.startswith('.') for part in parts)


def _move_shape_ok(source: object, destination: object) -> bool:
    """A journaled move has the shape this module writes: `<course>/<file>` -> `<course>/<week>/[<lecture>/]<file>`, or the
    lecture's `.ssu/transcript.md|json`. Anything else in a journal (planted or corrupt) is never acted on."""
    if not isinstance(source, str) or not isinstance(destination, str):
        return False
    src, dst = source.split('/'), destination.split('/')
    if len(src) != 2 or not 3 <= len(dst) <= 5 or dst[0] != src[0] or not _visible(src):
        return False
    if len(dst) == 5:
        return dst[3] == '.ssu' and _visible(dst[:3]) and dst[4] in ('transcript.md', 'transcript.json')
    return _visible(dst)


def _meta_kind(destination: object) -> str | None:
    """`item` for `<course>/<week>/<lecture>/.ssu/item.json`, `materials` for `<course>/<week>/.ssu/materials.json`, else None."""
    if not isinstance(destination, str):
        return None
    parts = destination.split('/')
    if len(parts) == 5 and parts[3:] == ['.ssu', ITEM_FILE_NAME] and _visible(parts[:3]):
        return 'item'
    if len(parts) == 4 and parts[2:] == ['.ssu', MATERIALS_FILE_NAME] and _visible(parts[:2]):
        return 'materials'
    return None


def _mkdir_shape_ok(destination: object) -> bool:
    if not isinstance(destination, str):
        return False
    parts = destination.split('/')
    if parts[-1] == '.ssu':
        return len(parts) in (3, 4) and _visible(parts[:-1])
    return len(parts) in (2, 3) and _visible(parts)


def _int_field(line: dict[str, Any], key: str) -> int | None:
    value = line.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _write_bytes_atomically(path: Path, data: bytes) -> None:
    tmp = path.with_name(f'{path.name}.{os.getpid()}-{secrets.token_hex(3)}.tmp')
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_small(path: Path) -> bytes | None:
    try:
        if not stat.S_ISREG(os.lstat(path).st_mode):
            return None
        with open(path, 'rb') as handle:
            data = handle.read(MAX_META_BYTES + 1)
    except OSError:
        return None
    return data if len(data) <= MAX_META_BYTES else None


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class RunResult:
    run_id: str | None
    moved: int
    skips: list[PlannedSkip]
    undo_available: bool

    def payload(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id,
            'moved': self.moved,
            'skipped': len(self.skips),
            'skips': [{'path': skip.path, 'reason': skip.reason} for skip in self.skips],
            'undo_available': self.undo_available,
        }


@dataclass(frozen=True)
class UndoResult:
    run_id: str
    restored: int
    skips: list[PlannedSkip]

    def payload(self) -> dict[str, Any]:
        return {
            'run_id': self.run_id,
            'restored': self.restored,
            'skipped': len(self.skips),
            'skips': [{'path': skip.path, 'reason': skip.reason} for skip in self.skips],
        }


class _Throttle:
    """`tidy_moving` progress: first, last, then every 10 files or every 0.5 s, whichever comes first."""

    def __init__(self, emit: Callable[[dict[str, Any]], None] | None, total: int, clock: Callable[[], float]) -> None:
        self._emit = emit
        self._total = total
        self._clock = clock
        self._last_done: int | None = None
        self._last_at = 0.0

    def tick(self, done: int, *, force: bool = False) -> None:
        if self._emit is None or done == self._last_done:
            return
        now = self._clock()
        if force or self._last_done is None or done - self._last_done >= PROGRESS_MIN_FILES or now - self._last_at >= PROGRESS_MIN_SECONDS:
            self._last_done, self._last_at = done, now
            self._emit({'stage': PROGRESS_STAGE, 'done': done, 'total': self._total})


# --------------------------------------------------------------------------- the runner


class MigrationRunner:
    """Plan, apply and undo a tidy-up. One run at a time (the caller holds the library lock)."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock

    # ---------------------------------------------------------------- plan (read only)

    def plan(self, root: Path, catalog: CourseCatalog) -> TidyPlan:
        plan_id = uuid.uuid4().hex
        created = _monotonic()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=PLAN_TTL_SECONDS)).strftime('%Y-%m-%dT%H:%M:%SZ')
        groups: list[MoveGroup] = []
        skips: list[PlannedSkip] = []
        claimed: set[str] = set()
        matched: dict[str, list[CatalogCourse]] | None = None
        for entry in _real_subfolders(root):
            listed = Path(entry.path)
            found, others = _list_course_folder(listed)
            if not found and not others:
                continue
            if matched is None:
                matched = {}
                for course in catalog.courses():
                    matched.setdefault(_nfc(safe_filename(course.name, fallback='course')), []).append(course)
            courses = matched.get(_nfc(entry.name))
            if courses is None:
                if found:  # a folder of another term or of the user: left alone, one line for the whole folder
                    skips.append(PlannedSkip(entry.name, COURSE_NOT_MATCHED))
                continue
            index = LibraryIndex.scan(listed)
            known = {_nfc(str(lecture.lecture_dir.parent)) for lecture in index.lectures.values()}
            known |= {_nfc(str(material.week_dir)) for material in index.materials.values()}
            for path, reason in others:
                if reason is not None:
                    skips.append(PlannedSkip(_relative(root, path), reason))
                elif _nfc(str(path)) not in known:  # a week folder of the new layout is not a skipped item
                    skips.append(PlannedSkip(_relative(root, path), NOT_A_REGULAR_FILE))
            if found:
                items = [item for course in courses for item in catalog.content_items(course)]
                self._plan_course(_CoursePlan(courses, items, root), found, groups, skips, claimed)
        groups.sort(key=lambda group: str(group.directory))
        skips.sort(key=lambda skip: (skip.path, skip.reason))
        return TidyPlan(plan_id, root, created, expires, groups, skips)

    def _plan_course(self, course: _CoursePlan, found: list[_Found], groups: list[MoveGroup], skips: list[PlannedSkip], claimed: set[str]) -> None:
        by_id: dict[str, list[_Found]] = {}
        for item in found:
            by_id.setdefault(item.parsed.content_id, []).append(item)
        for content_id in sorted(by_id, key=lambda key: (min(f.parsed.title.casefold() for f in by_id[key]), key)):
            files = sorted(by_id[content_id], key=lambda f: (_ROLE_ORDER.get(f.parsed.role, 9), f.path.name))
            try:
                group = self._plan_group(course, content_id, files, skips, claimed)
            except PathTooLong:
                skips.extend(PlannedSkip(_relative(course.root, f.path), PATH_TOO_LONG) for f in files)
                continue
            if group is not None and group.moves:
                groups.append(group)

    def _plan_group(self, course: _CoursePlan, content_id: str, files: list[_Found], skips: list[PlannedSkip], claimed: set[str]) -> MoveGroup | None:
        item = course.by_content.get(content_id)
        roles = {f.parsed.role for f in files}
        title = files[0].parsed.title
        if item is not None:
            lecture = item.kind == 'video' or 'video' in roles
        else:
            lecture = not content_id.startswith('canvas-') and bool(roles & LECTURE_ROLES)
        layout = course.layout
        if lecture:
            if item is not None:
                key = str(item.module_item_id)
                paths = cast(LecturePaths, layout.lecture(key) or layout.ensure_kind(key, 'lecture', title=item.title or title, content_id=content_id, extension=None))
            else:
                paths = cast(LecturePaths, layout.ensure(LibraryTarget(key=f'legacy:{content_id}', title=title, kind='lecture', content_id=content_id)))
            group = MoveGroup(course.folder, paths.lecture_dir, item=None if paths.meta is None else replace(paths.meta, created_by='migration'))
            week, stem = paths.lecture_dir.parent.name, paths.stem
            has_main_text = any(f.parsed.role == 'text' and f.parsed.duplicate is None for f in files)
            for f in files:
                destination, reason = _lecture_destination(paths, f, has_main_text)
                self._add(course, group, f, destination, reason, week, stem, skips, claimed)
            return group
        extension = clean_extension(files[0].parsed.suffix.rsplit('.', 1)[-1])
        if item is not None:
            key = str(item.module_item_id)
            target = layout.material(key, extension)
            paths = target if target is not None else layout.ensure(
                LibraryTarget(key=key, title=item.title or title, kind='material', content_id=content_id, module_item_id=key, extension=extension)
            )
        else:
            kind = 'canvas_file' if content_id.startswith('canvas-') else 'material'
            paths = layout.ensure(LibraryTarget(key=f'legacy:{content_id}', title=title, kind=kind, content_id=content_id, extension=extension))
        group = MoveGroup(course.folder, paths.week_dir)
        main_taken = False
        for f in files:
            name_stem, _, name_ext = paths.file_name.rpartition('.')
            if f.parsed.duplicate is not None:
                name = f'{name_stem} ({f.parsed.duplicate}).{name_ext}'
                destination = paths.week_dir / name
                reason = DESTINATION_EXISTS if layout.material_name_claimed(paths.week_dir.name, name, paths.content_id) else None
            elif main_taken:
                destination, reason = None, DESTINATION_EXISTS
            else:
                destination, reason = paths.target, None
            if self._add(course, group, f, destination, reason, paths.week_dir.name, None, skips, claimed, role='material' if f.parsed.duplicate is None else 'duplicate') and f.parsed.duplicate is None:
                main_taken = True
                group.material = (paths.content_id, MaterialMeta(file_name=paths.file_name, kind=paths.kind, title=paths.title, created_by='migration'))
        return group

    def _add(
        self,
        course: _CoursePlan,
        group: MoveGroup,
        found: _Found,
        destination: Path | None,
        reason: str | None,
        week: str,
        stem: str | None,
        skips: list[PlannedSkip],
        claimed: set[str],
        *,
        role: str | None = None,
    ) -> bool:
        if destination is not None and reason is None:
            key = _nfc(str(destination)).casefold()
            if key in claimed or (os.path.lexists(destination) and not _same_inode(found.path, destination)) or not _parents_are_plain(course.folder, destination):
                reason = DESTINATION_EXISTS
            else:
                claimed.add(key)
        if destination is None or reason is not None:
            skips.append(PlannedSkip(_relative(course.root, found.path), reason or UNKNOWN_FILE))
            return False
        label = role or ('duplicate' if found.parsed.duplicate is not None else 'text' if found.parsed.role == 'text_legacy' else found.parsed.role)
        group.moves.append(
            PlannedMove(found.path, destination, found.size, found.mtime_ns, found.inode, found.parsed.content_id, label, course.folder.name, week, stem)
        )
        return True

    # ---------------------------------------------------------------- apply

    def apply(
        self,
        plan: TidyPlan,
        *,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> RunResult:
        root = plan.root
        skips: list[PlannedSkip] = list(plan.skips)
        moves = plan.moves
        if not moves:
            return RunResult(None, 0, skips, False)
        self._recover_dangling(root)
        run_id = new_run_id()
        journal = Journal.create(root, run_id)
        progress = _Throttle(on_progress, len(moves), self._clock)
        moved = processed = 0
        try:
            journal.append('header', run_id=run_id, root=str(root), app_version=__version__, layout_version=LAYOUT_VERSION, move_count=len(moves), skip_count=len(skips))
            progress.tick(0, force=True)
            cancelled = False
            for group in plan.groups:
                for move in group.moves:
                    if cancelled or (should_cancel is not None and should_cancel()):
                        cancelled = True
                        break
                    skip_reason = self._revalidate(move)
                    if skip_reason is None:
                        skip_reason = self._prepare_and_move(root, group, move, journal)
                    if skip_reason is None:
                        moved += 1
                    else:
                        skips.append(PlannedSkip(_relative(root, move.source), skip_reason))
                    processed += 1
                    progress.tick(processed)
                if cancelled:
                    break
            progress.tick(processed, force=True)
        finally:
            journal.close()
        return RunResult(run_id, moved, skips, moved > 0)

    @staticmethod
    def _revalidate(move: PlannedMove) -> str | None:
        try:
            info = os.lstat(move.source)
        except FileNotFoundError:
            return SOURCE_MISSING
        except OSError:
            return SOURCE_CHANGED
        if not stat.S_ISREG(info.st_mode):
            return NOT_A_REGULAR_FILE
        if (info.st_size, info.st_mtime_ns, info.st_ino) != (move.size, move.mtime_ns, move.inode):
            return SOURCE_CHANGED
        if os.path.lexists(move.destination) and not _same_inode(move.source, move.destination):
            return DESTINATION_EXISTS
        return None

    def _prepare_and_move(self, root: Path, group: MoveGroup, move: PlannedMove, journal: Journal) -> str | None:
        """Create folders and metadata (before the first move of a group), journal, move. A skip reason or None."""
        try:
            if group.item is not None and not group.dir_ours and os.path.lexists(group.directory):
                return DESTINATION_EXISTS  # a folder of that name appeared after the preview: foreign, never written into
            created = self._ensure_dir(root, group.course_dir, move.destination.parent, journal)
            group.dir_ours = group.dir_ours or group.directory in created
            if not group.meta_written:
                self._write_meta(root, group, journal)
                group.meta_written = True
        except (FileExistsError, NotADirectoryError):
            return DESTINATION_EXISTS
        except OSError:
            return MOVE_FAILED
        intent = journal.append(
            'intent',
            source=_relative(root, move.source),
            destination=_relative(root, move.destination),
            size=move.size,
            mtime_ns=move.mtime_ns,
            inode=move.inode,
            content_id=move.content_id,
            role=move.role,
        )
        try:
            outcome = exclusive_move(move.source, move.destination)
        except OSError as exc:
            logger.debug('tidy move failed (%s)', type(exc).__name__)
            journal.append('skip', ref=intent, source=_relative(root, move.source), reason=MOVE_FAILED)
            return MOVE_FAILED
        if outcome == 'exists':
            journal.append('skip', ref=intent, source=_relative(root, move.source), reason=DESTINATION_EXISTS)
            return DESTINATION_EXISTS
        journal.append(
            'done',
            ref=intent,
            source=_relative(root, move.source),
            destination=_relative(root, move.destination),
            size=move.size,
            mtime_ns=move.mtime_ns,
            inode=move.inode,
            content_id=move.content_id,
            role=move.role,
        )
        return None

    @staticmethod
    def _ensure_dir(root: Path, course_dir: Path, target: Path, journal: Journal) -> list[Path]:
        """Create the missing folders from the course folder down to `target`; the ones this run made are journaled and returned."""
        missing: list[Path] = []
        current = target
        while current != course_dir:
            if course_dir not in current.parents:
                raise NotADirectoryError(str(current))
            try:
                info = os.lstat(current)
            except FileNotFoundError:
                missing.append(current)
                current = current.parent
                continue
            if not stat.S_ISDIR(info.st_mode):
                raise NotADirectoryError(str(current))
            break
        created: list[Path] = []
        for directory in reversed(missing):
            try:
                os.mkdir(directory)
            except FileExistsError:
                if not os.path.isdir(directory) or os.path.islink(directory):
                    raise
                continue  # created by someone else meanwhile: not ours to remove later
            journal.append('mkdir', destination=_relative(root, directory))
            created.append(directory)
        return created

    def _write_meta(self, root: Path, group: MoveGroup, journal: Journal) -> None:
        """Metadata before the first file of the group moves, so a crash leaves a folder the next plan recognises."""
        if group.item is not None:
            self._ensure_dir(root, group.course_dir, group.directory / META_DIR_NAME, journal)
            self._journal_meta(root, group.directory / META_DIR_NAME / ITEM_FILE_NAME, lambda: write_item_meta(group.directory, group.item), journal)
        if group.material is not None:
            content_id, entry = group.material
            self._ensure_dir(root, group.course_dir, group.directory / META_DIR_NAME, journal)
            self._journal_meta(
                root, group.directory / META_DIR_NAME / MATERIALS_FILE_NAME, lambda: upsert_material_meta(group.directory, content_id, entry), journal
            )

    @staticmethod
    def _journal_meta(root: Path, path: Path, write: Callable[[], Any], journal: Journal) -> None:
        previous = _read_small(path)
        write()
        written = _read_small(path)
        if written is None or written == previous:
            return
        journal.append(
            'meta_written',
            destination=_relative(root, path),
            previous_b64=None if previous is None else base64.b64encode(previous).decode('ascii'),
            sha256=hashlib.sha256(written).hexdigest(),
        )

    # ---------------------------------------------------------------- crash recovery

    def _recover_dangling(self, root: Path) -> None:
        """Close journal `intent` lines whose move did complete (source gone, destination present with the journaled inode)."""
        directory = _existing_migrations_dir(root)
        try:
            names = [] if directory is None else sorted(name for name in os.listdir(directory) if name.endswith('.jsonl') and RUN_ID_PATTERN.match(name[:-6]))
        except OSError:
            return
        for name in names:
            self._recover_journal(root, directory / name)

    def _recover_journal(self, root: Path, path: Path) -> None:
        lines = read_journal(path)
        closed = {line.get('ref') for line in lines if line.get('op') in ('done', 'skip')}
        dangling = [line for line in lines if line.get('op') == 'intent' and line.get('seq') not in closed]
        journal: Journal | None = None
        try:
            for line in dangling:
                source, destination = _resolve_inside(root, line.get('source')), _resolve_inside(root, line.get('destination'))
                if source is None or destination is None or not _move_shape_ok(line.get('source'), line.get('destination')) or os.path.lexists(source):
                    continue
                try:
                    info = os.lstat(destination)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode) or (info.st_ino, info.st_size) != (_int_field(line, 'inode'), _int_field(line, 'size')):
                    continue
                journal = journal or Journal.reopen(path)
                journal.append('done', **{key: line.get(key) for key in ('source', 'destination', 'size', 'mtime_ns', 'inode', 'content_id', 'role')}, ref=line['seq'])
        finally:
            if journal is not None:
                journal.close()

    # ---------------------------------------------------------------- undo

    def undo(
        self,
        root: Path,
        run_id: str,
        *,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> UndoResult:
        path = run_journal_path(root, run_id)
        self._recover_journal(root, path)
        lines = read_journal(path)
        undone = {(line.get('source'), line.get('destination')) for line in lines if line.get('op') == 'undo_done'}
        todo = [line for line in reversed(lines) if line.get('op') == 'done' and (line.get('source'), line.get('destination')) not in undone]
        journal = Journal.reopen(path)
        progress = _Throttle(on_progress, len(todo), self._clock)
        restored = 0
        skips: list[PlannedSkip] = []
        stopped = False
        pending = {(line.get('source'), line.get('destination')) for line in todo}  # moves still in their new place
        try:
            progress.tick(0, force=True)
            for index, line in enumerate(todo, start=1):
                if should_cancel is not None and should_cancel():
                    stopped = True
                    break
                reason = self._undo_one(root, line)
                if reason is None:
                    restored += 1
                    pending.discard((line.get('source'), line.get('destination')))
                    journal.append('undo_done', source=line.get('source'), destination=line.get('destination'))
                else:
                    skips.append(PlannedSkip(str(line.get('destination')), reason))
                    journal.append('undo_skip', source=line.get('source'), destination=line.get('destination'), reason=reason)
                progress.tick(index)
            progress.tick(len(todo), force=True)
            if not stopped:
                self._undo_meta(root, lines, pending)
                self._undo_dirs(root, lines)
        finally:
            journal.close()
        return UndoResult(run_id, restored, skips)

    @staticmethod
    def _undo_one(root: Path, line: dict[str, Any]) -> str | None:
        source, destination = _resolve_inside(root, line.get('source')), _resolve_inside(root, line.get('destination'))
        if source is None or destination is None or not _move_shape_ok(line.get('source'), line.get('destination')):
            return UNKNOWN_FILE
        try:
            info = os.lstat(destination)
        except OSError:
            return SOURCE_MISSING
        if not stat.S_ISREG(info.st_mode):
            return NOT_A_REGULAR_FILE
        if (info.st_size, info.st_mtime_ns, info.st_ino) != (_int_field(line, 'size'), _int_field(line, 'mtime_ns'), _int_field(line, 'inode')):
            return SOURCE_CHANGED
        if os.path.lexists(source):
            return DESTINATION_EXISTS
        try:
            return None if exclusive_move(destination, source) == 'moved' else DESTINATION_EXISTS
        except OSError:
            return MOVE_FAILED

    @staticmethod
    def _undo_meta(root: Path, lines: list[dict[str, Any]], pending: set[tuple[Any, Any]]) -> None:
        """Take back the metadata the run wrote, but only the app's own metadata files, and only while a file is
        still byte-identical to what the run wrote. A week keeps its `materials.json` while a material moved into
        it by this run is still there."""
        waiting_weeks = {'/'.join(str(destination).split('/')[:2]) for _, destination in pending if len(str(destination).split('/')) == 3}
        max_b64 = (MAX_META_BYTES + 2) // 3 * 4
        for line in reversed([line for line in lines if line.get('op') == 'meta_written']):
            kind = _meta_kind(line.get('destination'))
            path = _resolve_inside(root, line.get('destination')) if kind else None
            current = None if path is None else _read_small(path)
            if path is None or current is None or hashlib.sha256(current).hexdigest() != line.get('sha256'):
                continue
            if kind == 'materials' and '/'.join(str(line['destination']).split('/')[:2]) in waiting_weeks:
                continue
            previous_b64 = line.get('previous_b64')
            if isinstance(previous_b64, str):
                try:
                    if len(previous_b64) <= max_b64:
                        _write_bytes_atomically(path, base64.b64decode(previous_b64, validate=True))
                except (binascii.Error, OSError):
                    pass
                continue
            if kind == 'item' and _lecture_in_use(path.parent.parent):
                continue  # downloads added files since: the folder is a living lecture, keep its record
            try:
                os.unlink(path)
            except OSError:
                pass

    @staticmethod
    def _undo_dirs(root: Path, lines: list[dict[str, Any]]) -> None:
        for line in reversed([line for line in lines if line.get('op') == 'mkdir']):
            path = _resolve_inside(root, line.get('destination')) if _mkdir_shape_ok(line.get('destination')) else None
            if path is None:
                continue
            try:
                if stat.S_ISDIR(os.lstat(path).st_mode):
                    os.rmdir(path)  # never recursive: a folder that holds anything stays
            except OSError:
                pass

    # ---------------------------------------------------------------- runs

    def runs(self, root: Path) -> list[dict[str, Any]]:
        """Journals of past runs, newest first."""
        directory = _existing_migrations_dir(root)
        try:
            names = [] if directory is None else sorted((name for name in os.listdir(directory) if name.endswith('.jsonl') and RUN_ID_PATTERN.match(name[:-6])), reverse=True)
        except OSError:
            return []
        runs: list[dict[str, Any]] = []
        for name in names:
            lines = read_journal(directory / name)
            header = next((line for line in lines if line.get('op') == 'header'), None)
            done = [(line.get('source'), line.get('destination')) for line in lines if line.get('op') == 'done']
            undone = {(line.get('source'), line.get('destination')) for line in lines if line.get('op') == 'undo_done'}
            runs.append(
                {
                    'run_id': name[:-6],
                    'at': header.get('at') if header else None,
                    'moved': len(done),
                    'undo_available': any(pair not in undone for pair in done),
                }
            )
        return runs


def _lecture_destination(paths: LecturePaths, found: _Found, has_main_text: bool) -> tuple[Path | None, str | None]:
    """Where one old file of a lecture goes (design 07 § Name mapping), or why it stays."""
    parsed = found.parsed
    role, extension = parsed.role, parsed.suffix.rsplit('.', 1)[-1]
    directory, stem = paths.lecture_dir, paths.stem
    if role == 'material':
        return None, UNKNOWN_FILE
    if parsed.duplicate is not None:  # Q5: a second copy of the lecture's video/audio/subtitles joins the lecture folder
        if role in ('video', 'audio', 'srt'):
            base = f'{stem}{AUDIO_SUFFIX}' if role == 'audio' else stem
            return directory / f'{base} ({parsed.duplicate}).{extension}', None
        return None, DUPLICATE_TRANSCRIPT
    if role == 'video':
        return directory / f'{stem}.{extension}', None
    if role == 'audio':
        return directory / f'{stem}{AUDIO_SUFFIX}.{extension}', None
    if role == 'text_legacy':  # the oldest transcript name: only used when the lecture has no newer transcript anywhere
        return (None, DUPLICATE_TRANSCRIPT) if has_main_text or os.path.lexists(paths.targets['text']) else (paths.targets['text'], None)
    return paths.targets[role], None


def _lecture_in_use(lecture_dir: Path) -> bool:
    """Does the lecture folder hold anything besides its own `.ssu/item.json`?"""
    try:
        names = set(os.listdir(lecture_dir))
        meta_names = set(os.listdir(lecture_dir / META_DIR_NAME)) if META_DIR_NAME in names else set()
    except OSError:
        return True
    return bool(names - {META_DIR_NAME}) or bool(meta_names - {ITEM_FILE_NAME})
