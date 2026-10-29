from __future__ import annotations

from ssu_lecture_downloader.core import stt_cues
from ssu_lecture_downloader.core.stt_cues import TranscriptSegment


def cue(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


def looped(text: str, count: int, start: float, step: float = 2.0) -> list[TranscriptSegment]:
    return [cue(start + i * step, start + (i + 1) * step, text) for i in range(count)]


def test_srt_round_trip_and_tolerant_parsing():
    text = '1\n00:00:01,500 --> 00:00:03,000\n안녕하세요\n두 줄입니다\n\n2\r\n00:01:00.250 --> 00:01:02.000\r\n다음 문장\r\n'

    cues = stt_cues.parse_srt(text)

    assert [(c.start, c.end, c.text) for c in cues] == [(1.5, 3.0, '안녕하세요 두 줄입니다'), (60.25, 62.0, '다음 문장')]
    again = stt_cues.parse_srt(stt_cues.format_srt(cues))
    assert again == cues
    assert '00:01:00,250 --> 00:01:02,000' in stt_cues.format_srt(cues)


def test_verbose_json_segments_and_text_only_fallback():
    assert stt_cues.segments_from_verbose_json({'segments': [{'start': 0, 'end': 2, 'text': ' 안녕 '}, {'start': 2, 'end': 3, 'text': ''}]}) == [cue(0, 2, '안녕')]
    assert stt_cues.segments_from_verbose_json({'text': '전체 문장', 'duration': 9}) == [cue(0, 9, '전체 문장')]
    assert stt_cues.segments_from_verbose_json([]) == []


def test_find_repeat_runs_needs_four_identical_neighbours():
    cues = [cue(0, 1, '가'), *looped('반복', 3, 1), cue(7, 8, '나'), *looped('루프', 4, 8), *looped('음', 6, 16), cue(40, 41, '끝')]

    assert stt_cues.find_repeat_runs(cues) == [(5, 8), (9, 14)]
    assert stt_cues.find_repeat_runs(cues, min_run=3) == [(1, 3), (5, 8), (9, 14)]
    assert stt_cues.find_repeat_runs([]) == []


def test_find_repeat_runs_catches_two_and_three_cue_cycles():
    two = [cue(i, i + 1, '영양이' if i % 2 else '있어야 되는') for i in range(20, 36)]
    three = [cue(i, i + 1, ('가', '나', '다')[i % 3]) for i in range(100, 112)]
    speech = [cue(0, 1, '처음'), cue(1, 2, '그리고'), cue(2, 3, '처음'), cue(3, 4, '그리고'), cue(4, 5, '끝')]
    cues = [*speech, *two, cue(60, 61, '중간'), *three]

    assert stt_cues.find_repeat_runs(cues) == [(5, 20), (22, 33)]
    assert stt_cues.find_repeat_runs(speech) == []


def test_repair_windows_split_long_windows():
    cues = [cue(i, i + 1, '영양이' if i % 2 else '있어야 되는') for i in range(0, 300)]

    windows = stt_cues.repair_windows(cues, stt_cues.find_repeat_runs(cues), pad=0, max_window=120)

    assert windows == [(0.0, 100.0), (100.0, 200.0), (200.0, 300.0)]


def test_repair_windows_pad_clamp_and_merge():
    cues = [cue(0, 4, '앞'), *looped('루프', 4, 4), cue(30, 32, '중간'), *looped('또', 4, 40), cue(52, 53, '다음'), *looped('멀리', 4, 200)]
    runs = stt_cues.find_repeat_runs(cues)

    windows = stt_cues.repair_windows(cues, runs, pad=10, duration=215)

    # Run 1 spans 4-12 -> (0, 22) after clamping at 0; run 2 spans 40-48 -> (30, 58) (disjoint);
    # run 3 spans 200-208 -> (190, 215) clamped to the audio duration.
    assert windows == [(0.0, 22.0), (30.0, 58.0), (190.0, 215.0)]
    merged = stt_cues.repair_windows(cues, runs[:2], pad=20)
    assert merged == [(0.0, 68.0)]


def test_replace_window_shifts_drops_past_end_and_keeps_outside_cues():
    cues = [cue(0, 5, '앞'), *looped('루프', 5, 20), cue(45, 50, '뒤')]
    replacement = [cue(2, 6, '복구 하나'), cue(8, 12, '복구 둘'), cue(31, 35, '창 밖')]

    result = stt_cues.replace_window(cues, 10, 40, replacement)

    assert [(c.start, c.end, c.text) for c in result] == [(0, 5, '앞'), (12, 16, '복구 하나'), (18, 22, '복구 둘'), (45, 50, '뒤')]


def test_replace_window_only_replaces_cues_starting_inside_the_window():
    cues = [cue(5, 12, '경계 앞'), cue(10, 11, '창 안'), cue(40, 41, '창 끝')]

    result = stt_cues.replace_window(cues, 10, 40, [])

    assert [c.text for c in result] == ['경계 앞', '창 끝']


def test_drop_fillers_and_collapse_repeats():
    cues = [cue(0, 1, '음'), cue(1, 2, '음.'), cue(2, 3, ' 어 '), cue(3, 4, '그'), cue(4, 5, '그래서'), cue(5, 6, '그래서'), cue(9, 10, '그래서')]

    assert [c.text for c in stt_cues.drop_fillers(cues)] == ['그래서', '그래서', '그래서']
    collapsed = stt_cues.collapse_repeats(stt_cues.drop_fillers(cues))
    # The third '그래서' starts 3 s after the previous one ended, so it stays a separate cue.
    assert [(c.start, c.end, c.text) for c in collapsed] == [(4, 6, '그래서'), (9, 10, '그래서')]


def test_clean_cues_leaves_no_run_longer_than_one():
    cues = [cue(0, 2, '시작'), *looped('같은 문장', 123, 2, step=1.5), *looped('음', 49, 190, step=0.5), cue(215, 217, '시작'), cue(217, 219, '끝'), cue(219.5, 221, '끝'), cue(221, 222, '음'), cue(222, 224, '끝')]

    cleaned = stt_cues.clean_cues(cues)

    assert stt_cues.max_repeat_run(cleaned) == 1
    assert [c.text for c in cleaned] == ['시작', '같은 문장', '시작', '끝']
    assert cleaned[1].start == 2 and cleaned[1].end == 2 + 123 * 1.5
    assert cleaned[3].end == 224  # the filler between the last two 끝 cues was dropped, then they merged


def test_max_repeat_run_counts_only_close_neighbours():
    far = [cue(0, 1, '같음'), cue(10, 11, '같음'), cue(20, 21, '같음')]

    assert stt_cues.max_repeat_run(far) == 1
    assert stt_cues.max_repeat_run(looped('같음', 4, 0)) == 4


def test_find_gaps_reports_long_silences_including_edges():
    cues = [cue(50, 52, '첫'), cue(60, 62, '둘'), cue(130, 131, '셋')]

    assert stt_cues.find_gaps(cues, duration=200) == [(0.0, 50.0), (62.0, 130.0), (131.0, 200.0)]
    assert stt_cues.find_gaps(cues, duration=170, threshold=45) == [(0.0, 50.0), (62.0, 130.0)]
