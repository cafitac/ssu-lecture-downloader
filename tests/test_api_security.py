from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ssu_lecture_downloader.service.api import create_app
from ssu_lecture_downloader.service.credentials import CredentialPair
from test_api import FakeCredentialStore, FakeService

ALLOWED_ORIGINS = [
    'tauri://localhost',
    'http://tauri.localhost',
    'https://tauri.localhost',
    'http://127.0.0.1:5173',
    'http://localhost:5173',
]


def build_app(tmp_path: Path, **kwargs):
    store = FakeCredentialStore(CredentialPair(username='u', password='p'))
    return create_app(service_factory=FakeService, default_output_dir=tmp_path / 'downloads', credential_store=store, **kwargs)


@pytest.fixture(autouse=True)
def no_ambient_token(monkeypatch):
    monkeypatch.delenv('SSU_API_TOKEN', raising=False)


# ---- (a) CORS -----------------------------------------------------------------------------


@pytest.mark.parametrize('origin', ALLOWED_ORIGINS)
def test_cors_allows_only_the_known_app_origins(tmp_path, origin):
    client = TestClient(build_app(tmp_path))

    response = client.options('/api/health', headers={'Origin': origin, 'Access-Control-Request-Method': 'GET'})

    assert response.status_code == 200
    assert response.headers['access-control-allow-origin'] == origin
    assert response.headers['access-control-allow-credentials'] == 'true'


@pytest.mark.parametrize('origin', ['http://localhost:3000', 'http://127.0.0.1:8000', 'http://localhost', 'https://evil.example', 'http://localhost:5173.evil.example', 'null'])
def test_cors_rejects_other_origins_including_other_localhost_ports(tmp_path, origin):
    client = TestClient(build_app(tmp_path))

    preflight = client.options('/api/health', headers={'Origin': origin, 'Access-Control-Request-Method': 'GET'})
    simple = client.get('/api/health', headers={'Origin': origin})

    assert 'access-control-allow-origin' not in preflight.headers
    assert 'access-control-allow-origin' not in simple.headers


def test_cors_preflight_allows_the_token_header(tmp_path):
    client = TestClient(build_app(tmp_path, api_token='secret'))

    response = client.options(
        '/api/courses',
        headers={'Origin': 'tauri://localhost', 'Access-Control-Request-Method': 'GET', 'Access-Control-Request-Headers': 'x-ssu-token,content-type'},
    )

    assert response.status_code == 200
    assert 'x-ssu-token' in response.headers['access-control-allow-headers'].lower()


# ---- (b) Host header ----------------------------------------------------------------------


@pytest.mark.parametrize('base_url', ['http://127.0.0.1:8765', 'http://127.0.0.1', 'http://localhost:8000', 'http://localhost'])
def test_loopback_hosts_are_accepted(tmp_path, base_url):
    client = TestClient(build_app(tmp_path), base_url=base_url)

    assert client.get('/api/health').status_code == 200


@pytest.mark.parametrize('base_url', ['http://evil.example', 'http://evil.example:8000', 'http://127.0.0.1.evil.example', 'http://localhost.evil.example:8000', 'http://192.168.0.5:8000', 'http://0.0.0.0:8000'])
def test_non_loopback_host_headers_are_rejected_as_dns_rebinding(tmp_path, base_url):
    client = TestClient(build_app(tmp_path), base_url=base_url)

    response = client.get('/api/health')

    assert response.status_code == 400
    assert response.json()['error_code'] == 'invalid_host'
    assert response.json()['ok'] is False


def test_missing_host_header_is_rejected(tmp_path):
    client = TestClient(build_app(tmp_path))

    response = client.get('/api/health', headers={'Host': ''})

    assert response.status_code == 400


# ---- (c) API token ------------------------------------------------------------------------


def test_no_token_is_enforced_when_the_env_var_is_unset(tmp_path):
    client = TestClient(build_app(tmp_path))

    assert client.get('/api/credentials/status').status_code == 200


def test_token_required_on_every_api_route_except_get_health(tmp_path, monkeypatch):
    monkeypatch.setenv('SSU_API_TOKEN', 'launcher-secret')
    client = TestClient(build_app(tmp_path))

    assert client.get('/api/health').status_code == 200
    for method, path in [
        ('GET', '/api/credentials/status'),
        ('GET', '/api/terms'),
        ('GET', '/api/courses'),
        ('GET', '/api/deadlines'),
        ('POST', '/api/watch/session-bridge-plan'),
        ('POST', '/api/media/video/ensure-playable'),
        ('GET', '/api/download/jobs/anything'),
        ('DELETE', '/api/credentials'),
    ]:
        response = client.request(method, path)
        assert response.status_code == 401, (method, path)
        assert response.json() == {'ok': False, 'error_code': 'api_token_invalid', 'message': 'Missing or invalid API token.'}, (method, path)


def test_valid_token_header_is_accepted_and_wrong_ones_are_not(tmp_path, monkeypatch):
    monkeypatch.setenv('SSU_API_TOKEN', 'launcher-secret')
    client = TestClient(build_app(tmp_path))

    ok = client.get('/api/credentials/status', headers={'X-SSU-Token': 'launcher-secret'})
    wrong = client.get('/api/credentials/status', headers={'X-SSU-Token': 'launcher-secre'})
    empty = client.get('/api/credentials/status', headers={'X-SSU-Token': ''})
    bearer = client.get('/api/credentials/status', headers={'Authorization': 'Bearer launcher-secret'})

    assert ok.status_code == 200
    assert wrong.status_code == 401 and empty.status_code == 401 and bearer.status_code == 401


def test_token_does_not_break_cors_preflight_and_health_is_post_protected(tmp_path):
    client = TestClient(build_app(tmp_path, api_token='secret'))

    preflight = client.options('/api/credentials', headers={'Origin': 'tauri://localhost', 'Access-Control-Request-Method': 'POST'})
    health_post = client.post('/api/health')

    assert preflight.status_code == 200
    assert health_post.status_code == 401


def test_token_error_response_carries_cors_headers_so_the_frontend_can_read_it(tmp_path):
    client = TestClient(build_app(tmp_path, api_token='secret'))

    response = client.get('/api/credentials/status', headers={'Origin': 'http://localhost:5173'})

    assert response.status_code == 401
    assert response.headers['access-control-allow-origin'] == 'http://localhost:5173'


def test_token_is_not_required_outside_the_api_namespace(tmp_path):
    client = TestClient(build_app(tmp_path, api_token='secret'))

    assert client.get('/openapi.json').status_code == 200


# ---- (d) ensure-playable path policy --------------------------------------------------------


def write_plain_mp4(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'not a zip, already playable')
    return path


def ensure(client, path):
    return client.post('/api/media/video/ensure-playable', json={'input_path': str(path)})


def test_ensure_playable_accepts_mp4_files_under_the_default_download_root(tmp_path):
    client = TestClient(build_app(tmp_path))
    video = write_plain_mp4(tmp_path / 'downloads' / 'course' / 'lecture.mp4')

    response = ensure(client, video)

    assert response.status_code == 200
    assert response.json()['source_kind'] == 'mp4'


@pytest.mark.parametrize('name', ['lecture.txt', 'lecture.mov', 'lecture', 'lecture.mp4.zip'])
def test_ensure_playable_rejects_non_mp4_paths(tmp_path, name):
    client = TestClient(build_app(tmp_path))
    path = tmp_path / 'downloads' / name
    path.parent.mkdir(parents=True)
    path.write_bytes(b'x')

    response = ensure(client, path)

    assert response.status_code == 400
    assert response.json()['error_code'] == 'media_path_not_allowed'


def test_ensure_playable_rejects_mp4_outside_every_download_root(tmp_path):
    client = TestClient(build_app(tmp_path))
    outside = write_plain_mp4(tmp_path / 'elsewhere' / 'secret.mp4')

    response = ensure(client, outside)

    assert response.status_code == 400
    assert response.json()['error_code'] == 'media_path_not_allowed'


def test_ensure_playable_does_not_reveal_whether_a_rejected_path_exists(tmp_path):
    client = TestClient(build_app(tmp_path))

    missing = ensure(client, tmp_path / 'elsewhere' / 'missing.mp4')
    present = ensure(client, write_plain_mp4(tmp_path / 'elsewhere' / 'present.mp4'))

    assert missing.status_code == present.status_code == 400
    assert missing.json() == present.json()


def test_ensure_playable_rejects_dot_dot_traversal_out_of_the_root(tmp_path):
    client = TestClient(build_app(tmp_path))
    write_plain_mp4(tmp_path / 'secret.mp4')

    response = client.post('/api/media/video/ensure-playable', json={'input_path': str(tmp_path / 'downloads' / '..' / 'secret.mp4')})

    assert response.status_code == 400


def test_ensure_playable_rejects_symlinks_that_escape_the_root(tmp_path):
    client = TestClient(build_app(tmp_path))
    outside = write_plain_mp4(tmp_path / 'elsewhere' / 'secret.mp4')
    link = tmp_path / 'downloads' / 'course' / 'link.mp4'
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)

    response = ensure(client, link)

    assert response.status_code == 400


def test_ensure_playable_accepts_the_folder_the_user_chose_in_a_request(tmp_path):
    client = TestClient(build_app(tmp_path))
    chosen = tmp_path / 'my-picked-folder'
    video = write_plain_mp4(chosen / 'course' / 'lecture.mp4')
    assert ensure(client, video).status_code == 400

    plan = client.post('/api/download/plan', json={'course_id': 1, 'module_item_id': 2, 'output_dir': str(chosen)})
    assert plan.status_code == 200

    assert ensure(client, video).status_code == 200


def test_ensure_playable_never_registers_the_filesystem_root_or_home_as_a_download_root(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    (tmp_path / 'home').mkdir()
    client = TestClient(build_app(tmp_path))
    video = write_plain_mp4(tmp_path / 'home' / 'private.mp4')

    client.post('/api/download/plan', json={'course_id': 1, 'module_item_id': 2, 'output_dir': '/'})
    client.post('/api/download/plan', json={'course_id': 1, 'module_item_id': 2, 'output_dir': str(tmp_path / 'home')})

    assert ensure(client, video).status_code == 400


def test_ensure_playable_accepts_files_the_app_itself_produced(tmp_path):
    produced_dir = tmp_path / 'unregistered'
    produced = write_plain_mp4(produced_dir / 'course' / 'lecture.mp4')

    class ProducingService(FakeService):
        def download_lecture(self, *args, **kwargs):
            result = super().download_lecture(*args, **kwargs)
            return type(result)(**{**result.__dict__, 'output_path': str(produced)})

    store = FakeCredentialStore(CredentialPair(username='u', password='p'))
    client = TestClient(create_app(service_factory=ProducingService, default_output_dir=tmp_path / 'downloads', credential_store=store))
    assert ensure(client, produced).status_code == 400

    assert client.post('/api/download', json={'course_id': 1, 'module_item_id': 2}).status_code == 200

    assert ensure(client, produced).status_code == 200
