"""RusaintGateway against a fake `rusaint` module: call shape, error mapping, no echo."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from grades_fakes import SENTINEL_EXCEPTION, SENTINEL_PASSWORD, SENTINEL_USER
from test_grades_mapper import class_obj, pair, sem, semester_obj, summary_obj

from ssu_lecture_downloader.core.grades.gateway import RusaintGateway
from ssu_lecture_downloader.core.grades.models import GradesError


class RusaintError(Exception):
    pass


class FakeApp:
    def __init__(self, log):
        self.log = log
        self.fail: Exception | None = None

    async def semesters(self, course_type):
        self.log.append(("semesters", course_type))
        if self.fail:
            raise self.fail
        return [semester_obj(2025, "ONE")]

    async def recorded_summary(self, course_type):
        self.log.append(("recorded_summary", course_type))
        return summary_obj()

    async def certificated_summary(self, course_type):
        self.log.append(("certificated_summary", course_type))
        return summary_obj()

    async def classes(self, course_type, year, semester, include_details):
        self.log.append(("classes", course_type, year, semester, include_details))
        return [class_obj(1)]


def fake_rusaint(log, *, login_error=None, build_count=None):
    app = FakeApp(log)

    class SessionBuilder:
        async def with_password(self, user, password):
            log.append(("with_password", user, password))
            if login_error:
                raise login_error
            return "session-handle"

    class AppBuilder:
        async def build(self, session):
            log.append(("build", session))
            if build_count is not None:
                build_count.append(1)
            return app

    module = SimpleNamespace(
        USaintSessionBuilder=SessionBuilder,
        CourseGradesApplicationBuilder=AppBuilder,
        CourseType=SimpleNamespace(BACHELOR="BACHELOR"),
        SemesterType={"ONE": "S-ONE", "TWO": "S-TWO", "SUMMER": "S-SUMMER", "WINTER": "S-WINTER"},
    )
    return module, app


def run(coro):
    return asyncio.run(coro)


def test_overview_reads_semesters_and_both_summaries_for_the_bachelor_course():
    log = []
    module, _ = fake_rusaint(log)
    gateway = RusaintGateway(lambda: module)

    async def go():
        session = await gateway.login("u", "p")
        return await gateway.overview(session)

    overview = run(go())
    assert [entry[0] for entry in log] == ["with_password", "build", "semesters", "recorded_summary", "certificated_summary"]
    assert all(entry[1] == "BACHELOR" for entry in log[2:])
    assert overview.semesters[0].year == 2025


def test_classes_requests_one_semester_without_details_and_reuses_the_application():
    log, builds = [], []
    module, _ = fake_rusaint(log, build_count=builds)
    gateway = RusaintGateway(lambda: module)

    async def go():
        session = await gateway.login("u", "p")
        await gateway.overview(session)
        return await gateway.classes(session, 2025, "TWO")

    result = run(go())
    assert ("classes", "BACHELOR", 2025, "S-TWO", False) in log
    assert len(builds) == 1
    assert result.semester == "TWO" and len(result.classes) == 1


def test_a_missing_native_library_reports_unavailable_without_raising_on_start():
    def loader():
        raise OSError("dlopen failed: /private/path/librusaint_ffi.dylib")

    gateway = RusaintGateway(loader)
    assert gateway.available() is False
    with pytest.raises(GradesError) as raised:
        run(gateway.login("u", "p"))
    assert raised.value.code == "grades_unavailable"
    assert "dlopen" not in str(raised.value) and raised.value.__cause__ is None


def test_the_library_is_loaded_lazily_and_once():
    loads = []

    def loader():
        loads.append(1)
        return fake_rusaint([])[0]

    gateway = RusaintGateway(loader)
    assert loads == []  # constructing the gateway imports nothing
    assert gateway.available() and gateway.available()
    assert loads == [1]


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("login failed: session ID not assigned, got ''", "grades_login_rejected"),
        ("Login Failed", "grades_login_rejected"),
        ("NetworkError: failed to lookup address information", "grades_unavailable"),
        ("failed to parse update document", "grades_unavailable"),
        ("", "grades_unavailable"),
    ],
)
def test_login_errors_map_to_a_code_and_never_carry_the_message(message, code, caplog):
    caplog.set_level(logging.DEBUG)
    module, _ = fake_rusaint([], login_error=RusaintError(f"{message} {SENTINEL_EXCEPTION}"))
    gateway = RusaintGateway(lambda: module)
    with pytest.raises(GradesError) as raised:
        run(gateway.login(SENTINEL_USER, SENTINEL_PASSWORD))
    error = raised.value
    assert error.code == code
    assert error.__cause__ is None and error.__suppress_context__ is True
    assert SENTINEL_EXCEPTION not in str(error) + repr(error) + caplog.text


def test_a_failure_after_login_is_unavailable_not_rejected():
    module, app = fake_rusaint([])
    app.fail = RusaintError("login failed (a page that merely says so)")
    gateway = RusaintGateway(lambda: module)

    async def go():
        session = await gateway.login("u", "p")
        await gateway.overview(session)

    with pytest.raises(GradesError) as raised:
        run(go())
    assert raised.value.code == "grades_unavailable"


def test_a_shape_mismatch_is_unavailable_and_logs_only_the_field_name(caplog):
    caplog.set_level(logging.DEBUG)
    module, app = fake_rusaint([])

    async def bad_semesters(course_type):
        return [semester_obj(2025, "ONE", semester_rank=pair("ZZ-LEAK-VALUE", 1))]

    app.semesters = bad_semesters
    gateway = RusaintGateway(lambda: module)

    async def go():
        session = await gateway.login("u", "p")
        await gateway.overview(session)

    with pytest.raises(GradesError) as raised:
        run(go())
    assert raised.value.code == "grades_unavailable"
    assert "ZZ-LEAK-VALUE" not in caplog.text
    assert "field=semester_rank" in caplog.text


def test_cancellation_is_not_swallowed():
    module, app = fake_rusaint([])

    async def forever(course_type):
        await asyncio.sleep(60)

    app.semesters = forever
    gateway = RusaintGateway(lambda: module)

    async def go():
        session = await gateway.login("u", "p")
        await asyncio.wait_for(gateway.overview(session), 0.05)

    with pytest.raises(TimeoutError):
        run(go())
