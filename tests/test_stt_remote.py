from __future__ import annotations

import json
import threading
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import pytest
from stt_fakes import SUPPORTS_LOCAL_STT, FakeSttServer, RecordedRequest, json_text, sleepy, srt, write_silent_wav
from ssu_lecture_downloader.core import postprocess_media
from ssu_lecture_downloader.core.stt import TranscriptionCancelled, TranscriptionRequest, transcribe_audio
from ssu_lecture_downloader.core.stt_cues import TranscriptSegment
from ssu_lecture_downloader.core.stt_remote import (
    OpenAICompatibleProvider,
    RemoteSTTError,
    RemoteSTTService,
    WhisperCppServerProvider,
    repair_repeated_cues,
    response_budget_seconds,
)
from ssu_lecture_downloader.platforms.macos import MacOSPlatform
from ssu_lecture_downloader.core.stt_remote_config import RemoteSTTSettingsStore


def wav(tmp_path: Path, seconds: float = 3) -> Path:
    path = tmp_path / 'audio.wav'
    write_silent_wav(path, seconds)
    return path


def simulate_work(seconds: float) -> None:
    """Latency of simulated server work. Never used to wait for a test condition."""
    threading.Event().wait(seconds)


def cue(start: float, end: float, text: str) -> TranscriptSegment:
    return TranscriptSegment(start=start, end=end, text=text)


# ------------------------------------------------------------ whisper.cpp kind


def test_whisper_cpp_posts_the_proven_form_fields_and_parses_srt(tmp_path):
    body = srt((0, 2.5, '안녕하세요'), (3, 5, '두 번째'))
    with FakeSttServer(lambda request: (200, 'text/plain', body)) as server:
        cues = WhisperCppServerProvider(server.url, language='ko').transcribe(wav(tmp_path), temperature=0.0)

    assert [c.text for c in cues] == ['안녕하세요', '두 번째']
    request = server.requests[0]
    assert (request.method, request.path) == ('POST', '/inference')
    assert request.fields == {'temperature': '0.0', 'response_format': 'srt', 'language': 'ko'}
    assert request.file_size == (tmp_path / 'audio.wav').stat().st_size
    assert 'authorization' not in request.headers


def test_whisper_cpp_http_error_and_json_error_are_reported(tmp_path):
    with FakeSttServer(lambda request: (500, 'text/plain', 'boom')) as server:
        with pytest.raises(RemoteSTTError) as caught:
            WhisperCppServerProvider(server.url).transcribe(wav(tmp_path))
    assert caught.value.code == 'stt_remote_http_error' and 'HTTP 500' in caught.value.message

    with FakeSttServer(lambda request: (200, 'application/json', json_text({'error': 'failed to read audio'}))) as server:
        with pytest.raises(RemoteSTTError) as caught:
            WhisperCppServerProvider(server.url).transcribe(wav(tmp_path))
    assert 'failed to read audio' in caught.value.message


def test_whisper_cpp_probe_wants_200_on_root():
    with FakeSttServer(lambda request: (200, 'text/html', '<html>whisper</html>')) as server:
        WhisperCppServerProvider(server.url).probe()
    assert (server.requests[0].method, server.requests[0].path) == ('GET', '/')

    with FakeSttServer(lambda request: (404, 'text/plain', '')) as server:
        with pytest.raises(RemoteSTTError):
            WhisperCppServerProvider(server.url).probe()


def test_unreachable_server_is_a_stable_error(tmp_path):
    with pytest.raises(RemoteSTTError) as caught:
        WhisperCppServerProvider('http://127.0.0.1:9', timeout=2).transcribe(wav(tmp_path))
    assert caught.value.code == 'stt_remote_unreachable'


def test_a_slow_server_can_be_cancelled_without_waiting_for_it(tmp_path):
    started = time.monotonic()
    with FakeSttServer(sleepy(5, lambda request: (200, 'text/plain', srt((0, 1, 'late'))))) as server:
        calls = {'n': 0}

        def should_cancel() -> bool:
            calls['n'] += 1
            return calls['n'] > 2

        with pytest.raises(TranscriptionCancelled):
            WhisperCppServerProvider(server.url).transcribe(wav(tmp_path), should_cancel=should_cancel)
    assert time.monotonic() - started < 3


# ------------------------------------------------------------- OpenAI kind


def test_openai_compatible_sends_model_language_format_and_bearer(tmp_path):
    with FakeSttServer(lambda request: (200, 'text/plain', srt((0, 2, '오픈AI 문장')))) as server:
        provider = OpenAICompatibleProvider(server.url + '/v1', model='whisper-large', api_key='sk-secret', language='ko')
        cues = provider.transcribe(wav(tmp_path), temperature=0.2)

    assert [c.text for c in cues] == ['오픈AI 문장']
    request = server.requests[0]
    assert request.path == '/v1/audio/transcriptions'
    assert request.headers['authorization'] == 'Bearer sk-secret'
    assert request.fields == {'model': 'whisper-large', 'language': 'ko', 'response_format': 'srt', 'temperature': '0.2'}


def test_openai_compatible_falls_back_to_verbose_json_when_srt_is_rejected(tmp_path):
    def respond(request: RecordedRequest):
        if request.fields['response_format'] == 'srt':
            return 400, 'application/json', json_text({'error': {'message': "response_format 'srt' is not supported"}})
        return 200, 'application/json', json_text({'segments': [{'start': 1.0, 'end': 2.0, 'text': ' 제이슨 '}]})

    with FakeSttServer(respond) as server:
        provider = OpenAICompatibleProvider(server.url)
        first = provider.transcribe(wav(tmp_path))
        second = provider.transcribe(wav(tmp_path))

    assert [c.text for c in first] == [c.text for c in second] == ['제이슨']
    assert [r.fields['response_format'] for r in server.requests] == ['srt', 'verbose_json', 'verbose_json']


def test_openai_compatible_auth_failure_and_probe_variants(tmp_path):
    with FakeSttServer(lambda request: (401, 'application/json', '{}')) as server:
        with pytest.raises(RemoteSTTError) as caught:
            OpenAICompatibleProvider(server.url, api_key='bad').transcribe(wav(tmp_path))
        assert caught.value.code == 'stt_remote_auth_failed'
        with pytest.raises(RemoteSTTError) as caught:
            OpenAICompatibleProvider(server.url, api_key='bad').probe()
        assert caught.value.code == 'stt_remote_auth_failed'

    with FakeSttServer(lambda request: (200, 'application/json', json_text({'data': []}))) as server:
        OpenAICompatibleProvider(server.url + '/v1', api_key='ok').probe()
    assert server.requests[0].path == '/v1/models'
    assert server.requests[0].headers['authorization'] == 'Bearer ok'

    # A server without a model listing still counts as reachable through HEAD.
    with FakeSttServer(lambda request: (404 if request.path != '/' else 200, 'text/plain', '')) as server:
        OpenAICompatibleProvider(server.url).probe()
    assert [r.method for r in server.requests] == ['GET', 'HEAD']


# ------------------------------------------------------------- loop repair


def test_repair_repeated_cues_retranscribes_padded_windows_and_gives_up_after_the_passes():
    cues = [cue(0, 5, '앞'), *[cue(20 + i, 21 + i, '루프') for i in range(8)], cue(60, 62, '뒤')]
    calls: list[tuple[float, float, float]] = []

    def retranscribe(start: float, end: float, temperature: float):
        calls.append((start, end, temperature))
        return [cue(12, 15, '복구'), cue(40, 41, '창 밖')]

    events: list[dict] = []
    repaired, warnings = repair_repeated_cues(cues, retranscribe, duration=70, on_progress=events.append)

    assert calls == [(10.0, 38.0, 0.2)]
    assert [(c.start, c.text) for c in repaired] == [(0, '앞'), (22, '복구'), (60, '뒤')]
    assert warnings == []
    assert [e['stage'] for e in events] == ['transcript_repair']
    assert (events[0]['repair_index'], events[0]['repair_total'], events[0]['window_start'], events[0]['window_end']) == (1, 1, 10.0, 38.0)


def test_repair_second_pass_uses_a_higher_temperature_and_failures_become_warnings():
    cues = [*[cue(i, i + 1, '루프') for i in range(6)]]
    temperatures: list[float] = []

    def still_looping(start: float, end: float, temperature: float):
        temperatures.append(temperature)
        return [cue(i, i + 1, '루프') for i in range(5)]

    repaired, _ = repair_repeated_cues(cues, still_looping, duration=30)
    assert temperatures == [0.2, 0.4]
    assert len(repaired) >= 4  # still looping; the caller's clean_cues collapses it

    def failing(start: float, end: float, temperature: float):
        raise RemoteSTTError('stt_remote_http_error', 'HTTP 500')

    kept, warnings = repair_repeated_cues(cues, failing, duration=30)
    assert kept == cues
    assert len(warnings) == 2 and 'HTTP 500' in warnings[0]


def test_repair_stops_when_cancelled():
    cues = [*[cue(i, i + 1, '루프') for i in range(6)]]

    with pytest.raises(TranscriptionCancelled):
        repair_repeated_cues(cues, lambda *a: [], duration=30, should_cancel=lambda: True)


# ---------------------------------------------------------- full pipeline


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """Replace the two ffmpeg helpers: the 'converted' WAV is 60 s long, cuts are tiny WAVs."""
    cuts: list[tuple[float, float]] = []

    def convert(input_path, output_path, should_cancel=None):
        write_silent_wav(output_path, 60)

    def cut(input_path, output_path, start, end, should_cancel=None):
        cuts.append((start, end))
        write_silent_wav(output_path, max(1, end - start))

    monkeypatch.setattr(postprocess_media, 'convert_to_stt_wav', convert)
    monkeypatch.setattr(postprocess_media, 'cut_audio_segment', cut)
    return cuts


def service_for(tmp_path: Path, url: str, **config) -> RemoteSTTService:
    service = RemoteSTTService(RemoteSTTSettingsStore(tmp_path / 'stt.json', writer=MacOSPlatform()))
    service.update({'enabled': True, 'kind': 'whisper_cpp_server', 'base_url': url, **config})
    return service


def test_pipeline_repairs_a_loop_cleans_cues_and_writes_all_transcript_files(tmp_path, fake_ffmpeg):
    first_pass = srt(
        (0, 4, '안녕하세요'),
        (4, 6, '음'),
        *[(20 + i, 21 + i, '같은 문장') for i in range(10)],
        (59.9, 60, '마무리'),
    )
    repair = srt((2, 4, '복구된 문장'), (30, 32, '창 밖 문장'))

    def respond(request: RecordedRequest):
        return 200, 'text/plain', repair if request.fields['temperature'] == '0.2' else first_pass

    audio = tmp_path / 'lecture.mp3'
    audio.write_bytes(b'mp3')
    events: list[dict] = []
    with FakeSttServer(respond) as server:
        service = service_for(tmp_path, server.url)
        result = transcribe_audio(TranscriptionRequest(audio_path=audio, output_dir=tmp_path, overwrite=True), on_progress=events.append, remote=service, stt_capability=SUPPORTS_LOCAL_STT)

    assert result.ok, result.message
    assert fake_ffmpeg == [(10.0, 40.0)]
    assert [r.fields['temperature'] for r in server.requests] == ['0.0', '0.2']
    assert [(s.start, s.text) for s in result.segments] == [(0, '안녕하세요'), (12, '복구된 문장'), (59.9, '마무리')]
    assert result.text_path == tmp_path / 'lecture.transcript.txt'
    assert result.text_path.read_text() == '[00:00:00] 안녕하세요\n[00:00:12] 복구된 문장\n[00:00:59] 마무리\n'
    assert result.srt_path == tmp_path / 'lecture.srt'
    assert '00:00:12,000 --> 00:00:14,000\n복구된 문장' in result.srt_path.read_text()
    payload = json.loads(result.json_path.read_text())
    assert payload['provider'] == 'remote:whisper_cpp_server' and result.md_path.exists()
    # 14-59.9 s is quiet: the long-gap warning is reported but not fatal.
    assert any('00:00:14–00:00:59' in warning for warning in result.warnings)
    stages = [e['stage'] for e in events]
    assert stages[:2] == ['transcript_connecting', 'transcript_started']
    assert 'transcript_repair' in stages and stages[-1] == 'transcript_progress'
    assert all(e.get('remote') is True for e in events)


def test_pipeline_cancel_and_server_errors_map_to_codes(tmp_path, fake_ffmpeg):
    audio = tmp_path / 'lecture.mp3'
    audio.write_bytes(b'mp3')
    with FakeSttServer(lambda request: (500, 'text/plain', 'broken')) as server:
        service = service_for(tmp_path, server.url)
        failed = transcribe_audio(TranscriptionRequest(audio_path=audio, output_dir=tmp_path), remote=service, stt_capability=SUPPORTS_LOCAL_STT)
        cancelled = transcribe_audio(TranscriptionRequest(audio_path=audio, output_dir=tmp_path), should_cancel=lambda: True, remote=service, stt_capability=SUPPORTS_LOCAL_STT)

    assert (failed.ok, failed.error_code) == (False, 'stt_remote_http_error')
    assert service.status()['reachable'] is False and 'HTTP 500' in service.status()['last_error']
    assert (cancelled.ok, cancelled.error_code) == (False, 'cancelled')
    assert not list(tmp_path.glob('lecture.*txt')) and not (tmp_path / 'lecture.srt').exists()


def test_pipeline_empty_transcript_is_an_error(tmp_path, fake_ffmpeg):
    audio = tmp_path / 'lecture.mp3'
    audio.write_bytes(b'mp3')
    with FakeSttServer(lambda request: (200, 'text/plain', srt((0, 1, '음'), (1, 2, '어')))) as server:
        result = transcribe_audio(TranscriptionRequest(audio_path=audio, output_dir=tmp_path), remote=service_for(tmp_path, server.url), stt_capability=SUPPORTS_LOCAL_STT)

    assert result.error_code == 'stt_empty_result'


def test_fake_provider_and_disabled_remote_keep_the_local_path(tmp_path):
    audio = tmp_path / 'lecture.wav'
    audio.write_bytes(b'audio')
    disabled = RemoteSTTService(RemoteSTTSettingsStore(tmp_path / 'stt.json', writer=MacOSPlatform()))
    enabled_url_service = RemoteSTTService(RemoteSTTSettingsStore(tmp_path / 'other.json', writer=MacOSPlatform()))
    enabled_url_service.update({'enabled': True, 'base_url': 'http://127.0.0.1:9'})

    local = transcribe_audio(TranscriptionRequest(audio_path=audio, provider='fake', overwrite=True), remote=disabled, stt_capability=SUPPORTS_LOCAL_STT)
    fake_wins = transcribe_audio(TranscriptionRequest(audio_path=audio, provider='fake', overwrite=True), remote=enabled_url_service, stt_capability=SUPPORTS_LOCAL_STT)

    assert local.ok and fake_wins.ok  # the 'fake' provider never goes to a remote server


def test_check_reports_reachability_and_remembers_it(tmp_path):
    with FakeSttServer(lambda request: (200, 'text/html', 'ok')) as server:
        service = service_for(tmp_path, server.url)
        status = service.check()
    assert status['reachable'] is True and status['last_error'] is None and status['checked_at']
    assert service.provider_status()['available'] is True

    down = service_for(tmp_path, 'http://127.0.0.1:9')
    status = down.check()
    assert status['reachable'] is False and status['error_code'] == 'stt_remote_unreachable'
    assert down.provider_status()['available'] is False and down.provider_status()['installed'] is True

    unconfigured = RemoteSTTService(RemoteSTTSettingsStore(tmp_path / 'none.json', writer=MacOSPlatform()))
    assert unconfigured.check()['error_code'] == 'stt_remote_not_configured'


# ----------------------------------------------- queue-aware response budget (interim remote path)


def test_response_budget_is_one_slot_per_request_ahead_and_capped():
    assert response_budget_seconds(0) == 20 * 60
    assert response_budget_seconds(1) == 40 * 60
    assert response_budget_seconds(3) == 80 * 60
    assert response_budget_seconds(11) == 4 * 60 * 60  # 12 slots would be 4 h exactly
    assert response_budget_seconds(50) == 4 * 60 * 60  # never above the cap
    assert response_budget_seconds(-2) == 20 * 60


class SerialServer:
    """A whisper-server stand-in that, like the real one, answers one request at a time, strictly in arrival order."""

    def __init__(self, work_seconds: float) -> None:
        self.work_seconds = work_seconds
        self._cond = threading.Condition()
        self._queue: deque[int] = deque()
        self.arrived = 0
        self.served = 0

    def __call__(self, request: RecordedRequest):
        with self._cond:
            ticket = self.arrived
            self.arrived += 1
            self._queue.append(ticket)
            self._cond.notify_all()
            self._cond.wait_for(lambda: self._queue[0] == ticket)
        simulate_work(self.work_seconds)
        with self._cond:
            self._queue.popleft()
            self.served += 1
            answer = self.served
            self._cond.notify_all()
        return 200, 'text/plain', srt((0, 2, f'답변 {answer}'))

    def wait_arrived(self, count: int, timeout: float = 5.0) -> None:
        with self._cond:
            assert self._cond.wait_for(lambda: self.arrived >= count, timeout), f'only {self.arrived} of {count} requests arrived'


def staggered_requests(server_url: str, tmp_path: Path, count: int, *, serial: SerialServer, base_timeout: float, use_ahead: bool) -> list[object]:
    """`count` requests sent one after another: request n+1 starts only once the server has recorded request n."""
    service = RemoteSTTService(RemoteSTTSettingsStore(tmp_path / 'stt.json', writer=MacOSPlatform()))
    provider = WhisperCppServerProvider(server_url, timeout=base_timeout, idle_timeout=5)
    outcomes: list[object] = [None] * count

    def one(n: int) -> None:
        audio = tmp_path / f'audio-{n}.wav'
        write_silent_wav(audio, 3)
        with service.request_slot() as ahead:
            try:
                outcomes[n] = provider.transcribe(audio, ahead_requests=ahead if use_ahead else 0)
            except RemoteSTTError as exc:
                outcomes[n] = exc

    threads = []
    for n in range(count):
        thread = threading.Thread(target=one, args=(n,))
        thread.start()
        threads.append(thread)
        serial.wait_arrived(n + 1)
    for thread in threads:
        thread.join(20)
    assert service.in_flight == 0
    return outcomes


def test_requests_queued_behind_each_other_outlive_the_single_request_timeout(tmp_path):
    # Each request needs 0.3 s on the server but the base budget is 0.5 s: the 2nd, 3rd and 4th wait 0.55-1.05 s.
    serial = SerialServer(0.3)
    with FakeSttServer(serial) as server:
        outcomes = staggered_requests(server.url, tmp_path, 4, serial=serial, base_timeout=0.5, use_ahead=True)

    assert [type(outcome).__name__ for outcome in outcomes] == ['list'] * 4
    assert all(outcome and outcome[0].text.startswith('답변') for outcome in outcomes)


def test_without_the_queue_aware_budget_the_same_queue_times_out(tmp_path):
    serial = SerialServer(0.3)
    with FakeSttServer(serial) as server:
        outcomes = staggered_requests(server.url, tmp_path, 4, serial=serial, base_timeout=0.5, use_ahead=False)

    failures = [outcome for outcome in outcomes if isinstance(outcome, RemoteSTTError)]
    assert failures and all(failure.code == 'stt_remote_timeout' for failure in failures)


def test_a_server_that_never_answers_times_out_after_the_budget_not_before(tmp_path):
    started = time.monotonic()
    with FakeSttServer(sleepy(4, lambda request: (200, 'text/plain', srt((0, 1, 'late'))))) as server:
        provider = WhisperCppServerProvider(server.url, timeout=0.4, idle_timeout=5)
        with pytest.raises(RemoteSTTError) as caught:
            provider.transcribe(wav(tmp_path), ahead_requests=2)  # budget 3 x 0.4 s
    elapsed = time.monotonic() - started

    assert caught.value.code == 'stt_remote_timeout' and '초 기다렸어요' in caught.value.message
    assert 1.1 < elapsed < 3.0


def test_the_budget_never_exceeds_the_cap(tmp_path):
    started = time.monotonic()
    with FakeSttServer(sleepy(4, lambda request: (200, 'text/plain', srt((0, 1, 'late'))))) as server:
        provider = WhisperCppServerProvider(server.url, timeout=0.4, idle_timeout=5, budget_cap=0.6)
        with pytest.raises(RemoteSTTError) as caught:
            provider.transcribe(wav(tmp_path), ahead_requests=50)

    assert caught.value.code == 'stt_remote_timeout'
    assert time.monotonic() - started < 2.0


def test_upload_to_an_unresponsive_peer_still_uses_the_short_idle_timeout(tmp_path):
    # Connecting works but nothing ever reads the body: the idle timeout (not the long response budget) ends it.
    import socket

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)  # accepted by the kernel, never read by anyone
    audio = tmp_path / 'big.wav'
    write_silent_wav(audio, 60, rate=48000)  # ~5.7 MB: more than the socket buffers hold
    started = time.monotonic()
    try:
        provider = WhisperCppServerProvider(f'http://127.0.0.1:{listener.getsockname()[1]}', timeout=30, idle_timeout=0.5)
        with pytest.raises(RemoteSTTError) as caught:
            provider.transcribe(audio, ahead_requests=5)
    finally:
        listener.close()

    assert caught.value.code == 'stt_remote_timeout'
    assert time.monotonic() - started < 5


def test_cancel_interrupts_a_long_response_wait_at_once(tmp_path):
    started = time.monotonic()
    with FakeSttServer(sleepy(5, lambda request: (200, 'text/plain', srt((0, 1, 'late'))))) as server:
        provider = WhisperCppServerProvider(server.url)
        calls = {'n': 0}

        def should_cancel() -> bool:
            calls['n'] += 1
            return calls['n'] > 3

        with pytest.raises(TranscriptionCancelled):
            provider.transcribe(wav(tmp_path), ahead_requests=3, should_cancel=should_cancel)
    assert time.monotonic() - started < 3


@pytest.mark.parametrize('status', [429, 503])
def test_a_server_that_rejects_concurrent_requests_gets_the_request_again_after_a_backoff(tmp_path, status):
    answers = iter([status, status, 200])

    def respond(request: RecordedRequest):
        code = next(answers)
        return (code, 'text/plain', 'busy') if code != 200 else (200, 'text/plain', srt((0, 2, '재시도 성공')))

    events: list[dict] = []
    with FakeSttServer(respond) as server:
        provider = WhisperCppServerProvider(server.url, backoff_seconds=(0.05, 0.1))
        cues = provider.transcribe(wav(tmp_path), on_event=events.append)

    assert [c.text for c in cues] == ['재시도 성공']
    assert len(server.requests) == 3
    assert [(e['reason'], e['retry_after_seconds']) for e in events] == [('server_deferred', 0.05), ('server_deferred', 0.1)]


def test_a_server_that_keeps_rejecting_ends_in_the_http_error_once_the_budget_is_spent(tmp_path):
    with FakeSttServer(lambda request: (503, 'text/plain', 'busy')) as server:
        provider = WhisperCppServerProvider(server.url, timeout=0.5, backoff_seconds=(0.2,))
        with pytest.raises(RemoteSTTError) as caught:
            provider.transcribe(wav(tmp_path))

    assert caught.value.code == 'stt_remote_http_error' and 'HTTP 503' in caught.value.message
    assert 1 < len(server.requests) < 6


def test_cancel_during_a_backoff_wait_ends_it(tmp_path):
    started = time.monotonic()
    with FakeSttServer(lambda request: (503, 'text/plain', 'busy')) as server:
        provider = WhisperCppServerProvider(server.url, backoff_seconds=(30,))
        calls = {'n': 0}

        def should_cancel() -> bool:
            calls['n'] += 1
            return calls['n'] > 6

        with pytest.raises(TranscriptionCancelled):
            provider.transcribe(wav(tmp_path), should_cancel=should_cancel)
    assert time.monotonic() - started < 5


def test_openai_compatible_also_backs_off_and_resends(tmp_path):
    answers = iter([429, 200])

    def respond(request: RecordedRequest):
        return (429, 'text/plain', 'slow down') if next(answers) == 429 else (200, 'text/plain', srt((0, 2, '두 번째 시도')))

    with FakeSttServer(respond) as server:
        provider = OpenAICompatibleProvider(server.url, backoff_seconds=(0.05,))
        cues = provider.transcribe(wav(tmp_path))

    assert [c.text for c in cues] == ['두 번째 시도'] and len(server.requests) == 2


def test_pipeline_tells_how_many_requests_are_ahead_and_keeps_the_count_balanced(tmp_path, fake_ffmpeg):
    audio = [tmp_path / f'lecture-{n}.mp3' for n in (1, 2)]
    for path in audio:
        path.write_bytes(b'mp3')
    both_sent = threading.Barrier(2)
    first_at_server = threading.Event()

    def respond(request: RecordedRequest):
        first_at_server.set()
        try:
            both_sent.wait(3)  # answer only once both requests are at the server
        except threading.BrokenBarrierError:
            pass
        return 200, 'text/plain', srt((0, 3, '안녕하세요'))

    events: list[dict] = []
    results = []
    with FakeSttServer(respond) as server:
        service = service_for(tmp_path, server.url)

        def run(path: Path, after_first: bool) -> None:
            if after_first:
                assert first_at_server.wait(5)  # latch: the second request is sent only once the first one is at the server
            results.append(transcribe_audio(TranscriptionRequest(audio_path=path, output_dir=tmp_path, overwrite=True), on_progress=events.append, remote=service, stt_capability=SUPPORTS_LOCAL_STT))

        threads = [threading.Thread(target=run, args=(audio[0], False)), threading.Thread(target=run, args=(audio[1], True))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)

    assert all(result.ok for result in results)
    waiting = [e for e in events if e['stage'] == 'transcript_waiting']
    assert [(e['reason'], e['ahead_requests']) for e in waiting] == [('server_busy', 1)]
    assert service.in_flight == 0


def test_remote_failure_still_releases_the_in_flight_count(tmp_path, fake_ffmpeg):
    audio = tmp_path / 'lecture.mp3'
    audio.write_bytes(b'mp3')
    with FakeSttServer(lambda request: (500, 'text/plain', 'broken')) as server:
        service = service_for(tmp_path, server.url)
        result = transcribe_audio(TranscriptionRequest(audio_path=audio, output_dir=tmp_path), remote=service, stt_capability=SUPPORTS_LOCAL_STT)

    assert result.error_code == 'stt_remote_http_error' and service.in_flight == 0


class RecordingGates:
    """A WorkGates stand-in that records which limits were taken and whether an HTTP call ran while one was held."""

    def __init__(self, server) -> None:
        self.server = server
        self.names: list[str] = []
        self.held = 0
        self.requests_while_held = 0

    @contextmanager
    def hold(self, name, *, should_cancel=None, on_wait=None):
        self.names.append(name)
        self.held += 1
        before = len(self.server.requests)
        try:
            yield
        finally:
            self.requests_while_held += len(self.server.requests) - before
            self.held -= 1


def test_remote_pipeline_takes_the_media_limit_only_around_ffmpeg_work(tmp_path, fake_ffmpeg):
    audio = tmp_path / 'lecture.mp3'
    audio.write_bytes(b'mp3')
    first_pass = srt((0, 4, '안녕하세요'), *[(20 + i, 21 + i, '같은 문장') for i in range(10)], (59.9, 60, '마무리'))
    repair = srt((2, 4, '복구된 문장'))
    with FakeSttServer(lambda request: (200, 'text/plain', repair if request.fields['temperature'] == '0.2' else first_pass)) as server:
        service = service_for(tmp_path, server.url)
        gates = RecordingGates(server)
        result = transcribe_audio(TranscriptionRequest(audio_path=audio, output_dir=tmp_path, overwrite=True), remote=service, stt_capability=SUPPORTS_LOCAL_STT, gates=gates)

    assert result.ok, result.message
    assert gates.names == ['media', 'media']  # WAV conversion + one repair cut
    assert gates.requests_while_held == 0 and gates.held == 0
    assert len(server.requests) == 2
