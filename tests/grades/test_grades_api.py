from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from grades_fakes import FakeGateway, make_grades_app

from ssu_lecture_downloader.core.grades.models import GradesError
from ssu_lecture_downloader.service.api_errors import AUTHENTICATION_ERROR_CODES

BASE = "http://127.0.0.1:8000"
GRADES_ERROR_CODES_EXPECTED = {"grades_consent_required", "grades_login_rejected", "grades_unavailable", "invalid_consent_version", "invalid_semester", "invalid_year"}


def client(env) -> TestClient:
    return TestClient(env.app)


def assert_no_store(response) -> None:
    assert response.headers["cache-control"] == "no-store"


# ---- status and consent (AC1, AC2) --------------------------------------------------------------


def test_status_without_consent(tmp_path):
    env = make_grades_app(tmp_path)
    response = client(env).get("/api/grades/status")
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "consent": {"granted": False, "consent_version": None, "granted_at": None},
        "available": True,
        "current_consent_version": 1,
        "fetched_at": None,
    }
    assert_no_store(response)


def test_status_reports_an_unavailable_native_library(tmp_path):
    env = make_grades_app(tmp_path, gateway=FakeGateway(available=False))
    assert client(env).get("/api/grades/status").json()["available"] is False


def test_put_consent_writes_the_file_and_returns_the_status(tmp_path):
    env = make_grades_app(tmp_path)
    response = client(env).put("/api/grades/consent", json={"consent_version": 1})
    assert response.status_code == 200
    body = response.json()
    assert body["consent"] == {"granted": True, "consent_version": 1, "granted_at": "2030-01-02T03:04:05Z"}
    assert env.consent.path.exists()
    assert_no_store(response)
    assert client(env).get("/api/grades/status").json()["consent"]["granted"] is True


@pytest.mark.parametrize("body", [{"consent_version": 2}, {"consent_version": 0}, {"consent_version": "1"}, {"consent_version": True}, {"consent_version": None}, {}])
def test_put_consent_rejects_other_versions(tmp_path, body):
    env = make_grades_app(tmp_path)
    response = client(env).put("/api/grades/consent", json=body)
    assert response.status_code == 400
    assert response.json() == {"ok": False, "error_code": "invalid_consent_version"}
    assert_no_store(response)
    assert not env.consent.path.exists()


def test_delete_consent_removes_the_file_and_clears_the_data(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    c = client(env)
    assert c.get("/api/grades").status_code == 200
    response = c.delete("/api/grades/consent")
    assert response.status_code == 200
    assert response.json()["consent"]["granted"] is False
    assert not env.consent.path.exists()
    assert_no_store(response)
    assert c.get("/api/grades").status_code == 409


# ---- overview and classes (AC1, AC3) -------------------------------------------------------------


def test_overview_without_consent_is_409_and_never_reaches_u_saint(tmp_path):
    env = make_grades_app(tmp_path)
    response = client(env).get("/api/grades")
    assert response.status_code == 409
    assert response.json() == {"ok": False, "error_code": "grades_consent_required"}
    assert_no_store(response)
    assert env.gateway.logins == []


def test_overview_shape(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    response = client(env).get("/api/grades?refresh=false")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"ok", "fetched_at", "recorded_summary", "certificated_summary", "semesters"}
    assert body["ok"] is True and body["fetched_at"] == "2030-01-02T03:04:05Z"
    assert set(body["recorded_summary"]) == {"attempted_credits", "earned_credits", "pf_earned_credits", "grade_points_sum", "grade_points_average", "arithmetic_mean"}
    first = body["semesters"][0]
    assert first["year"] == 2025 and first["semester"] == "TWO"
    assert first["semester_rank"] == {"rank": 12, "total": 140}
    assert {"academic_probation", "consult", "flunked", "general_rank", "grade_points_average", "earned_credits"} <= set(first)
    assert_no_store(response)


def test_classes_shape_and_semester_validation(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    c = client(env)
    response = c.get("/api/grades/classes", params={"year": 2025, "semester": "ONE"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"ok", "year", "semester", "classes"}
    assert body["year"] == 2025 and body["semester"] == "ONE" and len(body["classes"]) == 9
    assert set(body["classes"][0]) == {"code", "class_name", "credits", "score", "grade", "professor"}
    assert_no_store(response)
    bad = c.get("/api/grades/classes", params={"year": 2025, "semester": "AUTUMN"})
    assert bad.status_code == 400 and bad.json() == {"ok": False, "error_code": "invalid_semester"}
    assert_no_store(bad)
    assert env.gateway.classes_calls == [(2025, "ONE")]


def test_missing_lms_credentials_use_the_existing_error(tmp_path):
    env = make_grades_app(tmp_path, consented=True, pair=None)
    response = client(env).get("/api/grades")
    assert response.status_code == 400
    assert response.json()["error_code"] == "missing_credentials"
    assert_no_store(response)


def test_the_snapshot_is_reused_over_http_and_refresh_forces_a_fetch(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    c = client(env)
    c.get("/api/grades")
    c.get("/api/grades")
    assert env.gateway.overview_calls == 1
    env.clock.advance(seconds=3)
    c.get("/api/grades?refresh=true")
    assert env.gateway.overview_calls == 2


# ---- AC5: failure isolation ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "status", "code"),
    [
        (GradesError("grades_unavailable"), 503, "grades_unavailable"),
        (RuntimeError("anything"), 503, "grades_unavailable"),
        (TimeoutError(), 503, "grades_unavailable"),
    ],
)
def test_overview_failures_map_to_grades_codes(tmp_path, raised, status, code):
    env = make_grades_app(tmp_path, consented=True)
    env.gateway.overview_errors = [raised, raised]
    response = client(env).get("/api/grades")
    assert response.status_code == status
    assert response.json() == {"ok": False, "error_code": code}
    assert_no_store(response)


def test_a_rejected_u_saint_login_is_403_not_an_lms_logout(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    env.gateway.login_error = GradesError("grades_login_rejected")
    response = client(env).get("/api/grades")
    assert response.status_code == 403
    assert response.json() == {"ok": False, "error_code": "grades_login_rejected"}
    assert_no_store(response)


def test_grades_error_codes_are_disjoint_from_the_lms_authentication_codes():
    from ssu_lecture_downloader.core.grades.models import GRADES_ERROR_CODES as core_codes
    from ssu_lecture_downloader.service.api_errors import GRADES_ERROR_CODES

    assert GRADES_ERROR_CODES == GRADES_ERROR_CODES_EXPECTED
    assert GRADES_ERROR_CODES.isdisjoint(AUTHENTICATION_ERROR_CODES)
    assert set(core_codes) <= GRADES_ERROR_CODES


def test_an_unknown_core_code_degrades_to_grades_unavailable_never_a_new_code():
    from ssu_lecture_downloader.service.api_errors import grades_error

    error = grades_error(GradesError("something_else"))
    assert error.status_code == 503 and error.detail == {"ok": False, "error_code": "grades_unavailable"}


def test_a_failing_grades_fetch_leaves_the_lms_routes_working(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    env.gateway.login_error = RuntimeError("down")
    c = client(env)
    assert c.get("/api/grades").status_code == 503
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/credentials/status").json()["username_present"] is True
    assert c.get("/api/terms").status_code == 200
    assert c.get("/api/courses").status_code == 200


def test_lms_routes_stay_fast_while_a_slow_fetch_is_in_flight(tmp_path):
    env = make_grades_app(tmp_path, consented=True, overview_timeout=5.0)
    env.gateway.delay = 0.8  # login + overview each wait this long: a slow u-SAINT (design: 10 s, scaled down)

    async def go():
        transport = httpx.ASGITransport(app=env.app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            slow = asyncio.create_task(http.get("/api/grades"))
            await asyncio.sleep(0.2)  # the grades request is now waiting on the fake u-SAINT
            timings = {}
            for path in ("/api/health", "/api/terms", "/api/courses", "/api/credentials/status"):
                started = time.monotonic()
                response = await http.get(path)
                timings[path] = (response.status_code, time.monotonic() - started)
            assert not slow.done()
            return timings, await slow

    timings, slow_response = asyncio.run(go())
    assert slow_response.status_code == 200
    for path, (status, elapsed) in timings.items():
        assert status == 200, path
        assert elapsed < 1.0, (path, elapsed)


def test_concurrent_http_requests_cause_one_u_saint_login(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    env.gateway.delay = 0.1

    async def go():
        transport = httpx.ASGITransport(app=env.app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            return await asyncio.gather(*[http.get("/api/grades") for _ in range(4)])

    responses = asyncio.run(go())
    assert [r.status_code for r in responses] == [200] * 4
    assert env.gateway.logins == ["alice"] and env.gateway.overview_calls == 1


# ---- AC9: logout / changed credentials clear ---------------------------------------------------------------


def test_logout_clears_the_snapshot_but_keeps_the_consent(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    c = client(env)
    c.get("/api/grades")
    assert c.delete("/api/credentials").status_code == 200
    assert env.consent.path.exists()
    assert c.get("/api/grades").json()["error_code"] == "missing_credentials"
    assert env.service.status().fetched_at is None


def test_saving_a_different_credential_pair_clears_the_snapshot_and_session(tmp_path):
    from ssu_lecture_downloader.service.credentials import CredentialPair

    env = make_grades_app(tmp_path, consented=True)
    c = client(env)
    c.get("/api/grades")
    assert c.post("/api/credentials", json={"username": "alice", "password": "pw-1"}).status_code == 200
    c.get("/api/grades")
    assert env.gateway.logins == ["alice"]  # the same pair keeps its data and session
    assert c.post("/api/credentials", json={"username": "alice", "password": "pw-new"}).status_code == 200
    c.get("/api/grades")
    assert env.gateway.logins == ["alice", "alice"]  # a changed pair starts over
    assert c.post("/api/credentials", json={"username": "bob", "password": "pw-2"}).status_code == 200
    c.get("/api/grades")
    assert env.gateway.logins == ["alice", "alice", "bob"]
    assert isinstance(env.store.pair, CredentialPair)


# ---- AC1: nothing happens without a request ------------------------------------------------------------------


def test_app_start_and_lecture_routes_never_call_the_gateway(tmp_path):
    env = make_grades_app(tmp_path, consented=True)  # even with consent: no request, no call
    c = client(env)
    for path in ("/api/health", "/api/credentials/status", "/api/terms", "/api/courses", "/api/grades/status"):
        assert c.get(path).status_code == 200, path
    assert env.gateway.logins == [] and env.gateway.overview_calls == 0 and env.gateway.classes_calls == []


def test_importing_the_app_and_serving_health_does_not_import_rusaint():
    import subprocess
    import sys

    code = (
        "import sys\n"
        "from fastapi.testclient import TestClient\n"
        "from ssu_lecture_downloader.service.api import app\n"
        "assert TestClient(app, base_url='http://127.0.0.1:8000').get('/api/health').status_code == 200\n"
        "assert 'rusaint' not in sys.modules, 'rusaint was imported at start'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr



# ---- input validation without FastAPI's 422 echo, and a blocking gateway ------------------------------------


@pytest.mark.parametrize("year", ["abc", "20.25", "", "-2025", "２０２５", "1800", "2300", "2025 ", "0x7e9"])
def test_a_bad_year_is_400_invalid_year_in_the_standard_shape(tmp_path, year):
    env = make_grades_app(tmp_path, consented=True)
    response = client(env).get("/api/grades/classes", params={"year": year, "semester": "ONE"})
    assert response.status_code == 400
    assert response.json() == {"ok": False, "error_code": "invalid_year"}
    assert_no_store(response)
    assert env.gateway.logins == []


def test_a_missing_year_or_semester_is_a_400_not_a_422(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    c = client(env)
    assert c.get("/api/grades/classes", params={"semester": "ONE"}).json() == {"ok": False, "error_code": "invalid_year"}
    assert c.get("/api/grades/classes", params={"year": 2025}).json() == {"ok": False, "error_code": "invalid_semester"}


@pytest.mark.parametrize("content", [b"not json", b"", b"[1]", b'"1"'])
def test_a_malformed_consent_body_is_400_invalid_consent_version(tmp_path, content):
    env = make_grades_app(tmp_path)
    response = client(env).put("/api/grades/consent", content=content, headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert response.json() == {"ok": False, "error_code": "invalid_consent_version"}
    assert not env.consent.path.exists()


def test_health_answers_promptly_while_the_gateway_blocks_its_thread(tmp_path):
    env = make_grades_app(tmp_path, consented=True, overview_timeout=5.0)
    env.gateway.blocking = 1.0  # time.sleep inside the gateway coroutine

    async def go():
        transport = httpx.ASGITransport(app=env.app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as http:
            slow = asyncio.create_task(http.get("/api/grades"))
            await asyncio.sleep(0.3)  # the worker thread is now stuck in time.sleep
            timings = []
            for path in ("/api/health", "/api/terms", "/api/credentials/status"):
                started = time.monotonic()
                response = await http.get(path)
                timings.append((path, response.status_code, time.monotonic() - started))
            return timings, await slow

    timings, slow_response = asyncio.run(go())
    assert slow_response.status_code == 200
    for path, status, elapsed in timings:
        assert status == 200 and elapsed < 0.5, (path, elapsed)
