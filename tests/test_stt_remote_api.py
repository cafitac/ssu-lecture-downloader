from __future__ import annotations

import json
import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from stt_fakes import FakeSttServer, srt, write_silent_wav
from test_api import FakeCredentialStore, FakeService

from ssu_lecture_downloader import postprocess_media
from ssu_lecture_downloader.api import create_app
from ssu_lecture_downloader.credentials import CredentialPair
from ssu_lecture_downloader.ssh_tunnel import TunnelError
from ssu_lecture_downloader.stt_remote import RemoteSTTService
from ssu_lecture_downloader.stt_remote_config import RemoteSTTConfig, RemoteSTTSettingsStore, apply_patch

SECRET = 'sk-test-secret-123'
MAC_STUDIO = {
    'enabled': True,
    'kind': 'whisper_cpp_server',
    'base_url': 'http://127.0.0.1:18795',
    'tunnel': {'host': 'macstudio', 'remote_port': 8795, 'local_port': 18795},
}


class FakeTunnel:
    """Stands in for SshTunnel: 'opens' the forward by pointing at a given local port."""

    def __init__(self, port: int | None = None, error: TunnelError | None = None) -> None:
        self.port = port
        self.error = error
        self.ensured: list[tuple] = []
        self.closed = 0

    def ensure(self, host, remote_port, local_port):
        self.ensured.append((host, remote_port, local_port))
        if self.error:
            raise self.error
        return self.port or local_port

    def active_port(self):
        return self.port if self.ensured and not self.error else None

    def close(self):
        self.closed += 1


def make_client(tmp_path: Path, tunnel: FakeTunnel | None = None) -> tuple[TestClient, RemoteSTTService]:
    service = RemoteSTTService(RemoteSTTSettingsStore(tmp_path / 'settings' / 'stt.json'), tunnel or FakeTunnel())
    app = create_app(service_factory=FakeService, default_output_dir=tmp_path, credential_store=FakeCredentialStore(CredentialPair(username='u', password='p')), remote_stt=service)
    return TestClient(app), service


# -------------------------------------------------------------- settings


def test_default_status_is_disabled_and_reports_no_secret(tmp_path):
    client, _ = make_client(tmp_path)

    payload = client.get('/api/stt/remote/status').json()

    assert payload['ok'] is True
    assert (payload['enabled'], payload['kind'], payload['api_key_set'], payload['tunnel']) == (False, 'whisper_cpp_server', False, None)
    assert payload['reachable'] is None and payload['last_error'] is None
    assert 'api_key' not in payload


def test_config_round_trip_persists_to_disk_and_never_returns_the_api_key(tmp_path):
    client, service = make_client(tmp_path)

    response = client.put('/api/stt/remote/config', json={'enabled': True, 'kind': 'openai_compatible', 'base_url': 'https://stt.example.com/', 'model': 'whisper-1', 'api_key': SECRET})
    status = client.get('/api/stt/remote/status')

    assert response.status_code == 200
    for body in (response.text, status.text):
        assert SECRET not in body
    assert response.json()['api_key_set'] is True
    assert response.json()['base_url'] == 'https://stt.example.com'
    # Persisted: a fresh service on the same file sees it; the file is private.
    reloaded = RemoteSTTService(RemoteSTTSettingsStore(tmp_path / 'settings' / 'stt.json'))
    assert reloaded.config().api_key == SECRET and reloaded.is_enabled()
    mode = stat.S_IMODE(os.stat(tmp_path / 'settings' / 'stt.json').st_mode)
    assert mode == 0o600
    assert SECRET not in json.dumps(service.status())


def test_omitted_api_key_is_kept_and_empty_string_clears_it(tmp_path):
    client, service = make_client(tmp_path)
    client.put('/api/stt/remote/config', json={'kind': 'openai_compatible', 'base_url': 'https://stt.example.com', 'api_key': SECRET})

    client.put('/api/stt/remote/config', json={'model': 'other-model'})
    assert service.config().api_key == SECRET and service.config().model == 'other-model'

    cleared = client.put('/api/stt/remote/config', json={'api_key': ''}).json()
    assert cleared['api_key_set'] is False and service.config().api_key == ''


def test_mac_studio_preset_is_stored_with_its_tunnel(tmp_path):
    client, service = make_client(tmp_path)

    status = client.put('/api/stt/remote/config', json=MAC_STUDIO).json()

    assert status['tunnel'] == {'host': 'macstudio', 'remote_port': 8795, 'local_port': 18795}
    assert status['effective_base_url'] == 'http://127.0.0.1:18795'
    assert service.is_enabled()
    removed = client.put('/api/stt/remote/config', json={'tunnel': None}).json()
    assert removed['tunnel'] is None


@pytest.mark.parametrize(
    'body,code',
    [
        ({'kind': 'ftp'}, 'invalid_stt_kind'),
        ({'base_url': 'not a url'}, 'invalid_stt_base_url'),
        ({'base_url': 'file:///etc/passwd'}, 'invalid_stt_base_url'),
        ({'base_url': 'http://user:pw@host:8000'}, 'invalid_stt_base_url'),
        ({'tunnel': {'host': '-oProxyCommand=evil', 'remote_port': 8795}}, 'invalid_ssh_host'),
        ({'tunnel': {'host': 'host name', 'remote_port': 8795}}, 'invalid_ssh_host'),
        ({'tunnel': {'host': 'mac', 'remote_port': 70000}}, 'invalid_port'),
        ({'tunnel': {'host': 'mac', 'remote_port': 8795, 'local_port': 0}}, 'invalid_port'),
        ({'enabled': True}, 'missing_stt_base_url'),
    ],
)
def test_invalid_config_is_rejected_and_not_saved(tmp_path, body, code):
    client, service = make_client(tmp_path)

    response = client.put('/api/stt/remote/config', json=body)

    assert response.status_code == 400 and response.json()['error_code'] == code
    assert service.config() == RemoteSTTConfig()


def test_changing_the_tunnel_closes_the_old_one_and_clears_the_last_check(tmp_path):
    tunnel = FakeTunnel(port=18795)
    client, service = make_client(tmp_path, tunnel)
    client.put('/api/stt/remote/config', json=MAC_STUDIO)
    service._last_check = {'reachable': True, 'last_error': None, 'error_code': None, 'checked_at': 1}
    before = tunnel.closed

    client.put('/api/stt/remote/config', json={'enabled': False})
    assert tunnel.closed == before and service.status()['reachable'] is True  # toggling does not drop the tunnel

    client.put('/api/stt/remote/config', json={'tunnel': {'host': 'other-host', 'remote_port': 8795}})
    assert tunnel.closed == before + 1 and service.status()['reachable'] is None


def test_apply_patch_ignores_unknown_none_values():
    base = apply_patch(RemoteSTTConfig(), {'kind': 'openai_compatible', 'base_url': 'http://h:1'})

    assert apply_patch(base, {'kind': None, 'enabled': None, 'base_url': None}) == base


# ----------------------------------------------------------------- check


def test_check_opens_the_tunnel_and_probes_the_server(tmp_path):
    with FakeSttServer(lambda request: (200, 'text/html', 'ok')) as server:
        tunnel = FakeTunnel(port=server.port)
        client, _ = make_client(tmp_path, tunnel)
        client.put('/api/stt/remote/config', json=MAC_STUDIO)

        payload = client.post('/api/stt/remote/check').json()

    assert tunnel.ensured == [('macstudio', 8795, 18795)]
    assert payload['ok'] is True and payload['reachable'] is True and payload['last_error'] is None
    assert server.requests[0].path == '/'
    assert client.get('/api/stt/remote/status').json()['reachable'] is True


def test_check_reports_tunnel_and_server_failures_as_data(tmp_path):
    tunnel = FakeTunnel(error=TunnelError('ssh_tunnel_failed', 'SSH 터널을 열지 못했어요. Permission denied'))
    client, _ = make_client(tmp_path, tunnel)
    client.put('/api/stt/remote/config', json=MAC_STUDIO)

    payload = client.post('/api/stt/remote/check')

    assert payload.status_code == 200
    assert payload.json()['ok'] is True and payload.json()['reachable'] is False
    assert payload.json()['error_code'] == 'ssh_tunnel_failed' and 'Permission denied' in payload.json()['last_error']

    client2, _ = make_client(tmp_path / 'b')
    client2.put('/api/stt/remote/config', json={'enabled': True, 'base_url': 'http://127.0.0.1:9'})
    assert client2.post('/api/stt/remote/check').json()['error_code'] == 'stt_remote_unreachable'


def test_check_without_configuration(tmp_path):
    client, _ = make_client(tmp_path)

    payload = client.post('/api/stt/remote/check').json()

    assert payload['reachable'] is False and payload['error_code'] == 'stt_remote_not_configured'


def test_openai_check_never_leaks_the_key_in_the_response(tmp_path):
    with FakeSttServer(lambda request: (200, 'application/json', '{"data": []}')) as server:
        client, _ = make_client(tmp_path)
        client.put('/api/stt/remote/config', json={'enabled': True, 'kind': 'openai_compatible', 'base_url': server.url, 'api_key': SECRET})
        response = client.post('/api/stt/remote/check')

    assert response.json()['reachable'] is True and SECRET not in response.text
    assert server.requests[0].headers['authorization'] == f'Bearer {SECRET}'


# ------------------------------------------------------ provider listings


def test_provider_status_lists_the_remote_engine_as_available_only_when_reachable(tmp_path):
    with FakeSttServer(lambda request: (200, 'text/html', 'ok')) as server:
        client, _ = make_client(tmp_path)

        def remote_entry(path: str):
            payload = client.get(path).json()
            return next(item for item in payload['providers'] if item['provider'] == 'remote')

        assert remote_entry('/api/stt/providers/status')['available'] is False
        client.put('/api/stt/remote/config', json={'enabled': True, 'base_url': server.url})
        assert remote_entry('/api/stt/providers/status')['installed'] is True
        assert remote_entry('/api/stt/providers/status')['available'] is False  # not checked yet
        client.post('/api/stt/remote/check')
        assert remote_entry('/api/media/dependencies/status')['available'] is True


def test_cors_preflight_allows_put_for_the_desktop_origin(tmp_path):
    client, _ = make_client(tmp_path)

    response = client.options('/api/stt/remote/config', headers={'Origin': 'tauri://localhost', 'Access-Control-Request-Method': 'PUT', 'Access-Control-Request-Headers': 'content-type'})

    assert response.status_code == 200 and 'PUT' in response.headers['access-control-allow-methods']


# ------------------------------------------------------ artifact pipeline


def test_artifact_txt_job_uses_the_remote_server_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(postprocess_media, 'convert_to_stt_wav', lambda src, dst, should_cancel=None: write_silent_wav(dst, 30))
    course_dir = tmp_path / 'UX방법론'
    course_dir.mkdir()
    mp3 = course_dir / '26년1학기_UX방법론_1주차 [699c7d422c427].mp3'
    mp3.write_bytes(b'existing-audio')

    with FakeSttServer(lambda request: (200, 'text/plain', srt((0, 3, '원격 자막'), (4, 6, '두 번째')))) as server:
        client, _ = make_client(tmp_path)
        client.put('/api/stt/remote/config', json={'enabled': True, 'base_url': server.url})
        started = client.post('/api/download/artifacts/jobs', json={'course_id': 44070, 'course_name': 'UX방법론', 'module_item_ids': [3268578], 'outputs': ['txt'], 'audio_format': 'mp3'}).json()
        status = {}
        for _ in range(200):
            status = client.get(f"/api/download/artifacts/jobs/{started['job_id']}").json()
            if status['status'] != 'running':
                break
            time.sleep(0.02)

    assert status['status'] == 'succeeded', status
    txt = next(artifact for artifact in status['result']['artifacts'] if artifact['kind'] == 'txt')
    assert txt['text_path'].endswith('.transcript.txt') and txt['srt_path'].endswith('.srt')
    assert Path(txt['text_path']).read_text().startswith('[00:00:00] 원격 자막')
    assert (course_dir / '26년1학기_UX방법론_1주차 [699c7d422c427].srt').exists()
    stages = [event['stage'] for event in status['progress']]
    assert 'transcribing' in stages and 'transcript_connecting' in stages
    transcribing = next(event for event in status['progress'] if event['stage'] == 'transcribing')
    assert (transcribing['provider'], transcribing['remote']) == ('remote', True)
    assert len(server.requests) == 1


def test_artifact_txt_job_reports_a_clear_error_when_the_remote_server_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(postprocess_media, 'convert_to_stt_wav', lambda src, dst, should_cancel=None: write_silent_wav(dst, 30))
    course_dir = tmp_path / 'UX방법론'
    course_dir.mkdir()
    (course_dir / '26년1학기_UX방법론_1주차 [699c7d422c427].mp3').write_bytes(b'existing-audio')
    client, _ = make_client(tmp_path)
    client.put('/api/stt/remote/config', json={'enabled': True, 'base_url': 'http://127.0.0.1:9'})

    started = client.post('/api/download/artifacts/jobs', json={'course_id': 44070, 'course_name': 'UX방법론', 'module_item_ids': [3268578], 'outputs': ['txt'], 'audio_format': 'mp3'}).json()
    status = {}
    for _ in range(200):
        status = client.get(f"/api/download/artifacts/jobs/{started['job_id']}").json()
        if status['status'] != 'running':
            break
        time.sleep(0.02)

    assert status['status'] == 'failed' and status['error_code'] == 'stt_remote_unreachable'
