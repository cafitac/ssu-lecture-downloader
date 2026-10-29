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


def test_classes_live_and_die_with_the_overview_snapshot(tmp_path):
    env = Env(tmp_path)
    run(env.service.classes(2025, "ONE"))  # creates the snapshot at t0
    env.clock.advance(minutes=20)
    run(env.service.overview())  # joins the same snapshot (it has no overview yet)
    env.clock.advance(minutes=9)
    run(env.service.classes(2025, "ONE"))
    assert len(env.gateway.classes_calls) == 1 and env.gateway.overview_calls == 1
    env.clock.advance(minutes=2)  # 31 minutes after the snapshot began: everything in it expires together
    assert env.service.status().fetched_at is None
    run(env.service.classes(2025, "ONE"))
    assert len(env.gateway.classes_calls) == 2


def test_an_overview_refresh_replaces_the_snapshot_and_its_classes(tmp_path):
    env = Env(tmp_path)
    run(env.service.overview())
    run(env.service.classes(2025, "ONE"))
    env.clock.advance(seconds=5)
    run(env.service.overview(refresh=True))
    run(env.service.classes(2025, "ONE"))
    assert len(env.gateway.classes_calls) == 2


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


def test_an_operation_failure_on_a_fresh_login_is_not_retried(tmp_path):
    env = Env(tmp_path)
    env.gateway.overview_errors = [GradesError("grades_unavailable")]
    assert code_of(env.service.overview()) == "grades_unavailable"
    assert env.gateway.logins == ["alice"] and env.gateway.overview_calls == 1


def test_a_failed_login_is_never_retried_within_one_request(tmp_path):
    env = Env(tmp_path)
    env.gateway.login_error = GradesError("grades_unavailable")
    assert code_of(env.service.overview()) == "grades_unavailable"
    assert env.gateway.logins == ["alice"]


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
    assert env.gateway.logins == ["alice"]  # the login timed out; a failed login is not retried
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


# ---- clear / account switch / revoke while a request is queued (review P2) -----------------------


def queued_behind_a_slow_fetch(env, then):
    """A starts a slow fetch; B queues behind it (captured the current account); `then()` runs; both finish."""
    env.gateway.delay = 0.05

    async def go():
        a = asyncio.create_task(env.service.overview())
        await asyncio.sleep(0.01)
        b = asyncio.create_task(env.service.overview(refresh=True))  # a different flight than A's
        await asyncio.sleep(0.01)
        then()
        return await asyncio.gather(a, b, return_exceptions=True)

    return run(go())


def assert_no_state(env):
    assert env.service._snapshot is None and env.service._session is None


def test_a_waiter_queued_across_an_account_switch_never_logs_in_with_the_old_account(tmp_path):
    env = Env(tmp_path)

    def switch():
        env.service.clear()  # what routers/credentials.py does ...
        env.pair = ("bob", "pw-b")  # ... around saving the new pair
        env.service.clear()

    a, b = queued_behind_a_slow_fetch(env, switch)
    assert isinstance(a, GradesError) and isinstance(b, GradesError)
    assert a.code == b.code == "grades_unavailable"
    assert env.gateway.logins == ["alice"]  # only A's login, which started before the switch
    assert_no_state(env)
    run(env.service.overview())  # the next request is a clean start under the new account
    assert env.gateway.logins == ["alice", "bob"]


def test_a_waiter_queued_across_a_logout_makes_no_gateway_call(tmp_path):
    env = Env(tmp_path)

    def logout():
        env.pair = None
        env.service.clear()

    a, b = queued_behind_a_slow_fetch(env, logout)
    assert isinstance(a, GradesError) and isinstance(b, GradesError)
    assert b.code == "missing_credentials"
    assert env.gateway.logins == ["alice"] and env.gateway.overview_calls <= 1
    assert_no_state(env)


def test_a_waiter_queued_across_a_consent_revoke_makes_no_gateway_call(tmp_path):
    env = Env(tmp_path)
    a, b = queued_behind_a_slow_fetch(env, env.service.revoke_consent)
    assert isinstance(b, GradesError) and b.code == "grades_consent_required"
    assert isinstance(a, GradesError)
    assert env.gateway.logins == ["alice"]
    assert_no_state(env)
    assert not env.consent.path.exists()


def test_no_retry_login_after_a_clear_during_the_failed_attempt(tmp_path):
    env = Env(tmp_path)
    run(env.service.overview())  # a reusable session: the only case that would retry
    env.clock.advance(seconds=5)
    env.gateway.delay = 0.1
    env.gateway.overview_errors = [GradesError("grades_unavailable")]

    async def go():
        task = asyncio.create_task(env.service.overview(refresh=True))
        await asyncio.sleep(0.04)  # inside the (failing) overview call, after the login
        env.service.clear()
        with pytest.raises(GradesError) as raised:
            await task
        return raised.value.code

    assert run(go()) == "grades_unavailable"
    assert env.gateway.logins == ["alice"]  # no second login for the retry
    assert_no_state(env)


# ---- shared single-flight outcomes and the rejection memory (review P3) ---------------------------


def rejecting(env):
    env.gateway.login_error = GradesError("grades_login_rejected")
    env.gateway.delay = 0.05


def test_concurrent_requests_during_a_rejected_login_cause_one_login(tmp_path):
    env = Env(tmp_path)
    rejecting(env)

    async def go():
        return await asyncio.gather(*[env.service.overview() for _ in range(4)], return_exceptions=True)

    results = run(go())
    assert [r.code for r in results] == ["grades_login_rejected"] * 4
    assert env.gateway.logins == ["alice"]


def test_a_rejection_is_remembered_until_the_credentials_change(tmp_path):
    env = Env(tmp_path)
    rejecting(env)
    assert code_of(env.service.overview()) == "grades_login_rejected"
    for _ in range(3):  # "다시 시도", refresh, and the classes of a semester
        assert code_of(env.service.overview(refresh=True)) == "grades_login_rejected"
        assert code_of(env.service.classes(2025, "ONE")) == "grades_login_rejected"
    assert env.gateway.logins == ["alice"]
    env.service.clear()  # changed credentials / logout / revoke
    env.gateway.login_error = None
    run(env.service.overview())
    assert env.gateway.logins == ["alice", "alice"]


def test_a_rejection_does_not_stick_to_another_account(tmp_path):
    env = Env(tmp_path)
    rejecting(env)
    code_of(env.service.overview())
    env.pair = ("bob", "pw-b")
    env.service.clear()
    env.gateway.login_error = None
    run(env.service.overview())
    assert env.gateway.logins == ["alice", "bob"]


def test_a_rejected_login_is_never_retried_within_one_request(tmp_path):
    env = Env(tmp_path)
    env.gateway.login_error = GradesError("grades_login_rejected")
    assert code_of(env.service.overview()) == "grades_login_rejected"
    assert env.gateway.logins == ["alice"]


def test_followers_receive_a_transient_failure_too(tmp_path):
    env = Env(tmp_path)
    env.gateway.delay = 0.05
    env.gateway.overview_errors = [GradesError("grades_unavailable")] * 2

    async def go():
        return await asyncio.gather(*[env.service.overview() for _ in range(3)], return_exceptions=True)

    results = run(go())
    assert [r.code for r in results] == ["grades_unavailable"] * 3
    assert env.gateway.logins == ["alice"]  # one attempt, shared by all three


def test_a_cancelled_leader_does_not_strand_its_followers(tmp_path):
    env = Env(tmp_path)
    env.gateway.delay = 0.2

    async def go():
        leader = asyncio.create_task(env.service.overview())
        await asyncio.sleep(0.02)
        follower = asyncio.create_task(env.service.overview())
        await asyncio.sleep(0.02)
        leader.cancel()
        outcome = await asyncio.gather(follower, return_exceptions=True)
        return outcome[0]

    outcome = run(go())
    assert isinstance(outcome, GradesError) and outcome.code == "grades_unavailable"


# ---- the gateway runs off the API loop (O4 fallback, review P3) ------------------------------------


def test_gateway_coroutines_run_on_a_worker_thread_not_the_caller(tmp_path):
    import threading

    env = Env(tmp_path)
    caller = {}

    async def go():
        caller["id"] = threading.get_ident()
        await env.service.overview()

    run(go())
    assert env.gateway.threads and caller["id"] not in env.gateway.threads
    assert len(env.gateway.threads) == 1  # one dedicated thread for everything


def test_a_blocking_gateway_does_not_block_the_caller_loop_and_the_timeout_still_applies(tmp_path):
    import time

    env = Env(tmp_path)
    env.gateway.blocking = 1.0

    async def go():
        ticks = []

        async def ticker():
            for _ in range(8):
                await asyncio.sleep(0.05)
                ticks.append(time.monotonic())

        started = time.monotonic()
        tick = asyncio.create_task(ticker())
        with pytest.raises(GradesError) as raised:
            await env.service.overview()
        await tick
        return raised.value.code, time.monotonic() - started, ticks

    code, elapsed, ticks = run(go())
    assert code == "grades_unavailable"
    assert len(ticks) == 8  # the loop kept turning while the worker was stuck
    gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    assert max(gaps) < 0.3
    env.service.close()


# ---- login-failure backoff (review cycle 2) ------------------------------------------------------------


def clicking(env, times):
    """Sequential "다시 시도" clicks (refresh, so a cached snapshot never answers); returns the codes."""
    codes = []
    for _ in range(times):
        with pytest.raises(GradesError) as raised:
            run(env.service.overview(refresh=True))
        codes.append(raised.value.code)
    return codes


def test_a_wrong_password_costs_at_most_two_logins_per_window_and_one_per_click(tmp_path):
    env = Env(tmp_path)
    env.gateway.login_error = GradesError("grades_unavailable")  # what the real gateway raises for any login failure
    seen = []
    for _ in range(5):
        before = len(env.gateway.logins)
        clicking(env, 1)
        seen.append(len(env.gateway.logins) - before)
    assert seen == [1, 1, 0, 0, 0]
    assert len(env.gateway.logins) == 2


def test_the_backoff_ends_after_five_minutes(tmp_path):
    env = Env(tmp_path)
    env.gateway.login_error = GradesError("grades_unavailable")
    clicking(env, 3)
    env.clock.advance(minutes=4, seconds=59)
    clicking(env, 1)
    assert len(env.gateway.logins) == 2
    env.clock.advance(seconds=2)
    clicking(env, 1)
    assert len(env.gateway.logins) == 3  # a fresh budget after the pause
    clicking(env, 1)
    assert len(env.gateway.logins) == 4
    clicking(env, 1)
    assert len(env.gateway.logins) == 4


def test_clear_lifts_the_backoff(tmp_path):
    env = Env(tmp_path)
    env.gateway.login_error = GradesError("grades_unavailable")
    clicking(env, 3)
    assert len(env.gateway.logins) == 2
    env.service.clear()  # changed credentials, logout or revoke
    clicking(env, 1)
    assert len(env.gateway.logins) == 3


def test_a_successful_login_resets_the_failure_count(tmp_path):
    env = Env(tmp_path)
    env.gateway.login_error = GradesError("grades_unavailable")
    clicking(env, 1)  # failure 1 -> logins 1
    env.gateway.login_error = None
    env.service._session = None
    run(env.service.overview(refresh=True))  # success resets the count -> logins 2
    env.clock.advance(seconds=5)
    env.service._session = None
    env.gateway.login_error = GradesError("grades_unavailable")
    clicking(env, 2)  # failure 1 again, then failure 2: both reach the gateway
    assert len(env.gateway.logins) == 4
    clicking(env, 1)  # now paused
    assert len(env.gateway.logins) == 4


def test_a_login_timeout_counts_as_a_failed_login(tmp_path):
    env = Env(tmp_path)
    env.gateway.delay = 2.0  # the 0.5 s timeout hits while logging in
    clicking(env, 3)
    assert len(env.gateway.logins) == 2


def test_the_backoff_is_per_account(tmp_path):
    env = Env(tmp_path)
    env.gateway.login_error = GradesError("grades_unavailable")
    clicking(env, 3)
    env.pair = ("bob", "pw-b")
    env.service.clear()
    clicking(env, 1)
    assert env.gateway.logins == ["alice", "alice", "bob"]


def test_an_operation_failure_does_not_count_as_a_login_failure(tmp_path):
    env = Env(tmp_path)
    for _ in range(4):
        env.gateway.overview_errors = [GradesError("grades_unavailable")]
        env.service._session = None
        clicking(env, 1)
    assert len(env.gateway.logins) == 4  # successful logins every time, never paused
