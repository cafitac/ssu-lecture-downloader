"""Subtitle cue helpers shared by every STT path: SRT parse/format and loop repair.

Whisper sometimes gets stuck and emits the same sentence over and over (or a filler such
as "음" dozens of times). The helpers here are pure functions over `TranscriptSegment`
lists so the repair logic can be tested without any server or audio:

1. `find_repeat_runs` finds runs of >= `MIN_REPEAT_RUN` identical consecutive cues, and
   loops that cycle through two or three cues ("영양이 / 있어야 되는 / 영양이 / ...").
2. `repair_windows` turns runs into padded, merged time windows (at most
   `REPAIR_MAX_WINDOW_SECONDS` long, so the server restarts often) to re-transcribe.
3. `replace_window` splices re-transcribed cues (relative times) back into the cue list.
4. `clean_cues` drops filler-only cues and collapses identical neighbours.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel

MIN_REPEAT_RUN = 4
REPAIR_PAD_SECONDS = 10.0
REPAIR_MAX_WINDOW_SECONDS = 120.0
MAX_LOOP_PERIOD = 3
COLLAPSE_GAP_SECONDS = 2.0
LONG_GAP_SECONDS = 45.0
FILLER_TEXTS = frozenset({'음', '음.', '어', '어.', '그'})


class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str


def normalize_text(text: str) -> str:
    return ' '.join(text.split())


# ------------------------------------------------------------------------- SRT

_TIME = r'(\d{1,3}):(\d{2}):(\d{2})[,.](\d{1,3})'
_TIMING_LINE = re.compile(rf'^\s*{_TIME}\s*-->\s*{_TIME}')


def _seconds(hours: str, minutes: str, seconds: str, fraction: str) -> float:
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction.ljust(3, '0')[:3]) / 1000


def parse_srt(text: str) -> list[TranscriptSegment]:
    """Cues of an SRT document; tolerant of missing indexes, CRLF and '.' millisecond separators."""
    cues: list[TranscriptSegment] = []
    start: float | None = None
    end = 0.0
    lines: list[str] = []

    def flush() -> None:
        nonlocal start, lines
        if start is not None:
            body = normalize_text(' '.join(lines))
            if body:
                cues.append(TranscriptSegment(start=start, end=max(end, start), text=body))
        start = None
        lines = []

    for raw_line in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        match = _TIMING_LINE.match(raw_line)
        if match:
            flush()
            groups = match.groups()
            start = _seconds(*groups[:4])
            end = _seconds(*groups[4:])
            continue
        if start is None:
            continue
        if not raw_line.strip():
            flush()
        else:
            lines.append(raw_line.strip())
    flush()
    return cues


def _srt_timestamp(seconds: float) -> str:
    total_ms = max(0, round(seconds * 1000))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f'{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}'


def format_srt(segments: Iterable[TranscriptSegment]) -> str:
    blocks: list[str] = []
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        blocks.append(f'{len(blocks) + 1}\n{_srt_timestamp(segment.start)} --> {_srt_timestamp(max(segment.end, segment.start))}\n{text}\n')
    return '\n'.join(blocks)


def segments_from_verbose_json(payload: Any) -> list[TranscriptSegment]:
    """Cues of an OpenAI-style `verbose_json` body (or a single cue for a text-only body)."""
    if not isinstance(payload, dict):
        return []
    raw_segments = payload.get('segments')
    if isinstance(raw_segments, list):
        cues = []
        for item in raw_segments:
            if not isinstance(item, dict):
                continue
            text = normalize_text(str(item.get('text') or ''))
            if text:
                start = float(item.get('start') or 0)
                cues.append(TranscriptSegment(start=start, end=max(float(item.get('end') or start), start), text=text))
        return cues
    text = normalize_text(str(payload.get('text') or ''))
    return [TranscriptSegment(start=0.0, end=float(payload.get('duration') or 0), text=text)] if text else []


# ------------------------------------------------------------------ loop repair


def find_repeat_runs(cues: Sequence[TranscriptSegment], min_run: int = MIN_REPEAT_RUN) -> list[tuple[int, int]]:
    """`(first, last)` inclusive index pairs of looped cues, sorted and merged when they overlap.

    A loop is >= `min_run` identical consecutive cues, or a cycle of 2..`MAX_LOOP_PERIOD`
    different cues repeated >= `min_run` times.
    """
    texts = [normalize_text(cue.text) for cue in cues]
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(texts):
        end = index
        while end + 1 < len(texts) and texts[end + 1] == texts[index]:
            end += 1
        if end - index + 1 >= min_run:
            runs.append((index, end))
        index = end + 1
    for period in range(2, MAX_LOOP_PERIOD + 1):
        index = period
        while index < len(texts):
            if texts[index] != texts[index - period]:
                index += 1
                continue
            end = index
            while end + 1 < len(texts) and texts[end + 1] == texts[end + 1 - period]:
                end += 1
            first = index - period
            if end - first + 1 >= min_run * period and len(set(texts[first : end + 1])) > 1:
                runs.append((first, end))
            index = end + 1
    merged: list[tuple[int, int]] = []
    for first, last in sorted(runs):
        if merged and first <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def repair_windows(
    cues: Sequence[TranscriptSegment],
    runs: Sequence[tuple[int, int]],
    *,
    pad: float = REPAIR_PAD_SECONDS,
    duration: float | None = None,
    max_window: float = REPAIR_MAX_WINDOW_SECONDS,
) -> list[tuple[float, float]]:
    """Time windows (start, end) covering each run plus `pad` on both sides.

    Overlaps are merged, then windows longer than `max_window` are split into equal parts:
    re-transcribing a long loop in one request tends to loop again.
    """
    windows: list[tuple[float, float]] = []
    for first, last in runs:
        start = max(0.0, cues[first].start - pad)
        end = cues[last].end + pad
        if duration is not None and duration > 0:
            end = min(end, duration)
        if end <= start:
            continue
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    split: list[tuple[float, float]] = []
    for start, end in windows:
        parts = max(1, math.ceil((end - start) / max_window))
        step = (end - start) / parts
        split.extend((start + step * part, end if part == parts - 1 else start + step * (part + 1)) for part in range(parts))
    return split


def shift_cues(cues: Iterable[TranscriptSegment], offset: float) -> list[TranscriptSegment]:
    return [TranscriptSegment(start=cue.start + offset, end=cue.end + offset, text=cue.text) for cue in cues]


def replace_window(
    cues: Sequence[TranscriptSegment],
    start: float,
    end: float,
    replacement: Iterable[TranscriptSegment],
) -> list[TranscriptSegment]:
    """Swap the cues starting inside [start, end) for `replacement`.

    `replacement` carries times relative to the cut segment: they are shifted by `start`
    and cues that then begin at or after `end` are dropped (the cut audio can run past the
    window's edge by a little).
    """
    shifted = [cue for cue in shift_cues(replacement, start) if cue.start < end]
    kept = [cue for cue in cues if not (start <= cue.start < end)]
    return sorted([*kept, *shifted], key=lambda cue: (cue.start, cue.end))


def drop_fillers(cues: Iterable[TranscriptSegment]) -> list[TranscriptSegment]:
    return [cue for cue in cues if normalize_text(cue.text) not in FILLER_TEXTS and normalize_text(cue.text)]


def collapse_repeats(cues: Iterable[TranscriptSegment], max_gap: float = COLLAPSE_GAP_SECONDS) -> list[TranscriptSegment]:
    """Merge identical consecutive cues that are less than `max_gap` seconds apart."""
    collapsed: list[TranscriptSegment] = []
    for cue in cues:
        text = normalize_text(cue.text)
        if collapsed and normalize_text(collapsed[-1].text) == text and cue.start - collapsed[-1].end < max_gap:
            collapsed[-1] = TranscriptSegment(start=collapsed[-1].start, end=max(collapsed[-1].end, cue.end), text=collapsed[-1].text)
        else:
            collapsed.append(TranscriptSegment(start=cue.start, end=cue.end, text=text))
    return collapsed


def clean_cues(cues: Iterable[TranscriptSegment]) -> list[TranscriptSegment]:
    """Collapse repeats, drop filler-only cues, and collapse again (removing a filler can make neighbours identical)."""
    return collapse_repeats(drop_fillers(collapse_repeats(cues)))


def max_repeat_run(cues: Sequence[TranscriptSegment], max_gap: float = COLLAPSE_GAP_SECONDS) -> int:
    """Longest chain of identical consecutive cues that follow each other within `max_gap` seconds."""
    longest = 0
    current = 0
    previous: TranscriptSegment | None = None
    for cue in cues:
        if previous is not None and normalize_text(previous.text) == normalize_text(cue.text) and cue.start - previous.end < max_gap:
            current += 1
        else:
            current = 1
        longest = max(longest, current)
        previous = cue
    return longest


def find_gaps(cues: Sequence[TranscriptSegment], *, duration: float | None = None, threshold: float = LONG_GAP_SECONDS) -> list[tuple[float, float]]:
    """Silent stretches longer than `threshold` seconds between cues (and before the first / after the last)."""
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for cue in cues:
        if cue.start - cursor > threshold:
            gaps.append((cursor, cue.start))
        cursor = max(cursor, cue.end)
    if duration is not None and duration - cursor > threshold:
        gaps.append((cursor, duration))
    return gaps


def format_clock(seconds: float) -> str:
    total = max(0, int(seconds))
    return f'{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}'
