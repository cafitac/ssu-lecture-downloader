"""Hidden library metadata and the legacy file-name parser.

On disk (design 04, schema 1):

- `<lecture dir>/.ssu/item.json`: dedup authority of one lecture (content id, source, versions).
- `<week dir>/.ssu/materials.json`: content id -> file name of the materials in that week folder.
- `<lecture dir>/.ssu/transcript.md|json`: written by the transcript writer, not by this module.

Everything read from the download folder is untrusted (the folder is user-writable): reads are size
bounded, schema checked, names must be plain (no separator, no `..`), symlinks are not followed, and
anything invalid is ignored (the caller treats the folder as foreign) instead of raising.
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
import threading
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ssu_lecture_downloader import __version__

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
LAYOUT_VERSION = 1
META_DIR_NAME = '.ssu'
ITEM_FILE_NAME = 'item.json'
MATERIALS_FILE_NAME = 'materials.json'
MAX_ITEM_JSON_BYTES = 64 * 1024
MAX_MATERIALS_JSON_BYTES = 256 * 1024
MAX_NAME_BYTES = 255

CREATED_BY_VALUES = frozenset({'download', 'migration'})
MATERIAL_KINDS = frozenset({'material', 'canvas_file'})

VIDEO_EXTENSIONS = frozenset({'mp4', 'm4v', 'mov', 'mkv', 'webm'})
AUDIO_EXTENSIONS = frozenset({'mp3', 'm4a', 'wav', 'aac', 'ogg', 'flac'})


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def is_plain_name(value: object) -> bool:
    """A single path component that cannot leave its folder: no separator, no `..`, no hidden name."""
    if not isinstance(value, str) or not value or value != value.strip():
        return False
    if '/' in value or '\\' in value or '\x00' in value or value.startswith('.'):
        return False
    return len(value.encode('utf-8')) <= MAX_NAME_BYTES


# --------------------------------------------------------------------------- atomic JSON I/O

_write_counter = 0
_write_counter_lock = threading.Lock()


def _tmp_sibling(path: Path) -> Path:
    global _write_counter
    with _write_counter_lock:
        _write_counter += 1
        serial = _write_counter
    return path.with_name(f'{path.name}.{os.getpid()}-{serial}.tmp')


def ensure_meta_dir(parent: Path) -> Path:
    """`<parent>/.ssu`, created if missing. A symlink in its place is refused (never followed)."""
    meta_dir = parent / META_DIR_NAME
    parent.mkdir(parents=True, exist_ok=True)
    try:
        meta_dir.mkdir(exist_ok=True)
    except FileExistsError:
        pass
    if meta_dir.is_symlink() or not meta_dir.is_dir():
        raise OSError(f'{META_DIR_NAME} is not a plain folder')
    return meta_dir


def write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    """tmp sibling + `os.replace`: readers see the old or the new file, never a partial one."""
    tmp = _tmp_sibling(path)
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def read_bounded_json(path: Path, max_bytes: int) -> dict[str, Any] | None:
    """The JSON object in `path`, or None when missing, not a regular file, too big, unreadable or not an object."""
    try:
        info = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    if info.st_size > max_bytes:
        logger.debug('library metadata ignored (too large): %s', path.name)
        return None
    try:
        with open(path, 'rb') as handle:
            raw = handle.read(max_bytes + 1)
        if len(raw) > max_bytes:
            return None
        data = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError):
        logger.debug('library metadata ignored (unreadable): %s', path.name)
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- item.json


@dataclass(frozen=True)
class ItemMeta:
    """`.ssu/item.json` (design 04 `item_json`)."""

    content_id: str
    stem: str
    week_folder: str
    course_id: str
    module_item_id: str | None = None
    kind: str = 'lecture'
    lms_title: str | None = None
    resolved_title: str | None = None
    created_by: str = 'download'
    app_version: str = __version__
    layout_version: int = LAYOUT_VERSION
    created_at: str = ''
    updated_at: str = ''

    def to_json(self) -> dict[str, Any]:
        return {
            'schema': SCHEMA_VERSION,
            'content_id': self.content_id,
            'module_item_id': self.module_item_id,
            'course_id': self.course_id,
            'kind': self.kind,
            'lms_title': self.lms_title,
            'resolved_title': self.resolved_title,
            'stem': self.stem,
            'week_folder': self.week_folder,
            'created_by': self.created_by,
            'app_version': self.app_version,
            'layout_version': self.layout_version,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def parse_item_meta(data: dict[str, Any]) -> ItemMeta | None:
    """Validate an `item.json` object; None when it does not satisfy schema 1."""
    if data.get('schema') != SCHEMA_VERSION:
        return None
    content_id = data.get('content_id')
    stem = data.get('stem')
    week_folder = data.get('week_folder')
    course_id = data.get('course_id')
    if not isinstance(content_id, str) or not content_id.strip() or len(content_id) > 200:
        return None
    if not is_plain_name(stem) or not is_plain_name(week_folder):
        return None
    if not isinstance(course_id, (str, int)) or isinstance(course_id, bool):
        return None
    kind = data.get('kind')
    created_by = data.get('created_by')
    layout_version = data.get('layout_version')
    return ItemMeta(
        content_id=content_id,
        stem=stem,  # type: ignore[arg-type]
        week_folder=week_folder,  # type: ignore[arg-type]
        course_id=str(course_id),
        module_item_id=None if data.get('module_item_id') is None else str(data.get('module_item_id')),
        kind=kind if isinstance(kind, str) else 'lecture',
        lms_title=_optional_text(data.get('lms_title')),
        resolved_title=_optional_text(data.get('resolved_title')),
        created_by=created_by if created_by in CREATED_BY_VALUES else 'download',
        app_version=_optional_text(data.get('app_version')) or '',
        layout_version=layout_version if isinstance(layout_version, int) and not isinstance(layout_version, bool) else LAYOUT_VERSION,
        created_at=_optional_text(data.get('created_at')) or '',
        updated_at=_optional_text(data.get('updated_at')) or '',
    )


def read_item_meta(lecture_dir: Path) -> ItemMeta | None:
    """The valid `.ssu/item.json` of a lecture folder, else None (the folder is then foreign)."""
    meta_dir = lecture_dir / META_DIR_NAME
    if meta_dir.is_symlink():
        return None
    data = read_bounded_json(meta_dir / ITEM_FILE_NAME, MAX_ITEM_JSON_BYTES)
    return None if data is None else parse_item_meta(data)


_item_write_lock = threading.Lock()


def write_item_meta(lecture_dir: Path, meta: ItemMeta) -> ItemMeta:
    """Create `.ssu/item.json`, or refresh it when a field other than the timestamps changed.

    Returns what is on disk afterwards. `created_at` of an existing record is kept.
    """
    with _item_write_lock:
        existing = read_item_meta(lecture_dir)
        now = utc_now()
        if existing is not None and existing.content_id == meta.content_id:
            merged = ItemMeta(
                **{
                    **meta.__dict__,
                    'resolved_title': meta.resolved_title or existing.resolved_title,
                    'created_by': existing.created_by,
                    'created_at': existing.created_at or now,
                    'updated_at': existing.updated_at or now,
                }
            )
            if _same_content(existing, merged):
                return existing
            merged = ItemMeta(**{**merged.__dict__, 'updated_at': now})
        else:
            merged = ItemMeta(**{**meta.__dict__, 'created_at': meta.created_at or now, 'updated_at': meta.updated_at or now})
        meta_dir = ensure_meta_dir(lecture_dir)
        write_json_atomically(meta_dir / ITEM_FILE_NAME, merged.to_json())
        return merged


def _same_content(left: ItemMeta, right: ItemMeta) -> bool:
    ignore = {'created_at', 'updated_at', 'app_version'}
    return all(getattr(left, name) == getattr(right, name) for name in left.__dict__ if name not in ignore)


# --------------------------------------------------------------------------- materials.json


@dataclass(frozen=True)
class MaterialMeta:
    file_name: str
    kind: str = 'material'
    title: str | None = None
    created_by: str = 'download'
    updated_at: str = ''

    def to_json(self) -> dict[str, Any]:
        return {'file_name': self.file_name, 'kind': self.kind, 'title': self.title, 'created_by': self.created_by, 'updated_at': self.updated_at}


def read_materials_meta(week_dir: Path) -> dict[str, MaterialMeta]:
    """content id -> entry of `<week dir>/.ssu/materials.json`; invalid entries are dropped, an invalid file is empty."""
    meta_dir = week_dir / META_DIR_NAME
    if meta_dir.is_symlink():
        return {}
    data = read_bounded_json(meta_dir / MATERIALS_FILE_NAME, MAX_MATERIALS_JSON_BYTES)
    if data is None or data.get('schema') != SCHEMA_VERSION or not isinstance(data.get('entries'), dict):
        return {}
    entries: dict[str, MaterialMeta] = {}
    for content_id, raw in data['entries'].items():
        if not isinstance(content_id, str) or not content_id or not isinstance(raw, dict) or not is_plain_name(raw.get('file_name')):
            continue
        kind = raw.get('kind')
        created_by = raw.get('created_by')
        entries[content_id] = MaterialMeta(
            file_name=raw['file_name'],
            kind=kind if kind in MATERIAL_KINDS else 'material',
            title=_optional_text(raw.get('title')),
            created_by=created_by if created_by in CREATED_BY_VALUES else 'download',
            updated_at=_optional_text(raw.get('updated_at')) or '',
        )
    return entries


_materials_write_lock = threading.Lock()


def upsert_material_meta(week_dir: Path, content_id: str, entry: MaterialMeta) -> None:
    """Add or replace one entry of the week's `materials.json` (read-merge-write, atomic)."""
    with _materials_write_lock:
        entries = read_materials_meta(week_dir)
        current = entries.get(content_id)
        if current is not None and current.file_name == entry.file_name and current.kind == entry.kind:
            return
        entries[content_id] = MaterialMeta(entry.file_name, entry.kind, entry.title, entry.created_by, entry.updated_at or utc_now())
        meta_dir = ensure_meta_dir(week_dir)
        payload = {'schema': SCHEMA_VERSION, 'entries': {key: value.to_json() for key, value in sorted(entries.items())}}
        write_json_atomically(meta_dir / MATERIALS_FILE_NAME, payload)


# --------------------------------------------------------------------------- legacy file names

# `<title> [<id>]( (n))?<suffix>` (design 07 § Legacy name parser). The last `[...]` group wins.
_LEGACY_NAME = re.compile(
    r'^(?P<title>.+?) \[(?P<id>[^\[\]/]+)\](?P<dup> \((?P<n>[0-9]{1,3})\))?(?P<suffix>\.transcript\.(?:txt|md|json)|\.[A-Za-z0-9]{1,10})$'
)


@dataclass(frozen=True)
class LegacyName:
    title: str
    content_id: str
    duplicate: int | None
    suffix: str  # lower-case, with the leading dot (`.mp4`, `.transcript.txt`)
    role: str  # video | audio | srt | text | text_legacy | md | json | material


def _legacy_role(suffix: str) -> str:
    if suffix == '.transcript.txt':
        return 'text'
    if suffix == '.transcript.md':
        return 'md'
    if suffix == '.transcript.json':
        return 'json'
    extension = suffix.lstrip('.')
    if extension in VIDEO_EXTENSIONS:
        return 'video'
    if extension in AUDIO_EXTENSIONS:
        return 'audio'
    if extension == 'srt':
        return 'srt'
    if extension == 'txt':
        return 'text_legacy'
    return 'material'


def parse_legacy_name(name: str) -> LegacyName | None:
    """Attribute an old flat-layout file name to a content id; None when it carries no usable marker."""
    match = _LEGACY_NAME.match(unicodedata.normalize('NFC', name))
    if match is None:
        return None
    content_id = match.group('id')
    if content_id == 'content':  # the old fallback when the LMS gave no id: not attributable
        return None
    suffix = match.group('suffix').lower()
    duplicate = int(match.group('n')) if match.group('n') is not None else None
    return LegacyName(title=match.group('title'), content_id=content_id, duplicate=duplicate, suffix=suffix, role=_legacy_role(suffix))


@dataclass
class LegacyFiles:
    """Old flat-layout files of one content id, by role (the `(n)` copies kept apart)."""

    roles: dict[str, Path] = field(default_factory=dict)
    audio: dict[str, Path] = field(default_factory=dict)  # extension -> path
    duplicates: list[Path] = field(default_factory=list)
