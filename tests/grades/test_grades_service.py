from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest
from grades_fakes import SENTINEL_EXCEPTION, SENTINEL_GPA, FakeClock, FakeGateway

from ssu_lecture_downloader.core.grades.consent import GradesConsentStore
from ssu_lecture_downloader.core.grades.models import GradesError
from ssu_lecture_downloader.core.grades.service import GradesService


class Env:
    def __init__(self, tmp_path, *, consented=True, pair=("alice", "pw-1")):
        self.clock = FakeClock()
        self.gateway = FakeGateway()
        self.pair = pair
        self.consent = GradesConsentStore(tmp_path / "grades-consent.json", now=self.clock)
        self.service = GradesService(self.consent, self.gateway, lambda: self.pair, now=self.clock, overview_timeout=0.5, classes_timeout=0.5)
        if consented:
            self.consent.grant(1)


def run(coro):
    return asyncio.run(coro)


def code_of(coro) -> str:
    with pytest.raises(GradesError) as raised:
        run(coro)
    return raised.value.code


# ---- AC1: no consent, no gateway call ----------------------------------------------------------


def test_without_consent_nothing_reaches_the_gateway(tmp_path):
    env = Env(tmp_path, consented=False)
    assert code_of(env.service.overview()) == "grades_consent_required"
    assert code_of(env.service.classes(2025, "ONE")) == "grades_consent_required"
    assert env.service.status().consent is None
    assert env.gateway.logins == [] and env.gateway.overview_calls == 0 and env.gateway.classes_calls == []


def test_consent_is_checked_before_credentials(tmp_path):
    env = Env(tmp_path, consented=False, pair=None)
    assert code_of(env.service.overview()) == "grades_consent_required"


def test_missing_lms_credentials_is_reported_after_consent(tmp_path):
    env = Env(tmp_path, pair=None)
    assert code_of(env.service.overview()) == "missing_credentials"
    assert env.gateway.logins == []


def test_a_consent_file_with_another_version_blocks_fetching(tmp_path):
    env = Env(tmp_path, consented=False)
    env.consent.path.write_text('{"schema": 1, "consent_version": 9, "granted_at": "2030-01-01T00:00:00Z"}')
    assert code_of(env.service.overview()) == "grades_consent_required"
    assert env.gateway.logins == []


def test_revoke_deletes_the_file_clears_memory_and_blocks_the_next_fetch(tmp_path):
    env = Env(tmp_path)
    run(env.service.overview())
    status = env.service.revoke_consent()
    assert status.consent is None and status.fetched_at is None
    assert not env.consent.path.exists()
    assert code_of(env.service.overview()) == "grades_consent_required"
    assert env.gateway.logins == ["alice"]


def test_grant_consent_rejects_other_versions(tmp_path):
    env = Env(tmp_path, consented=False)
    with pytest.raises(ValueError):
        env.service.grant_consent(2)
    assert env.service.grant_consent(1).consent is not None


# ---- AC9: cache, TTL, refresh, single flight, clearing ------------------------------------------


def test_the_snapshot_is_served_for_30_minutes_then_fetched_again(tmp_path):
    env = Env(tmp_path)
    first = run(env.service.overview())
    env.clock.advance(minutes=29, seconds=59)
    assert run(env.service.overview()) is first
    assert env.gateway.overview_calls == 1 and env.gateway.logins == ["alice"]
    env.clock.advance(seconds=2)
    run(env.service.overview())
    assert env.gateway.overview_calls == 2
    assert env.gateway.logins == ["alice"]  # the u-SAINT session is reused


def test_overview_is_stamped_with_the_fetch_time(tmp_path):
    env = Env(tmp_path)
    assert run(env.service.overview()).fetched_at == "2030-01-02T03:04:05Z"
    assert env.service.status().fetched_at == "2030-01-02T03:04:05Z"


def test_refresh_bypasses_the_snapshot(tmp_path):
    env = Env(tmp_path)
    run(env.service.overview())
    env.clock.advance(minutes=1)
    refreshed = run(env.service.overview(refresh=True))
    assert env.gateway.overview_calls == 2
    assert refreshed.fetched_at == "2030-01-02T03:05:05Z"


def test_two_concurrent_requests_log_in_once(tmp_path):
    env = Env(tmp_path)
    env.gateway.delay = 0.05

    async def go():
        return await asyncio.gather(env.service.overview(), env.service.overview(), env.service.classes(2025, "ONE"))

    first, second, _classes = run(go())
    assert first is second
    assert env.gateway.logins == ["alice"]
    assert env.gateway.overview_calls == 1


def test_a_refresh_that_waited_behind_a_fetch_is_served_by_that_fetch(tmp_path):
    env = Env(tmp_path)
    env.gateway.delay = 0.05

    async def go():
        return await asyncio.gather(env.service.overview(refresh=True), env.service.overview(refresh=True))

    first, second = run(go())
    assert first is second and env.gateway.overview_calls == 1


def test_classes_are_cached_per_semester(tmp_path):
    env = Env(tmp_path)

    async def go():
        a = await env.service.classes(2025, "ONE")
        b = await env.service.classes(2025, "ONE")
        c = await env.service.classes(2025, "TWO")
        return a, b, c

    a, b, c = run(go())
    assert a is b and a is not c
    assert env.gateway.classes_calls == [(2025, "ONE"), (2025, "TWO")]
    assert env.gateway.logins == ["alice"]
    env.clock.advance(seconds=5)
    run(env.service.classes(2025, "ONE", refresh=True))
    assert len(env.gateway.classes_calls) == 3


def test_an_unknown_semester_is_rejected_before_any_work(tmp_path):
    env = Env(tmp_path)
    with pytest.raises(ValueError):
        run(env.service.classes(2025, "AUTUMN"))
    assert env.gateway.logins == []


def test_clear_drops_the_snapshot_and_the_session(tmp_path):
    env = Env(tmp_path)
    run(env.service.overview())
    env.service.clear()
    run(env.service.overview())
    assert env.gateway.overview_calls == 2
    assert env.gateway.logins == ["alice", "alice"]


def test_another_account_never_sees_the_snapshot_or_session(tmp_path):
    env = Env(tmp_path)
    run(env.service.overview())
    env.pair = ("bob", "pw-2")
    assert env.service.status().fetched_at is None
    run(env.service.overview())
    assert env.gateway.logins == ["alice", "bob"]
    run(env.service.classes(2025, "ONE"))
    env.pair = ("alice", "pw-1")
    run(env.service.classes(2025, "ONE"))  # bob's snapshot replaced alice's: a miss
    assert len(env.gateway.classes_calls) == 2


def test_clearing_during_a_fetch_does_not_resurrect_the_data(tmp_path):
    env = Env(tmp_path)
    env.gateway.delay = 0.05

    async def go():
        task = asyncio.create_task(env.service.overview())
        await asyncio.sleep(0.01)
        env.service.clear()  # e.g. logout while the request is in flight
        with pytest.raises(GradesError):
            await task

    run(go())
    assert env.service.status().fetched_at is None
    env.gateway.delay = 0
    run(env.service.overview())
    assert env.gateway.logins == ["alice", "alice"]


# ---- AC5: failures ----------------------------------------------------------------------------


def test_a_failure_on_a_reused_session_retries_once_with_a_fresh_login(tmp_path):
    env = Env(tmp_path)
    run(env.service.overview())
    env.clock.advance(seconds=5)
    env.gateway.overview_errors = [GradesError("grades_unavailable")]
    run(env.service.overview(refresh=True))
    assert env.gateway.logins == ["alice", "alice"]
    assert env.gateway.overview_sessions[1] is not env.gateway.overview_sessions[2]


def test_a_failure_on_a_fresh_login_is_not_retried(tmp_path):
    env = Env(tmp_path)
    env.gateway.overview_errors = [GradesError("grades_unavailable"), GradesError("grades_unavailable")]
    assert code_of(env.service.overview()) == "grades_unavailable"
    assert env.gateway.logins == ["alice"] and env.gateway.overview_calls == 1


def test_the_retry_gives_up_after_one_attempt(tmp_path):
    env = Env(tmp_path)
    run(env.service.overview())
    env.clock.advance(seconds=5)
    env.gateway.overview_errors = [GradesError("grades_unavailable")] * 3
    assert code_of(env.service.overview(refresh=True)) == "grades_unavailable"
    assert env.gateway.overview_calls == 3  # first fetch + the failed reuse + one fresh retry


def test_login_rejection_is_reported_as_such_and_not_retried(tmp_path):
    env = Env(tmp_path)
    env.gateway.login_error = GradesError("grades_login_rejected")
    assert code_of(env.service.overview()) == "grades_login_rejected"
    assert env.gateway.logins == ["alice"]


def test_a_slow_u_saint_times_out_as_unavailable_and_drops_the_session(tmp_path):
    env = Env(tmp_path)
    env.gateway.delay = 2.0
    assert code_of(env.service.overview()) == "grades_unavailable"
    env.gateway.delay = 0
    run(env.service.overview())
    assert env.gateway.logins == ["alice", "alice"]


def test_an_unexpected_gateway_exception_becomes_a_code_without_its_message(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    env = Env(tmp_path)
    env.gateway.login_error = RuntimeError(SENTINEL_EXCEPTION)
    with pytest.raises(GradesError) as raised:
        run(env.service.overview())
    error = raised.value
    assert error.code == "grades_unavailable"
    assert error.__cause__ is None and error.__suppress_context__
    assert SENTINEL_EXCEPTION not in str(error) + caplog.text


def test_a_failed_refresh_keeps_the_still_valid_snapshot(tmp_path):
    env = Env(tmp_path)
    first = run(env.service.overview())
    env.clock.advance(seconds=5)
    env.gateway.overview_errors = [GradesError("grades_unavailable")] * 2
    assert code_of(env.service.overview(refresh=True)) == "grades_unavailable"
    assert run(env.service.overview()) is first


def test_an_expired_snapshot_is_not_served_after_a_failed_fetch(tmp_path):
    env = Env(tmp_path)
    run(env.service.overview())
    env.clock.advance(minutes=31)
    env.gateway.overview_errors = [GradesError("grades_unavailable")] * 2
    assert code_of(env.service.overview()) == "grades_unavailable"


def test_logging_carries_the_code_and_timing_only(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    env = Env(tmp_path)
    run(env.service.overview())
    env.clock.advance(seconds=5)
    env.gateway.overview_errors = [GradesError("grades_unavailable")] * 2
    code_of(env.service.overview(refresh=True))
    lines = [r.getMessage() for r in caplog.records if r.name == "ssu_lecture_downloader.grades"]
    assert lines[0].startswith("grades_fetch result=ok elapsed_ms=")
    assert any(line.startswith("grades_fetch result=grades_unavailable elapsed_ms=") for line in lines)
    text = caplog.text
    assert "alice" not in text and "pw-1" not in text and str(SENTINEL_GPA) not in text


def test_the_ttl_is_configurable(tmp_path):
    env = Env(tmp_path)
    short = GradesService(env.consent, env.gateway, lambda: env.pair, now=env.clock, ttl=timedelta(seconds=10))
    run(short.overview())
    env.clock.advance(seconds=11)
    run(short.overview())
    assert env.gateway.overview_calls == 2
