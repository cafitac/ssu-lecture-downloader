from __future__ import annotations

import pytest
from stt_fakes import write_silent_wav

from ssu_lecture_downloader import postprocess_media
from ssu_lecture_downloader.stt_remote import wav_duration_seconds

pytestmark = pytest.mark.skipif(postprocess_media.bundled_or_system_ffmpeg() is None, reason='ffmpeg is not available')


def test_convert_and_cut_use_the_stt_wav_format(tmp_path):
    source = tmp_path / 'in.wav'
    write_silent_wav(source, 30, rate=8000)
    converted = tmp_path / 'out.wav'
    piece = tmp_path / 'piece.wav'

    postprocess_media.convert_to_stt_wav(source, converted)
    postprocess_media.cut_audio_segment(converted, piece, 10, 20)

    assert wav_duration_seconds(converted) == pytest.approx(30, abs=0.1)
    assert wav_duration_seconds(piece) == pytest.approx(10, abs=0.2)
    import wave

    with wave.open(str(converted), 'rb') as handle:
        assert (handle.getframerate(), handle.getnchannels(), handle.getsampwidth()) == (16000, 1, 2)
