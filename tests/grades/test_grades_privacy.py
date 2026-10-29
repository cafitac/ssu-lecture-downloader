"""AC4: grade values, credentials and exception text never reach logs, error bodies, files, or access lines."""

from __future__ import annotations

import logging
import threading
import time

import httpx
import uvicorn
from fastapi.testclient import TestClient
from grades_fakes import ALL_SENTINELS, SENTINEL_CLASS, SENTINEL_EXCEPTION, SENTINEL_PASSWORD, SENTINEL_USER, FakeGateway, make_grades_app

from ssu_lecture_downloader.core.grades.models import GradesError


def leaks(text: str) -> list[str]:
    return [marker for marker in ALL_SENTINELS if marker in text]


def sentinel_env(tmp_path, **kwargs):
    return make_grades_app(tmp_path, pair=(SENTINEL_USER, SENTINEL_PASSWORD), **kwargs)


def drive_every_path(env, caplog):
    """Consent, success, cache hit, refresh, every failure kind (one with a leaky exception), logout."""
    caplog.set_level(logging.DEBUG)  # the root logger at DEBUG: nothing may hide below the default level
    client = TestClient(env.app)
    bodies = []

    def call(method, path, **kwargs):
        response = getattr(client, method)(path, **kwargs)
        bodies.append((response.status_code, response.text, dict(response.headers), path))
        return response

    call("get", "/api/grades/status")
    call("put", "/api/grades/consent", json={"consent_version": 1})
    call("get", "/api/grades")
    call("get", "/api/grades")
    call("get", "/api/grades/classes", params={"year": 2025, "semester": "ONE"})
    env.clock.advance(seconds=5)
    for failure in (RuntimeError(SENTINEL_EXCEPTION), GradesError("grades_login_rejected"), TimeoutError(SENTINEL_EXCEPTION)):
        env.gateway.login_error = failure
        env.service.clear()
        call("get", "/api/grades", params={"refresh": "true"})
        call("get", "/api/grades/classes", params={"year": 2025, "semester": "TWO"})
    env.gateway.login_error = None
    env.gateway.overview_errors = [RuntimeError(SENTINEL_EXCEPTION)] * 2
    env.service.clear()
    call("get", "/api/grades")
    call("get", "/api/grades/classes", params={"year": 2025, "semester": "AUTUMN"})
    call("put", "/api/grades/consent", json={"consent_version": 7})
    call("delete", "/api/credentials")
    call("get", "/api/grades")
    call("delete", "/api/grades/consent")
    return bodies


def test_no_marker_reaches_logs_error_bodies_or_files(tmp_path, caplog):
    env = sentinel_env(tmp_path)
    bodies = drive_every_path(env, caplog)

    assert leaks(caplog.text) == [], "log leak"
    for status, text, _headers, _path in bodies:
        if status >= 400:
            assert leaks(text) == [], f"error body leak ({status})"
        # success bodies hold the grade values by design but never the credentials or exception text
        assert SENTINEL_USER not in text and SENTINEL_PASSWORD not in text and SENTINEL_EXCEPTION not in text

    seen_files = [path for path in tmp_path.rglob("*") if path.is_file()]
    for path in seen_files:
        assert leaks(path.read_text(errors="ignore")) == [], f"file leak: {path.name}"
    assert env.store.saved is None  # the credential store is never written by the grades feature


def test_the_only_file_the_feature_writes_is_the_consent_file(tmp_path, caplog):
    env = sentinel_env(tmp_path)
    client = TestClient(env.app)
    client.put("/api/grades/consent", json={"consent_version": 1})
    client.get("/api/grades")
    client.get("/api/grades/classes", params={"year": 2025, "semester": "ONE"})
    files = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*") if path.is_file())
    assert files == ["app-data/grades-consent.json"]
    client.delete("/api/grades/consent")
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


def test_every_grades_response_is_no_store_including_errors_and_unknown_paths(tmp_path, caplog):
    env = sentinel_env(tmp_path)
    checked = 0
    for status, _text, headers, path in drive_every_path(env, caplog):
        if path.startswith("/api/grades"):
            assert headers.get("cache-control") == "no-store", (path, status)
            checked += 1
    assert checked >= 15
    unknown = TestClient(env.app).get("/api/grades/nope")
    assert unknown.status_code == 404 and unknown.headers["cache-control"] == "no-store"


def test_non_grades_routes_keep_their_own_caching_headers(tmp_path):
    env = sentinel_env(tmp_path)
    assert "cache-control" not in TestClient(env.app).get("/api/health").headers


def test_uvicorn_access_lines_carry_no_marker(tmp_path):
    env = sentinel_env(tmp_path, consented=True)
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    handler = Capture()
    loggers = [logging.getLogger(name) for name in ("uvicorn", "uvicorn.access", "uvicorn.error")]
    previous = [logger.level for logger in loggers]
    for logger in loggers:
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    config = uvicorn.Config(env.app, host="127.0.0.1", port=0, log_config=None, access_log=True)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        port = server.servers[0].sockets[0].getsockname()[1]
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as http:
            overview = http.get("/api/grades")
            classes = http.get("/api/grades/classes", params={"year": 2025, "semester": "ONE", "refresh": "false"})
            bad = http.get("/api/grades/classes", params={"year": 2025, "semester": "AUTUMN"})
        assert overview.status_code == 200 and classes.status_code == 200 and bad.status_code == 400
        assert SENTINEL_CLASS in classes.text  # the values travel in the response body only
        assert overview.headers["cache-control"] == classes.headers["cache-control"] == "no-store"
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        for logger, level in zip(loggers, previous, strict=True):
            logger.removeHandler(handler)
            logger.setLevel(level)
    access = [line for line in records if "/api/grades" in line]
    assert len(access) >= 3, records
    assert leaks("\n".join(records)) == []
    assert any("year=2025&semester=ONE" in line for line in access)  # only year/semester/refresh ride in the URL


def test_a_gateway_that_leaks_in_its_exception_still_yields_a_clean_error(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    env = sentinel_env(tmp_path, consented=True)

    class LeakyGateway(FakeGateway):
        async def overview(self, session):
            raise RuntimeError(f"page said {SENTINEL_CLASS} {SENTINEL_EXCEPTION}")

    env.service._gateway = LeakyGateway()
    response = TestClient(env.app).get("/api/grades")
    assert response.status_code == 503
    assert response.json() == {"ok": False, "error_code": "grades_unavailable"}
    assert leaks(response.text + caplog.text) == []
