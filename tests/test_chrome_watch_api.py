import time
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ssu_lecture_downloader.core import chrome_watch as cw
from ssu_lecture_downloader.service.api import create_app
from ssu_lecture_downloader.service.app_context import AppContext
from ssu_lecture_downloader.core.credentials import CredentialPair
from ssu_lecture_downloader.core.jobs import JobRunner
from ssu_lecture_downloader.service.routers.watch import chrome_plan_provider
from ssu_lecture_downloader.service.security import MediaPathPolicy
from test_api import ExpiredSessionFakeService, FakeCredentialStore, FakeService

from chrome_fakes import FakeBrowser, FakePlayer, FakePopen, make_chrome_binary

ROUTES = [
    ("POST", "/api/watch/chrome/start"),
    ("GET", "/api/watch/chrome/status"),
    ("POST", "/api/watch/chrome/next"),
    ("POST", "/api/watch/chrome/stop"),
    ("POST", "/api/watch/chrome/show"),
]


class StubManager:
    """Records what the routes ask of the manager."""

    def __init__(self):
        self.calls = []
        self.shutdowns = 0

    def start(self, items):
        self.calls.append(("start", items))
        if items and items[0]["module_item_id"] == "busy":
            raise cw.ChromeWatchBusy()
        return {"ok": True, "state": "starting", "total": len(items)}

    def status(self):
        return {"ok": True, "state": "idle"}

    def next(self):
        self.calls.append(("next",))
        return {"ok": True, "state": "playing"}

    def stop(self):
        self.calls.append(("stop",))
        return {"ok": True, "state": "stopped"}

    def show(self):
        raise cw.ChromeWatchError("chrome_window_missing", "Chrome 창이 열려 있지 않아요.")

    def shutdown(self):
        self.shutdowns += 1


def app_with(tmp_path: Path, manager=None, **kwargs):
    store = FakeCredentialStore(CredentialPair(username="u", password="p"))
    return create_app(service_factory=FakeService, default_output_dir=tmp_path / "downloads", credential_store=store, chrome_watch=manager, **kwargs)


@pytest.fixture(autouse=True)
def no_ambient_token(monkeypatch):
    monkeypatch.delenv("SSU_API_TOKEN", raising=False)


def test_every_chrome_route_needs_the_api_token(tmp_path, monkeypatch):
    monkeypatch.setenv("SSU_API_TOKEN", "launcher-secret")
    client = TestClient(app_with(tmp_path, StubManager()))
    for method, path in ROUTES:
        response = client.request(method, path)
        assert response.status_code == 401, (method, path)
        assert response.json()["error_code"] == "api_token_invalid"
        ok = client.request(method, path, headers={"X-SSU-Token": "launcher-secret"}, json={"items": []} if method == "POST" and path.endswith("start") else None)
        assert ok.status_code != 401, (method, path)


def test_routes_pass_the_queue_and_return_the_manager_status(tmp_path):
    manager = StubManager()
    client = TestClient(app_with(tmp_path, manager))

    started = client.post(
        "/api/watch/chrome/start",
        json={"items": [{"course_id": 44070, "course_name": "UX", "module_item_id": 3268578, "title": "1주차", "duration": 600}, {"course_id": "9", "module_item_id": "2"}]},
    )
    assert started.status_code == 200 and started.json()["total"] == 2
    assert manager.calls[0][1][0] == {"course_id": 44070, "course_name": "UX", "module_item_id": 3268578, "title": "1주차", "duration": 600.0}
    assert manager.calls[0][1][1]["title"] == "" and manager.calls[0][1][1]["duration"] is None
    assert client.get("/api/watch/chrome/status").json()["state"] == "idle"
    assert client.post("/api/watch/chrome/next").json()["state"] == "playing"
    assert client.post("/api/watch/chrome/stop").json()["state"] == "stopped"
    shown = client.post("/api/watch/chrome/show")
    assert shown.status_code == 400 and shown.json()["error_code"] == "chrome_window_missing"


def test_start_errors_use_the_json_error_shape(tmp_path):
    client = TestClient(app_with(tmp_path, StubManager()))
    busy = client.post("/api/watch/chrome/start", json={"items": [{"course_id": "1", "module_item_id": "busy"}]})
    assert busy.status_code == 409 and busy.json() == {"ok": False, "error_code": "chrome_watch_busy", "message": "이미 자동 재생이 진행 중이에요."}
    assert client.post("/api/watch/chrome/start", json={}).status_code == 422


def test_start_without_login_is_a_missing_credentials_error(tmp_path):
    app = create_app(service_factory=FakeService, default_output_dir=tmp_path / "d", credential_store=FakeCredentialStore(None), chrome_watch=StubManager())
    response = TestClient(app).post("/api/watch/chrome/start", json={"items": [{"course_id": "1", "module_item_id": "1"}]})
    assert response.status_code == 400 and response.json()["error_code"] == "missing_credentials"


def test_start_without_chrome_installed_reports_chrome_not_found(tmp_path):
    # The default manager (conftest hides every Chrome candidate).
    client = TestClient(app_with(tmp_path))
    response = client.post("/api/watch/chrome/start", json={"items": [{"course_id": "1", "module_item_id": "1", "title": "x"}]})
    assert response.status_code == 400 and response.json()["error_code"] == "chrome_not_found"
    status = client.get("/api/watch/chrome/status").json()
    assert status["state"] == "idle" and status["chrome_available"] is False


def test_the_backend_shutdown_closes_the_chrome_window(tmp_path):
    manager = StubManager()
    with TestClient(app_with(tmp_path, manager)):
        assert manager.shutdowns == 0
    assert manager.shutdowns == 1


def test_a_queue_runs_end_to_end_through_the_api_with_the_service_plan(tmp_path, monkeypatch):
    chrome = make_chrome_binary(tmp_path)
    browser = FakeBrowser([FakePlayer(duration=42.0)])  # the fake service plan says 42 s
    monkeypatch.setattr(cw, "chrome_candidates", lambda: [chrome])
    monkeypatch.setattr(cw.subprocess, "Popen", FakePopen)
    # Launch Chrome directly (no `open -g` wrapper) so the fake process stands for Chrome itself.
    monkeypatch.setattr(cw, "build_background_launch", lambda chrome, args: args)
    monkeypatch.setattr(cw, "default_list_processes", lambda: [])
    monkeypatch.setattr(cw, "WebSocketTransport", lambda url: browser)
    fast = cw.Timings(tick=0.002, poll_interval=0.01, stall_after=30.0, terminate_grace=0.5, stop_join=5.0)
    monkeypatch.setattr(cw, "Timings", lambda: fast)
    monkeypatch.setattr(cw, "default_killpg", lambda pgid, sig: None)
    monkeypatch.setattr(cw, "default_process_command", lambda pid: None)
    FakePopen.instances = []
    FakePopen.silent = False

    with TestClient(app_with(tmp_path)) as client:
        started = client.post("/api/watch/chrome/start", json={"items": [{"course_id": 44070, "course_name": "UX방법론", "module_item_id": 3268578, "title": "1주차", "duration": 5}]})
        assert started.status_code == 200
        status = started.json()
        deadline = time.time() + 15
        while time.time() < deadline and status["state"] not in {"finished", "failed", "stopped"}:
            time.sleep(0.02)
            status = client.get("/api/watch/chrome/status").json()
    assert status["state"] == "finished", status
    # The plan came from the (fake) LMS service, with its cookie, and the lecture URL was opened.
    assert browser.navigations == ["https://canvas.ssu.ac.kr/courses/44070/modules/items/3268578"]
    assert browser.cookies[0]["name"] == "xn_api_token" and browser.cookies[0]["value"] == "raw-token"
    assert "raw-token" not in str(status)  # cookies never appear in the status payload


def make_context(service, pair):
    return AppContext(
        service=service,
        credentials=FakeCredentialStore(pair),
        default_output_dir=Path("/tmp/none"),
        media_paths=MediaPathPolicy(Path("/tmp/none")),
        runner=JobRunner(error_code_for=lambda exc: "x"),
    )


def test_plan_provider_returns_the_bridge_plan_with_cookies():
    provide = chrome_plan_provider(make_context(FakeService(), CredentialPair(username="u", password="p")))
    plan = provide("44070", "3268578", "UX")
    assert plan["launch_url"] == "https://canvas.ssu.ac.kr/courses/44070/modules/items/3268578"
    assert plan["cookies"][0]["name"] == "xn_api_token"


def test_plan_provider_maps_failures_to_plan_errors():
    class NotOk(FakeService):
        def official_viewer_session_bridge_plan(self, *a, **k):
            return replace(super().official_viewer_session_bridge_plan(*a, **k), ok=False, error_code="network_error")

    class Boom(FakeService):
        def official_viewer_session_bridge_plan(self, *a, **k):
            raise RuntimeError("secret detail")

    pair = CredentialPair(username="u", password="p")
    with pytest.raises(cw.ChromeWatchPlanError) as network:
        chrome_plan_provider(make_context(NotOk(), pair))("1", "2", "c")
    assert network.value.code == "network_error" and network.value.auth is False
    with pytest.raises(cw.ChromeWatchPlanError) as boom:
        chrome_plan_provider(make_context(Boom(), pair))("1", "2", "c")
    assert boom.value.code == "internal_error" and "secret" not in str(boom.value)
    with pytest.raises(cw.ChromeWatchPlanError) as nobody:
        chrome_plan_provider(make_context(FakeService(), None))("1", "2", "c")
    assert nobody.value.code == "missing_credentials" and nobody.value.auth is True


def test_plan_provider_treats_a_lost_lms_login_as_an_auth_error():
    class Expired(ExpiredSessionFakeService):
        def official_viewer_session_bridge_plan(self, *a, **k):
            return replace(FakeService.official_viewer_session_bridge_plan(self, *a, **k), ok=False, error_code="session_expired")

    with pytest.raises(cw.ChromeWatchPlanError) as expired:
        chrome_plan_provider(make_context(Expired(), CredentialPair(username="u", password="p")))("1", "2", "c")
    assert expired.value.code == "session_expired" and expired.value.auth is True
