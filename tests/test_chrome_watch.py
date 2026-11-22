import json
import logging
import signal
import threading
import time
from pathlib import Path

import pytest

from ssu_lecture_downloader.core import chrome_watch as cw
from ssu_lecture_downloader.core.chrome_cdp import CdpClient, CdpClosed, CdpError, WebSocketTransport
from ssu_lecture_downloader.platforms import macos
from ssu_lecture_downloader.platforms.base import PROFILE_DIR_ENV
from ssu_lecture_downloader.platforms.macos import MacOSPlatform

from chrome_fakes import (
    FRAME_SESSION,
    PAGE_SESSION,
    FakeBrowser,
    FakeClock,
    FakePlayer,
    FakePopen,
    KillRecorder,
    make_chrome_binary,
)

FAST = cw.Timings(port_timeout=30.0, port_poll=0.2, tick=0.001, poll_interval=2.0, stall_after=90.0, terminate_grace=3.0, stop_join=10.0)


@pytest.fixture(autouse=True)
def reset_popen():
    FakePopen.instances = []
    FakePopen.silent = False
    yield


def plan_for(item_id: str) -> dict:
    return {
        "ok": True,
        "launch_url": f"https://canvas.ssu.ac.kr/courses/1/modules/items/{item_id}",
        "viewer_url": "https://commons.ssu.ac.kr/x",
        "duration": 20,
        "cookies": [{"name": "xn_api_token", "value": "secret-cookie", "domain": "canvas.ssu.ac.kr", "path": "/", "secure": True, "http_only": True, "same_site": "lax"}],
    }


def items(count: int) -> list[dict]:
    return [{"course_id": "1", "course_name": "UX방법론", "module_item_id": str(n), "title": f"{n}주차", "duration": 20} for n in range(1, count + 1)]


class Harness:
    def __init__(self, tmp_path: Path, players: list[FakePlayer], *, plan=None, chrome=True, timings=FAST) -> None:
        self.browser = FakeBrowser(players)
        self.clock = FakeClock()
        self.profile = tmp_path / "support" / "chrome-watch-profile"
        self.kills = KillRecorder(FakePopen.instances)
        self.candidates = [make_chrome_binary(tmp_path)] if chrome else [tmp_path / "missing"]
        self.plans: list[tuple[str, str, str]] = []

        def provider(course_id, module_item_id, course_name):
            self.plans.append((course_id, module_item_id, course_name))
            return plan(course_id, module_item_id, course_name) if plan else plan_for(module_item_id)

        platform = MacOSPlatform(
            candidates=self.candidates,
            popen=FakePopen,
            killpg=self.kills,
            process_command=lambda pid: None,
            clock=self.clock,
            sleep=self.clock.sleep,
        )
        self.manager = cw.ChromeWatchManager(
            provider,
            chrome_locator=platform,
            process_tree=platform,
            background_launch=False,
            profile_dir=self.profile,
            connect=lambda url: self.connect(url),
            clock=self.clock,
            sleep=self.clock.sleep,
            timings=timings,
        )
        self.urls: list[str] = []

    def connect(self, url: str):
        self.urls.append(url)
        return self.browser

    def wait(self, predicate, timeout: float = 15.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.manager.status()
            if predicate(status):
                return status
            time.sleep(0.005)
        raise AssertionError(f"timed out; status={json.dumps(self.manager.status(), ensure_ascii=False)[:800]}")

    def finish(self):
        return self.wait(lambda s: s["state"] in {"finished", "stopped", "failed"})

    def wait_thread(self):
        run = self.manager._run
        assert run is not None and run.thread is not None
        run.thread.join(10)
        assert not run.thread.is_alive()


# ------------------------------------------------------------------- launch helpers


def test_find_chrome_returns_the_first_executable_candidate(tmp_path):
    chrome = make_chrome_binary(tmp_path)
    assert macos.find_chrome([tmp_path / "nope", chrome]) == chrome
    assert macos.find_chrome([tmp_path / "nope"]) is None
    plain = tmp_path / "plain"
    plain.write_text("x", encoding="utf-8")
    assert macos.find_chrome([plain]) is None


def test_default_candidates_are_the_system_and_user_applications_folders(monkeypatch):
    monkeypatch.undo()  # conftest hides the real candidates from every test
    paths = [str(path) for path in macos.chrome_candidates()]
    assert paths[0] == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert paths[1].endswith("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def test_default_profile_dir_is_inside_the_app_support_folder_and_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.delenv(PROFILE_DIR_ENV, raising=False)
    assert str(MacOSPlatform().chrome_profile_dir()).endswith("Library/Application Support/com.local.ssu-lecture-downloader/chrome-watch-profile")
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path / "p"))
    assert MacOSPlatform().chrome_profile_dir() == tmp_path / "p"


def test_chrome_args_use_a_private_profile_a_free_debug_port_and_no_visibility_tricks():
    args = cw.build_chrome_args("/c/Chrome", "/p/chrome-watch-profile")
    assert args[0] == "/c/Chrome"
    assert "--user-data-dir=/p/chrome-watch-profile" in args
    assert "--disk-cache-size=104857600" in args
    assert "--remote-debugging-port=0" in args
    assert "--no-first-run" in args and "--no-default-browser-check" in args
    assert "--window-size=1000,700" in args
    joined = " ".join(args)
    for forbidden in ("disable-backgrounding", "disable-renderer-backgrounding", "disable-background", "autoplay-policy", "headless"):
        assert forbidden not in joined


def test_devtools_active_port_is_parsed_only_when_complete():
    assert cw.parse_devtools_active_port("9222\n/devtools/browser/abc-1\n") == (9222, "/devtools/browser/abc-1")
    assert cw.parse_devtools_active_port("9222\n") is None
    assert cw.parse_devtools_active_port("") is None
    assert cw.parse_devtools_active_port("abc\n/devtools/browser/x") is None
    assert cw.parse_devtools_active_port("99999\n/devtools/browser/x") is None
    assert cw.parse_devtools_active_port("9222\nnot-a-path") is None


def test_waiting_for_the_port_times_out_and_notices_a_dead_process(tmp_path):
    clock = FakeClock()
    with pytest.raises(cw.ChromeWatchError) as timeout:
        cw.wait_for_devtools_port(tmp_path, timeout=30, poll=0.2, alive=lambda: True, clock=clock, sleep=clock.sleep)
    assert timeout.value.code == "chrome_launch_timeout"
    with pytest.raises(cw.ChromeWatchError) as dead:
        cw.wait_for_devtools_port(tmp_path, timeout=30, poll=0.2, alive=lambda: False, clock=clock, sleep=clock.sleep)
    assert dead.value.code == "chrome_exited"
    (tmp_path / "DevToolsActivePort").write_text("5555\n/devtools/browser/q", encoding="utf-8")
    assert cw.wait_for_devtools_port(tmp_path, timeout=30, poll=0.2, alive=lambda: True, clock=clock, sleep=clock.sleep) == (5555, "/devtools/browser/q")


def test_bridge_cookies_map_to_cdp_cookie_params():
    base = {"name": "a", "value": "b", "domain": ".ssu.ac.kr", "path": "/x", "secure": True, "http_only": True}
    assert cw.cookie_params({**base, "same_site": "lax"}) == {"name": "a", "value": "b", "domain": ".ssu.ac.kr", "path": "/x", "secure": True, "httpOnly": True, "sameSite": "Lax"}
    assert cw.cookie_params({**base, "same_site": "Strict"})["sameSite"] == "Strict"
    assert cw.cookie_params({**base, "same_site": "no_restriction"})["sameSite"] == "None"
    assert "sameSite" not in cw.cookie_params({**base, "same_site": None})
    assert "sameSite" not in cw.cookie_params({**base, "same_site": "unspecified"})
    # SameSite=None needs Secure; without it the attribute is dropped.
    assert "sameSite" not in cw.cookie_params({**base, "secure": False, "same_site": "none"})
    assert cw.cookie_params({**base, "path": ""})["path"] == "/"
    assert cw.cookie_params({"name": "", "value": "v", "domain": "d"}) is None
    assert cw.cookie_params({"name": "n", "value": "v"}) is None


# -------------------------------------------------------------------------- the CDP client


def test_cdp_client_matches_replies_and_forwards_events():
    class Echo:
        def __init__(self):
            self.incoming = __import__("queue").Queue()

        def send(self, text):
            message = json.loads(text)
            self.incoming.put(json.dumps({"method": "Some.event", "params": {"n": 1}, "sessionId": "s"}))
            if message["method"] == "Bad.call":
                self.incoming.put(json.dumps({"id": message["id"], "error": {"code": -1, "message": "nope"}}))
            else:
                self.incoming.put(json.dumps({"id": message["id"], "result": {"echo": message["params"], "session": message.get("sessionId")}}))

        def recv(self):
            return self.incoming.get()

        def close(self):
            self.incoming.put(None)

    events = []
    client = CdpClient(Echo(), lambda *args: events.append(args))
    assert client.call("A.b", {"x": 1}, session_id="S") == {"echo": {"x": 1}, "session": "S"}
    with pytest.raises(CdpError) as error:
        client.call("Bad.call")
    assert error.value.code == -1
    client.close()
    with pytest.raises(CdpClosed):
        client.call("A.b")
    assert ("Some.event", {"n": 1}, "s") in events
    deadline = time.time() + 2
    while time.time() < deadline and not any(event[0] == "CdpClient.closed" for event in events):
        time.sleep(0.005)
    assert any(event[0] == "CdpClient.closed" for event in events)


def test_websocket_transport_talks_to_a_real_local_socket_server():
    from websockets.sync.server import serve

    def handler(connection):
        for text in connection:
            message = json.loads(text)
            connection.send(json.dumps({"method": "Hello.event", "params": {}}))
            connection.send(json.dumps({"id": message["id"], "result": {"method": message["method"]}}))

    with serve(handler, "127.0.0.1", 0) as server:
        port = server.socket.getsockname()[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        events = []
        client = CdpClient(WebSocketTransport(f"ws://127.0.0.1:{port}/devtools/browser/x"), lambda *args: events.append(args))
        assert client.call("Browser.getVersion") == {"method": "Browser.getVersion"}
        client.close()
        server.shutdown()
    assert events and events[0][0] == "Hello.event"


# ------------------------------------------------------------------------ queue behavior


def test_queue_plays_every_lecture_in_one_tab_and_closes_chrome(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer(resume=True), FakePlayer()])
    started = harness.manager.start(items(2))
    assert started["state"] in {"starting", "playing"} and started["total"] == 2

    status = harness.finish()
    harness.wait_thread()

    assert status["state"] == "finished" and status["done_count"] == 2 and status["skipped_count"] == 0
    assert [item["state"] for item in status["items"]] == ["done", "done"]
    assert status["index"] == 2 and status["current"] is None and status["error_code"] is None
    assert status["notices"][-1]["kind"] == "finished" and "2개 시청" in status["notices"][-1]["text"]
    # Plans came from the service provider, one per lecture, with the item's course.
    assert harness.plans == [("1", "1", "UX방법론"), ("1", "2", "UX방법론")]
    # Both lectures were opened in the same tab, with the session cookie set first.
    assert harness.browser.navigations == [plan_for("1")["launch_url"], plan_for("2")["launch_url"]]
    assert [cookie["name"] for cookie in harness.browser.cookies] == ["xn_api_token", "xn_api_token"]
    assert harness.browser.cookies[0]["sameSite"] == "Lax" and harness.browser.cookies[0]["httpOnly"] is True
    methods = harness.browser.methods()
    assert methods.index("Network.setCookie") < methods.index("Page.navigate")
    # The browser endpoint named by DevToolsActivePort is used.
    assert harness.urls == ["ws://127.0.0.1:9222/devtools/browser/abc"]
    # Chrome ran with a private profile in its own process group and was shut down afterwards.
    popen = FakePopen.instances[0]
    assert popen.kwargs["start_new_session"] is True
    assert (4242, signal.SIGTERM) in harness.kills.calls
    assert not (harness.profile.parent / "chrome-watch.pid").exists()
    assert "chrome_watch chrome_stop run_id=" in caplog.text
    assert harness.profile.is_dir()  # the profile stays
    messages = [record.getMessage() for record in caplog.records if record.name == cw.logger.name]
    for event in ("run_start", "item_start", "item_end", "run_end", "progress"):
        assert any(message.startswith(f"chrome_watch {event} ") for message in messages)
    assert "state=finished phase=playing error_code=- done_count=2 skipped_count=0" in messages[-1]
    assert any("kind=resume ok=True" in message for message in messages)
    assert any("kind=front ok=True" in message for message in messages)
    assert any("action=minimize" in message for message in messages)
    for message in messages:
        assert not any(secret.lower() in message.lower() for secret in ("cookie", "token", "http", "://", "?", "1주차", "UX방법론"))


def test_start_button_and_resume_yes_are_pressed_with_trusted_mouse_events(tmp_path):
    harness = Harness(tmp_path, [FakePlayer(resume=True)])
    harness.manager.start(items(1))
    harness.finish()
    mouse = [event for event in harness.browser.mouse if event[1] == "mousePressed"]
    # Start button (box 10,20 100x40 in the top frame -> center 60,40), then the resume "예".
    assert [(event[0], event[2], event[3]) for event in mouse] == [(PAGE_SESSION, 60.0, 40.0), (PAGE_SESSION, 60.0, 40.0)]
    assert harness.browser.player.clicks == ["front", "resume"]
    sequence = [event[1] for event in harness.browser.mouse][:3]
    assert sequence == ["mouseMoved", "mousePressed", "mouseReleased"]


def test_clicks_in_the_cross_origin_player_frame_add_the_iframe_offset(tmp_path):
    harness = Harness(tmp_path, [FakePlayer(in_frame=True)])
    harness.manager.start(items(1))
    status = harness.finish()
    assert status["state"] == "finished"
    pressed = [event for event in harness.browser.mouse if event[1] == "mousePressed"]
    # iframe content box starts at (200, 100); the button center inside the frame is (60, 40).
    assert pressed[0][0] == PAGE_SESSION and (pressed[0][2], pressed[0][3]) == (260.0, 140.0)
    # The helper and the binding were set up in the iframe session too.
    frame_calls = [m["method"] for m in harness.browser.sent if m.get("sessionId") == FRAME_SESSION]
    assert "Runtime.addBinding" in frame_calls and "Page.addScriptToEvaluateOnNewDocument" in frame_calls and "Target.setAutoAttach" in frame_calls
    setup = next(m for m in harness.browser.sent if m["method"] == "Target.setAutoAttach" and m.get("sessionId") == PAGE_SESSION)
    assert setup["params"] == {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}


def test_helper_script_is_installed_for_new_documents_and_never_fakes_playback(tmp_path):
    harness = Harness(tmp_path, [FakePlayer()])
    harness.manager.start(items(1))
    harness.finish()
    install = next(m for m in harness.browser.sent if m["method"] == "Page.addScriptToEvaluateOnNewDocument")
    assert install["params"]["runImmediately"] is True
    source = install["params"]["source"]
    assert ".play(" not in source and "playbackRate" not in source and "visibilityState" not in source and "currentTime =" not in source
    assert ".muted =" not in source and ".muted=" not in source
    binding = next(m for m in harness.browser.sent if m["method"] == "Runtime.addBinding")
    assert binding["params"] == {"name": "ssuWatchSignal"}


def test_a_stalled_lecture_is_reloaded_once_then_skipped_with_a_visible_notice(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer(stall=True), FakePlayer()])
    harness.manager.start(items(2))
    status = harness.finish()

    harness.wait_thread()
    messages = [record.getMessage() for record in caplog.records]
    assert any("chrome_watch stall " in message and "seconds_without_progress=" in message and "action=skip" in message for message in messages)
    assert any("chrome_watch skip " in message and "reason=stalled" in message for message in messages)
    assert any("chrome_watch item_end " in message and "module_item_id=1 outcome=skipped elapsed_seconds=" in message for message in messages)
    assert status["state"] == "finished"
    assert [item["state"] for item in status["items"]] == ["skipped", "done"]
    assert status["items"][0]["reason"] == cw.STALL_SKIP_REASON
    # Reloaded exactly once: the first lecture was navigated twice.
    assert harness.browser.navigations.count(plan_for("1")["launch_url"]) == 2
    kinds = [notice["kind"] for notice in status["notices"]]
    assert kinds == ["stalled", "skipped", "finished"]
    stalled, skipped = status["notices"][0], status["notices"][1]
    assert cw.MINIMIZED_HINT in stalled["text"] and cw.MINIMIZED_HINT in skipped["text"]
    assert skipped["item_title"] == "1주차" and skipped["reason"] == cw.STALL_SKIP_REASON
    assert [notice["id"] for notice in status["notices"]] == [1, 2, 3]


def test_slow_but_moving_playback_is_never_treated_as_a_stall(tmp_path):
    # One second of lecture per poll still counts as time advancing.
    harness = Harness(tmp_path, [FakePlayer(duration=60.0, step=1.0)])
    harness.manager.start(items(1))
    status = harness.finish()
    assert status["state"] == "finished" and status["items"][0]["state"] == "done"
    assert not [n for n in status["notices"] if n["kind"] in {"stalled", "skipped"}]


def test_next_skips_the_current_lecture_as_a_user_choice(tmp_path):
    harness = Harness(tmp_path, [FakePlayer(stall=True), FakePlayer()])
    harness.manager.start(items(2))
    harness.wait(lambda s: s["state"] == "playing" and s["index"] == 0 and len(harness.browser.navigations) >= 1)
    harness.manager.next()
    status = harness.finish()
    assert status["items"][0]["state"] == "skipped" and status["items"][0]["reason"] == cw.USER_SKIP_REASON
    assert status["items"][1]["state"] == "done"
    assert not [n for n in status["notices"] if n["kind"] == "skipped"]  # no alarm for a deliberate skip


def test_stop_kills_the_chrome_process_group_and_keeps_the_profile(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer(stall=True)])
    harness.manager.start(items(1))
    harness.wait(lambda s: len(harness.browser.navigations) >= 1)
    status = harness.manager.stop()
    assert status["state"] == "stopped" and status["active"] is False
    harness.wait_thread()
    assert (4242, signal.SIGTERM) in harness.kills.calls
    assert "chrome_watch user_stop run_id=" in caplog.text
    assert "chrome_watch chrome_stop run_id=" in caplog.text
    assert harness.profile.is_dir()
    assert not (harness.profile.parent / "chrome-watch.pid").exists()


def test_stop_escalates_to_sigkill_when_chrome_ignores_sigterm(tmp_path):
    harness = Harness(tmp_path, [FakePlayer(stall=True)])
    harness.kills.ignore_term = True
    harness.manager.start(items(1))
    harness.wait(lambda s: len(harness.browser.navigations) >= 1)
    harness.manager.stop()
    harness.wait_thread()
    assert [sig for _, sig in harness.kills.calls][:2] == [signal.SIGTERM, signal.SIGKILL]


def test_stopping_while_waiting_for_chrome_to_open_ends_cleanly(tmp_path):
    FakePopen.silent = True  # Chrome never writes DevToolsActivePort
    harness = Harness(tmp_path, [], timings=cw.Timings(port_timeout=10_000.0, tick=0.001, stop_join=10.0))
    harness.manager.start(items(1))
    harness.wait(lambda s: bool(FakePopen.instances))
    status = harness.manager.stop()
    assert status["state"] == "stopped"
    harness.wait_thread()
    assert harness.kills.calls and harness.kills.calls[0] == (4242, signal.SIGTERM)


def test_chrome_that_never_opens_fails_with_a_clear_code_and_is_killed(tmp_path):
    FakePopen.silent = True
    harness = Harness(tmp_path, [])
    harness.manager.start(items(1))
    status = harness.finish()
    assert status["state"] == "failed" and status["error_code"] == "chrome_launch_timeout"
    harness.wait_thread()
    assert (4242, signal.SIGTERM) in harness.kills.calls


def test_a_stale_devtools_port_file_is_removed_before_launch(tmp_path):
    harness = Harness(tmp_path, [FakePlayer()])
    harness.profile.mkdir(parents=True)
    (harness.profile / "DevToolsActivePort").write_text("1111\n/devtools/browser/old\n", encoding="utf-8")
    FakePopen.silent = True  # the new Chrome has not written its own file yet
    harness.manager.start(items(1))
    status = harness.finish()
    assert status["error_code"] == "chrome_launch_timeout"  # the old port was not trusted
    assert harness.urls == []


def test_chrome_not_installed_is_reported_before_anything_starts(tmp_path):
    harness = Harness(tmp_path, [], chrome=False)
    with pytest.raises(cw.ChromeWatchError) as error:
        harness.manager.start(items(1))
    assert error.value.code == "chrome_not_found"
    assert harness.manager.status()["chrome_available"] is False
    assert FakePopen.instances == []


def test_empty_queue_and_double_start_are_rejected(tmp_path):
    harness = Harness(tmp_path, [FakePlayer(stall=True)])
    with pytest.raises(cw.ChromeWatchError) as empty:
        harness.manager.start([{"course_id": "", "module_item_id": "1", "title": "x"}])
    assert empty.value.code == "empty_watch_queue"
    harness.manager.start(items(1))
    with pytest.raises(cw.ChromeWatchBusy):
        harness.manager.start(items(1))
    harness.manager.stop()


def test_duplicates_are_dropped_and_titles_defaulted():
    cleaned = cw._clean_items(
        [
            {"course_id": 1, "module_item_id": 5, "title": "  ", "course_name": "A", "duration": 30},
            {"course_id": "1", "module_item_id": "5", "title": "again"},
            {"course_id": "1", "module_item_id": "6", "title": "x", "duration": -3},
        ]
    )
    assert [(item.course_id, item.module_item_id, item.title, item.duration) for item in cleaned] == [("1", "5", "강의", 30.0), ("1", "6", "x", None)]


def test_a_lecture_whose_plan_fails_is_skipped_and_the_queue_goes_on(tmp_path):
    def plan(course_id, module_item_id, course_name):
        if module_item_id == "1":
            raise cw.ChromeWatchPlanError("network_error")
        return plan_for(module_item_id)

    harness = Harness(tmp_path, [FakePlayer()], plan=plan)
    harness.manager.start(items(2))
    status = harness.finish()
    assert [item["state"] for item in status["items"]] == ["skipped", "done"]
    assert "LMS에 연결하지 못했어요" in status["items"][0]["reason"]
    assert status["notices"][0]["kind"] == "skipped"


@pytest.mark.parametrize("failure, reason_code", [
    (cw.ChromeWatchPlanError("service_error"), "service_error"),
    (cw.ChromeWatchPlanError("network_timeout"), "network_timeout"),
    (RuntimeError("private detail"), "plan_exception"),
    (None, "launch_url_missing"),
    (cw.ChromeWatchPlanError("unsafe code\n"), "plan_error"),
])
def test_plan_skip_logs_explicit_reason_code(tmp_path, caplog, failure, reason_code):
    caplog.set_level(logging.INFO, logger=cw.logger.name)

    def plan(*args):
        if failure is not None:
            raise failure
        return {"launch_url": ""}

    harness = Harness(tmp_path, [], plan=plan)
    harness.manager.start(items(1))
    status = harness.finish()
    harness.wait_thread()
    assert status["state"] == "finished" and status["skipped_count"] == 1
    messages = [record.getMessage() for record in caplog.records]
    assert any(message.startswith("chrome_watch skip ") and message.endswith(f" reason={reason_code}") for message in messages)
    assert status["items"][0]["reason"] == cw.plan_error_reason(failure.code if isinstance(failure, cw.ChromeWatchPlanError) else None)
    if reason_code == "plan_exception":
        assert any(message.startswith("chrome_watch plan_failed run_id=") and message.endswith(" error=RuntimeError") for message in messages)


def test_a_lost_lms_login_fails_the_run_with_the_error_code(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    def plan(course_id, module_item_id, course_name):
        raise cw.ChromeWatchPlanError("session_expired", auth=True)

    harness = Harness(tmp_path, [], plan=plan)
    harness.manager.start(items(1))
    status = harness.finish()
    assert status["state"] == "failed" and status["error_code"] == "session_expired"
    harness.wait_thread()
    assert (4242, signal.SIGTERM) in harness.kills.calls
    assert "chrome_watch run_failed run_id=" in caplog.text
    assert "error_code=session_expired" in caplog.text
    assert "state=failed phase=" in caplog.text


def test_closing_the_chrome_window_ends_the_run_with_a_clear_code(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer(stall=True)])
    harness.manager.start(items(1))
    harness.wait(lambda s: len(harness.browser.navigations) >= 1)
    harness.browser.close_page()
    status = harness.finish()
    assert status["state"] == "failed" and status["error_code"] == "chrome_window_closed"
    assert "Chrome 창이 닫혀" in status["error_message"]
    harness.wait_thread()
    assert "chrome_watch window_closed run_id=" in caplog.text
    assert "chrome_watch chrome_stop run_id=" in caplog.text


def test_a_dropped_connection_with_chrome_exited_ends_the_run(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer(stall=True)])
    harness.manager.start(items(1))
    harness.wait(lambda s: len(harness.browser.navigations) >= 1)
    FakePopen.instances[0].returncode = 1
    harness.browser.incoming.put(None)
    status = harness.finish()
    assert status["state"] == "failed" and status["error_code"] == "chrome_quit_unexpectedly"
    harness.wait_thread()


def test_status_payload_while_playing_carries_position_and_course(tmp_path):
    harness = Harness(tmp_path, [FakePlayer(duration=100.0, step=0.5)])
    harness.manager.start(items(1))
    status = harness.wait(lambda s: s["current"] is not None and s["current"]["position"] not in (None, 0))
    current = status["current"]
    assert current["title"] == "1주차" and current["course_name"] == "UX방법론"
    assert current["duration"] == 100.0 and current["phase"] == "playing"
    assert set(status) >= {"state", "run_id", "active", "chrome_available", "index", "total", "done_count", "skipped_count", "items", "error_code", "notices"}
    harness.manager.stop()


def test_idle_status_before_any_run(tmp_path):
    harness = Harness(tmp_path, [])
    status = harness.manager.status()
    assert status["state"] == "idle" and status["total"] == 0 and status["current"] is None and status["chrome_available"] is True


def test_show_brings_the_window_back(tmp_path):
    harness = Harness(tmp_path, [FakePlayer(stall=True)])
    with pytest.raises(cw.ChromeWatchError):
        harness.manager.show()
    harness.manager.start(items(1))
    harness.wait(lambda s: len(harness.browser.navigations) >= 1)
    harness.manager.show()
    methods = harness.browser.methods()
    assert "Browser.setWindowBounds" in methods and "Target.activateTarget" in methods
    bounds = next(m for m in harness.browser.sent if m["method"] == "Browser.setWindowBounds")
    assert bounds["params"]["bounds"] == {"windowState": "normal"}
    harness.manager.stop()


def test_a_chrome_left_by_an_earlier_backend_run_is_killed_by_its_pid_file(tmp_path):
    profile = tmp_path / "support" / "chrome-watch-profile"
    profile.mkdir(parents=True)
    (profile.parent / "chrome-watch.pid").write_text("777", encoding="utf-8")
    alive = {"yes": True}
    calls: list[tuple[int, int]] = []

    def killpg(pgid, sig):
        calls.append((pgid, sig))
        alive["yes"] = False

    clock = FakeClock()
    platform = MacOSPlatform(
        killpg=killpg,
        process_command=lambda pid: f"/x/Chrome --user-data-dir={profile}" if alive["yes"] else None,
        clock=clock,
        sleep=lambda s: None,
    )
    manager = cw.ChromeWatchManager(
        lambda *a: {},
        chrome_locator=platform,
        process_tree=platform,
        background_launch=False,
        profile_dir=profile,
        clock=clock,
        sleep=lambda s: None,
    )
    manager.shutdown()
    assert calls == [(777, signal.SIGTERM)]
    assert not (profile.parent / "chrome-watch.pid").exists()


def test_a_pid_file_of_an_unrelated_process_is_not_signalled(tmp_path):
    profile = tmp_path / "support" / "chrome-watch-profile"
    profile.mkdir(parents=True)
    (profile.parent / "chrome-watch.pid").write_text("777", encoding="utf-8")
    calls: list = []
    platform = MacOSPlatform(
        killpg=lambda *args: calls.append(args),
        process_command=lambda pid: "/usr/bin/python something-else",
    )
    manager = cw.ChromeWatchManager(
        lambda *a: {},
        chrome_locator=platform,
        process_tree=platform,
        background_launch=False,
        profile_dir=profile,
    )
    manager.shutdown()
    assert calls == []


def test_chrome_window_is_minimized_when_each_lecture_starts_playing(tmp_path):
    harness = Harness(tmp_path, [FakePlayer(), FakePlayer()])
    harness.manager.start(items(2))
    harness.finish()
    harness.wait_thread()

    bounds = [m["params"]["bounds"] for m in harness.browser.sent if m["method"] == "Browser.setWindowBounds"]
    # After real playback of each lecture (moving on can bring the window back).
    assert bounds == [{"windowState": "minimized"}, {"windowState": "minimized"}]



def test_background_launch_uses_open_without_activation_and_a_separate_instance():
    args = cw.build_chrome_args("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", "/tmp/p")
    launch = macos.build_background_launch("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", args)
    assert launch[:6] == ["/usr/bin/open", "-g", "-n", "-W", "-a", "/Applications/Google Chrome.app"]
    assert launch[6] == "--args" and launch[7:] == args[1:]


def test_chrome_main_pid_is_the_browser_process_of_the_dedicated_profile():
    rows = [
        (10, "/usr/bin/open -g -n -W -a /Applications/Google Chrome.app --args --user-data-dir=/tmp/p"),
        (11, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/tmp/p --remote-debugging-port=0"),
        (12, "/Applications/Google Chrome.app/.../Google Chrome Helper --type=renderer --user-data-dir=/tmp/p"),
        (13, "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/me/other"),
    ]
    assert macos.find_chrome_main_pid(rows, "/tmp/p") == 11
    assert macos.find_chrome_main_pid(rows, "/tmp/none") is None


def test_audio_is_silenced_by_chrome_not_by_muting_the_video():
    args = cw.build_chrome_args("/x/Google Chrome", "/tmp/p")
    assert "--mute-audio" in args
    from ssu_lecture_downloader.core import chrome_watch_helper
    source = next(v for k, v in vars(chrome_watch_helper).items() if isinstance(v, str) and "ssuWatchSignal" in v or (isinstance(v, str) and "findVideo" in v))
    # Chrome pauses muted videos in a minimized window, so the helper must not mute them.
    assert ".muted = true" not in source


def test_player_prompts_are_answered_like_a_person_would():
    from ssu_lecture_downloader.core import chrome_watch_helper
    source = "\n".join(v for v in vars(chrome_watch_helper).values() if isinstance(v, str))
    # Resume prompt -> 예, progress-tracking prompt -> 확인.
    assert "이어서 보시겠습니까" in source and "button: '예'" in source
    assert "진도체크를 시작합니다" in source and "button: '확인'" in source


def test_main_video_is_judged_by_the_lms_length_not_a_fixed_ten_seconds():
    driver_cls = next(c for n, c in vars(cw).items() if isinstance(c, type) and hasattr(c, "is_main"))
    probe = type("Probe", (), {"expected_duration": 3027.0, "is_main": driver_cls.is_main})()
    # A 40 s notice clip before a 50 min chapel lecture is not the lecture.
    assert probe.is_main(40) is False
    assert probe.is_main(3026.5) is True
    probe.expected_duration = None
    assert probe.is_main(40) is True and probe.is_main(8) is False


def test_leave_site_prompts_are_answered_without_waiting():
    from ssu_lecture_downloader.core.chrome_cdp import CdpClient

    sent = []

    class Transport:
        def send(self, text):
            sent.append(json.loads(text))

        def recv(self):
            time.sleep(0.01)
            return None

        def close(self):
            pass

    client = CdpClient(Transport(), lambda *a: None)
    client.send_nowait("Page.handleJavaScriptDialog", {"accept": True}, session_id="S1")
    assert sent[-1]["method"] == "Page.handleJavaScriptDialog" and sent[-1]["params"] == {"accept": True} and sent[-1]["sessionId"] == "S1"
    source = open(cw.__file__, encoding="utf-8").read()
    assert '"Page.javascriptDialogOpening"' in source and 'params.get("type") == "beforeunload"' in source


@pytest.mark.parametrize("failure", ["process_exit", "worker_error"])
def test_worker_failure_diagnostics(tmp_path, caplog, monkeypatch, failure):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer(stall=True)])
    if failure == "worker_error":
        def fail_attach(self):
            raise RuntimeError("test worker failure")

        monkeypatch.setattr(cw._Playback, "attach_page", fail_attach)
    harness.manager.start(items(1))
    if failure == "process_exit":
        harness.wait(lambda s: len(harness.browser.navigations) >= 1)
        monkeypatch.setattr(FakePopen.instances[0], "poll", lambda: 7)
    status = harness.finish()
    harness.wait_thread()
    assert status["state"] == "failed"
    if failure == "process_exit":
        assert "exit_code=7 expected=false" in caplog.text
        assert "chrome_watch chrome_exit run_id=" in caplog.text
        assert status["error_code"] == "chrome_quit_unexpectedly"
        assert "chrome_watch window_closed run_id=" not in caplog.text
    else:
        record = next(r for r in caplog.records if "chrome_watch worker_error run_id=" in r.getMessage())
        assert record.exc_info is not None
        assert status["error_code"] == "chrome_watch_error"


@pytest.mark.parametrize('event', ['Target.targetCrashed', 'Inspector.targetCrashed'])
def test_renderer_crash_recovers_same_item_and_completes(tmp_path, caplog, event):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer(resume=True)])
    navigate = harness.browser.navigate

    def crash_once(message, url):
        navigate(message, url)
        if len(harness.browser.navigations) == 1:
            harness.browser.emit(event, {'targetId': 'T1', 'status': 'crashed', 'errorCode': 9}, PAGE_SESSION)

    harness.browser.navigate = crash_once
    recovery_statuses = []
    notice = harness.manager._notice

    def observe_notice(run, kind, level, text, **kwargs):
        notice(run, kind, level, text, **kwargs)
        if kind == 'recover':
            recovery_statuses.append(harness.manager.status())

    harness.manager._notice = observe_notice
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['state'] == 'finished' and status['done_count'] == 1
    assert harness.browser.navigations == [plan_for('1')['launch_url']] * 2
    assert 'status=crashed error_code=9' in caplog.text
    assert 'attempt=1 cause=renderer_gone' in caplog.text
    recovery_commands = harness.browser.sent
    navigations = [i for i, command in enumerate(recovery_commands) if command["method"] == "Page.navigate"]
    between = recovery_commands[navigations[0] + 1:navigations[1]]
    assert not any(command["method"] in {"Page.enable", "Runtime.enable", "Target.attachToTarget", "Network.setCookie"} for command in between)
    assert recovery_commands[navigations[1]]["sessionId"] == PAGE_SESSION
    assert 'Inspector.enable' in harness.browser.methods()
    assert 'Browser.getVersion' in harness.browser.methods()
    assert harness.plans == [('1', '1', 'UX방법론')]
    assert len(recovery_statuses) == 1
    recovering = recovery_statuses[0]
    assert recovering['current']['phase'] == 'opening'
    assert [(n['kind'], n['level']) for n in recovering['notices']] == [('recover', 'info')]
    assert not [n for n in status['notices'] if n['kind'] in {'failed', 'skipped'}]


def test_renderer_crash_mid_item_resumes_after_recovery_and_completes(tmp_path, caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    player = FakePlayer()
    harness = Harness(tmp_path, [player])
    poll = player.poll
    reload = player.reload
    reports = []
    stopped_at = None

    def crash_mid_playback():
        nonlocal stopped_at
        report = poll()
        reports.append((len(harness.browser.navigations), report))
        if stopped_at is None and (report.get("video") or {}).get("time") == 10:
            stopped_at = player.time
            harness.browser.emit("Target.targetCrashed", {"targetId": "T1", "status": "crashed", "errorCode": 9})
        return report

    def reload_with_resume_prompt():
        assert stopped_at == 10
        reload()
        player.resume = True
        # Model the LMS's saved position, retained when its resume prompt is accepted.
        player.time = stopped_at

    monkeypatch.setattr(player, "poll", crash_mid_playback)
    monkeypatch.setattr(player, "reload", reload_with_resume_prompt)
    harness.manager.start(items(1))
    harness.wait_thread()

    status = harness.manager.status()
    assert stopped_at == 10
    assert harness.browser.navigations == [plan_for("1")["launch_url"]] * 2
    assert any(navigation == 2 and report["resume"] is True for navigation, report in reports)
    assert player.clicks == ["front", "front", "resume"]
    resumed_times = [report["video"]["time"] for navigation, report in reports if navigation == 2 and report.get("video")]
    assert resumed_times[0] > stopped_at
    assert resumed_times[-1] == player.duration
    messages = [record.getMessage() for record in caplog.records]
    recovery = next(i for i, message in enumerate(messages) if "attempt=1 cause=renderer_gone" in message)
    resume = next(i for i, message in enumerate(messages) if "kind=resume ok=True" in message)
    assert recovery < resume
    assert status["state"] == "finished" and status["done_count"] == 1


def test_cdp_closed_with_chrome_alive_reconnects_using_current_port(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer()])
    first = harness.browser
    replacement = FakeBrowser([FakePlayer(resume=True)])
    navigate = first.navigate

    def disconnect(message, url):
        navigate(message, url)
        (harness.profile / 'DevToolsActivePort').write_text('9333\n/devtools/browser/new\n')
        harness.browser = replacement
        first.close()

    first.navigate = disconnect
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['state'] == 'finished' and status['done_count'] == 1
    assert harness.urls[-1] == 'ws://127.0.0.1:9333/devtools/browser/new'
    assert replacement.navigations == [plan_for('1')['launch_url']]
    assert 'attempt=1 cause=cdp_closed' in caplog.text
    assert harness.manager._run.client is None


def test_three_recoveries_then_repeated_renderer_failure(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer()])
    navigate = harness.browser.navigate

    def crash(message, url):
        navigate(message, url)
        harness.browser.emit('Target.targetCrashed', {'targetId': 'T1', 'status': 'crashed', 'errorCode': 9})

    harness.browser.navigate = crash
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['state'] == 'failed'
    assert status['error_code'] == 'chrome_recover_exhausted'
    assert len(harness.browser.navigations) == 4
    assert caplog.text.count('chrome_watch recover run_id=') == 3
    assert caplog.text.count('chrome_watch renderer_gone run_id=') == 4
    assert [(n['kind'], n['level']) for n in status['notices']] == [
        ('recover', 'info'), ('recover', 'info'), ('recover', 'info'), ('failed', 'error'),
    ]
    assert len([n for n in status['notices'] if n['kind'] == 'failed']) == 1


def test_user_stop_during_reconnect_stops_cleanly(tmp_path):
    harness = Harness(tmp_path, [FakePlayer()])
    first = harness.browser
    navigate = first.navigate
    replacement = FakeBrowser([FakePlayer()])

    def disconnect(message, url):
        navigate(message, url)
        first.close()

    def connect(url):
        harness.urls.append(url)
        if len(harness.urls) == 1:
            return first
        harness.manager._run.stop.set()
        return replacement

    first.navigate = disconnect
    harness.connect = connect
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['state'] == 'stopped' and status['error_code'] is None
    assert replacement.navigations == []


def test_lms_failure_warning_contains_only_step_and_status(caplog):
    from types import SimpleNamespace
    from ssu_lecture_downloader.service.api_errors import lms_http_error_detail

    service = SimpleNamespace(http=SimpleNamespace(trace=SimpleNamespace(last_entry=lambda: {
        'step': 'lecture_launch', 'url': 'https://example.com/private?token=secret',
    })))
    exc = Exception('https://example.com/private?token=secret')
    exc.response = SimpleNamespace(status_code=502)
    with caplog.at_level(logging.WARNING):
        code, _, _ = lms_http_error_detail(exc, service)
    assert code == 'lms_request_failed:lecture_launch:502'
    assert 'lms_request_failed step=lecture_launch status=502' in caplog.text
    assert 'example.com' not in caplog.text and 'secret' not in caplog.text


def test_user_closing_crashed_tab_stops_recovery(tmp_path):
    harness = Harness(tmp_path, [FakePlayer()])
    browser = harness.browser
    handle = browser.handle
    navigate = browser.navigate

    def commands(message):
        if browser.navigations and message['method'] == 'Target.getTargets':
            return browser.reply(message, {'targetInfos': []})
        if message['method'] == 'Target.createTarget':
            return browser.reply(message, {'targetId': 'T2'})
        return handle(message)

    def crash_once(message, url):
        navigate(message, url)
        if len(browser.navigations) == 1:
            browser.emit('Target.targetCrashed', {'targetId': 'T1'})
            browser.close_page()

    browser.handle = commands
    browser.navigate = crash_once
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['error_code'] == 'chrome_window_closed'
    assert 'Target.createTarget' not in browser.methods()


@pytest.mark.parametrize("event", ["Inspector.targetCrashed", "Target.targetCrashed"])
def test_iframe_crash_recovers(tmp_path, event):
    harness = Harness(tmp_path, [FakePlayer(in_frame=True)])
    browser = harness.browser
    navigate = browser.navigate

    def crash_once(message, url):
        navigate(message, url)
        if len(browser.navigations) == 1:
            browser.emit(event, {"targetId": "F2"}, FRAME_SESSION)

    browser.navigate = crash_once
    harness.manager.start(items(1))
    harness.wait_thread()
    assert harness.manager.status()["state"] == "finished"
    assert len(browser.navigations) == 2
    assert any(m["method"] == "Inspector.enable" and m.get("sessionId") == FRAME_SESSION for m in browser.sent)


def test_duplicate_crash_events_keep_target_details_once(tmp_path, caplog):
    harness = Harness(tmp_path, [FakePlayer()])
    browser = harness.browser
    navigate = browser.navigate

    def crash_once(message, url):
        navigate(message, url)
        if len(browser.navigations) == 1:
            browser.emit("Inspector.targetCrashed", {}, PAGE_SESSION)
            browser.emit("Target.targetCrashed", {"targetId": "T1", "status": "crashed", "errorCode": 9})

    browser.navigate = crash_once
    harness.manager.start(items(1))
    harness.wait_thread()
    assert harness.manager.status()["state"] == "finished"
    records = [r.getMessage() for r in caplog.records if "chrome_watch renderer_gone " in r.getMessage()]
    assert len(records) == 1
    assert "status=crashed error_code=9" in records[0]


def test_chrome_exits_after_first_reconnect_attempt(tmp_path):
    harness = Harness(tmp_path, [FakePlayer()])
    first = harness.browser
    navigate = first.navigate

    def disconnect(message, url):
        navigate(message, url)
        first.close()

    def connect(url):
        harness.urls.append(url)
        if len(harness.urls) == 1:
            return first
        FakePopen.instances[0].returncode = 7
        raise OSError("Chrome is exiting")

    first.navigate = disconnect
    harness.connect = connect
    harness.manager.start(items(1))
    harness.wait_thread()
    assert harness.manager.status()["error_code"] == "chrome_quit_unexpectedly"
    assert len(harness.urls) == 2


@pytest.mark.parametrize("failure", ["version_error", "version_timeout", "attach_error", "attach_timeout"])
def test_recovery_protocol_failure_counts_as_try(tmp_path, monkeypatch, caplog, failure):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer()])
    first = harness.browser
    replacement = FakeBrowser([FakePlayer()])
    browsers = [first, replacement, FakeBrowser([FakePlayer()])]

    def connect(url):
        harness.urls.append(url)
        return browsers[len(harness.urls) - 1]

    harness.connect = connect
    navigate = first.navigate

    def disconnect(message, url):
        navigate(message, url)
        harness.browser = replacement
        first.close()

    original = CdpClient.call
    failed = False

    def call(client, method, *args, **kwargs):
        nonlocal failed
        target = "Browser.getVersion" if failure.startswith("version") else "Target.attachToTarget"
        if client._transport is replacement and method == target and not failed:
            failed = True
            if failure.endswith("timeout"):
                raise cw.CdpTimeout(target)
            raise CdpError(-32000, "test")
        return original(client, method, *args, **kwargs)

    monkeypatch.setattr(CdpClient, "call", call)
    first.navigate = disconnect
    harness.manager.start(items(1))
    harness.wait_thread()
    assert failed
    assert harness.manager.status()["state"] == "finished"
    assert caplog.text.count("chrome_watch recover run_id=") == 2

    if failure == "attach_timeout":
        assert len(harness.urls) == 3
        assert "attempt=2 cause=cdp_timeout method=Target.attachToTarget" in caplog.text


@pytest.mark.parametrize("background", [False, True])
def test_disconnecting_chrome_gets_exit_grace_before_recovery(tmp_path, monkeypatch, caplog, background):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer()])
    browser = harness.browser
    navigate = browser.navigate
    checks = 0

    def alive():
        nonlocal checks
        checks += 1
        return checks < 3

    def disconnect(message, url):
        navigate(message, url)
        if background:
            harness.manager._background_launch = True
            monkeypatch.setattr(harness.manager._process_tree, "find_main_pid", lambda profile: 4242 if alive() else None)
        else:
            monkeypatch.setattr(FakePopen.instances[0], "poll", lambda: None if alive() else 7)
        browser.close()

    browser.navigate = disconnect
    harness.manager.start(items(1))
    harness.wait_thread()
    assert harness.manager.status()["error_code"] == "chrome_quit_unexpectedly"
    assert len(harness.urls) == 1
    assert "chrome_watch recover run_id=" not in caplog.text


@pytest.mark.parametrize(("method", "failure"), [
    ("Page.navigate", cw.CdpError),
    ("Page.navigate", cw.CdpTimeout),
    ("Input.dispatchMouseEvent", cw.CdpError),
    ("Input.dispatchMouseEvent", cw.CdpTimeout),
], ids=["navigate-error", "navigate-timeout", "click-error", "click-timeout"])
def test_queued_crash_is_drained_before_protocol_failure_decision(tmp_path, monkeypatch, method, failure):
    # given: a renderer crash is queued immediately before the command fails.
    harness = Harness(tmp_path, [FakePlayer()])
    browser = harness.browser
    handle = browser.handle
    failed = False

    def commands(message):
        nonlocal failed
        if message["method"] == method and not failed:
            failed = True
            browser.emit("Target.targetCrashed", {"targetId": "T1"})
            browser.incoming.put(json.dumps({"id": message["id"], "error": {"code": -32000, "message": "Target crashed"}}))
            return
        return handle(message)

    browser.handle = commands
    original = CdpClient.call

    def call(client, command, *args, **kwargs):
        try:
            return original(client, command, *args, **kwargs)
        except CdpError as exc:
            if command == method and failure is cw.CdpTimeout:
                raise cw.CdpTimeout(command) from exc
            raise

    monkeypatch.setattr(CdpClient, "call", call)
    # when
    harness.manager.start(items(1))
    harness.wait_thread()
    # then
    status = harness.manager.status()
    assert failed
    assert status["state"] == "finished"
    assert [(n["kind"], n["level"]) for n in status["notices"]] == [("recover", "info"), ("finished", "success")]


def test_user_close_before_recovery_attach_wins(tmp_path):
    # given: close arrives after the recovery notice, before reattaching.
    harness = Harness(tmp_path, [FakePlayer()])
    browser = harness.browser
    navigate = browser.navigate
    notice = harness.manager._notice

    def crash(message, url):
        navigate(message, url)
        browser.emit("Target.targetCrashed", {"targetId": "T1"})

    def close_on_recover(run, kind, level, text, **kwargs):
        notice(run, kind, level, text, **kwargs)
        if kind == "recover":
            # The event queue is the boundary consumed by _recover_page.
            run_events = run.client._on_event
            run_events("Target.targetDestroyed", {"targetId": "T1"}, None)

    browser.navigate = crash
    harness.manager._notice = close_on_recover
    # when
    harness.manager.start(items(1))
    harness.wait_thread()
    # then
    assert harness.manager.status()["error_code"] == "chrome_window_closed"
    assert len(browser.navigations) == 1


@pytest.mark.parametrize("method", ["Page.enable", "Runtime.enable"])
def test_fake_crashed_page_domains_timeout_until_navigate(method):
    browser = FakeBrowser([FakePlayer()])
    events = []
    client = CdpClient(browser, lambda method, *args: events.append(method))
    try:
        browser.emit("Inspector.targetCrashed", {}, PAGE_SESSION)
        with pytest.raises(cw.CdpTimeout):
            client.call(method, session_id=PAGE_SESSION, timeout=0.01)
        client.call("Inspector.enable", session_id=PAGE_SESSION, timeout=1)
        client.call("Page.navigate", {"url": plan_for("1")["launch_url"]}, session_id=PAGE_SESSION, timeout=1)
        client.call(method, session_id=PAGE_SESSION, timeout=1)
        assert events.count("Inspector.targetCrashed") == 2
        assert events.count("Inspector.targetReloadedAfterCrash") == 1
    finally:
        client.close()


def test_replayed_crash_during_revival_recovers(tmp_path, caplog):
    harness = Harness(tmp_path, [FakePlayer()])
    browser = harness.browser
    navigate = browser.navigate
    handle = browser.handle

    def crash_once(message, url):
        navigate(message, url)
        if len(browser.navigations) == 1:
            browser.emit("Target.targetCrashed", {"targetId": "T1", "status": "crashed", "errorCode": 9})

    def replay_after_revival(message):
        replay = message["method"] == "Page.navigate" and bool(browser.navigations)
        handle(message)
        if replay:
            # The stale notification follows the reload event, so only the
            # reviving-session guard prevents it from marking the page crashed.
            browser.emit("Inspector.targetCrashed", {}, PAGE_SESSION)
            browser.crashed_sessions.clear()  # A replay does not crash the new renderer.

    browser.navigate = crash_once
    browser.handle = replay_after_revival
    harness.manager.start(items(1))
    harness.wait_thread()
    assert harness.manager.status()["state"] == "finished"
    assert len(browser.navigations) == 2
    assert caplog.text.count("chrome_watch renderer_gone run_id=") == 1


def test_recovery_failure_discards_detached_session(tmp_path, monkeypatch):
    harness = Harness(tmp_path, [FakePlayer()])
    browser = harness.browser
    navigate = browser.navigate
    attach = cw._Playback.attach_page
    calls = 0

    def crash_once(message, url):
        navigate(message, url)
        if len(browser.navigations) == 1:
            browser.emit("Target.targetCrashed", {"targetId": "T1"})

    def fail_reattach(playback):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise cw.CdpError(-32000, "reattach failed")
        if calls == 3:
            assert playback.page_session is None
            assert not playback.sessions
        attach(playback)

    browser.navigate = crash_once
    monkeypatch.setattr(cw._Playback, "attach_page", fail_reattach)
    harness.manager.start(items(1))
    harness.wait_thread()
    assert harness.manager.status()["state"] == "finished"
    assert calls == 3


@pytest.mark.parametrize("failure", ["timeout", "error"])
def test_failed_same_session_revival_retries_navigate_first(tmp_path, monkeypatch, caplog, failure):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer()])
    browser = harness.browser
    navigate = browser.navigate
    original = CdpClient.call
    failed = False

    def crash_once(message, url):
        navigate(message, url)
        if len(browser.navigations) == 1:
            browser.emit("Target.targetCrashed", {"targetId": "T1", "status": "crashed", "errorCode": 9})

    def call(client, method, *args, **kwargs):
        nonlocal failed
        if method == "Page.navigate" and browser.crashed_sessions and not failed:
            failed = True
            if failure == "timeout":
                raise cw.CdpTimeout("revival timed out")
            browser.fail_methods[method] = "revival failed"
            try:
                return original(client, method, *args, **kwargs)
            finally:
                browser.fail_methods.pop(method)
                assert PAGE_SESSION in browser.crashed_sessions
        return original(client, method, *args, **kwargs)

    browser.navigate = crash_once
    monkeypatch.setattr(CdpClient, "call", call)
    harness.manager.start(items(1))
    harness.wait_thread()
    assert failed
    assert harness.manager.status()["state"] == "finished"
    assert len(browser.navigations) == 2
    assert caplog.text.count("chrome_watch recover run_id=") == 2
    assert caplog.text.count("chrome_watch renderer_gone run_id=") == 1
    commands = browser.sent
    first_navigation = next(i for i, command in enumerate(commands) if command["method"] == "Page.navigate")
    revival_navigation = max(i for i, command in enumerate(commands) if command["method"] == "Page.navigate")
    # No domain setup may run against the still-crashed target between attempts.
    assert not any(command["method"] in {"Page.enable", "Runtime.enable"} for command in commands[first_navigation + 1:revival_navigation])


def test_profile_cache_allowlist():
    assert cw.PROFILE_CACHE_DIRS == (
        "disk-cache", "GraphiteDawnCache", "GrShaderCache", "ShaderCache", "Default/Cache",
        "Default/Code Cache", "Default/GPUCache", "Default/DawnGraphiteCache", "Default/DawnWebGPUCache",
        "BrowserMetrics", "optimization_guide_model_store", "component_crx_cache", "WasmTtsEngine",
        "OnDeviceHeadSuggestModel",
    )


@pytest.mark.parametrize("relative", ["disk-cache", "Default/Cache", "optimization_guide_model_store"])
def test_profile_trim_removes_only_regenerable_dirs(tmp_path, caplog, relative):
    cache = tmp_path / relative
    cache.mkdir(parents=True)
    (cache / "data").write_bytes(b"cache" * 20)
    protected = ("Default/Cookies", "Default/Local Storage/data", "Default/IndexedDB/data",
                 "Default/Preferences", "Default/Login Data", "Safe Browsing/data", "WidevineCdm/data")
    for name in protected:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"protected\x00\xff")
    with caplog.at_level(logging.INFO, logger=cw.logger.name):
        cw.trim_chrome_profile(tmp_path, threshold_bytes=90)
    assert not cache.exists()
    for name in protected:
        assert (tmp_path / name).read_bytes() == b"protected\x00\xff"
    assert "chrome_watch profile_trim before_mb=" in caplog.text
    assert "after_mb=" in caplog.text and "removed=1" in caplog.text


@pytest.mark.parametrize("threshold", [5, 6])
def test_profile_trim_leaves_small_profile_untouched(tmp_path, caplog, threshold):
    cache = tmp_path / "Default/Cache"
    cache.mkdir(parents=True)
    data = cache / "data"
    data.write_bytes(b"12345")
    before = data.stat().st_mtime_ns
    cw.trim_chrome_profile(tmp_path, threshold_bytes=threshold)
    assert data.read_bytes() == b"12345"
    assert data.stat().st_mtime_ns == before
    assert "profile_trim" not in caplog.text


@pytest.mark.parametrize("link_name", ["Default", "component_crx_cache", "Default/Cache"])
def test_profile_trim_does_not_follow_symlinks(tmp_path, link_name):
    profile = tmp_path / "profile"
    profile.mkdir()
    outside = tmp_path / "outside"
    (outside / "Cache").mkdir(parents=True)
    (outside / "Cache/data").write_bytes(b"keep")
    link = profile / link_name
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside, target_is_directory=True)
    (profile / "large").write_bytes(b"xx")
    cw.trim_chrome_profile(profile, threshold_bytes=1)
    assert link.is_symlink()
    assert (outside / "Cache/data").read_bytes() == b"keep"


def test_profile_trim_continues_after_deletion_error(tmp_path, monkeypatch, caplog):
    for name in ("BrowserMetrics", "ShaderCache"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "data").write_bytes(b"xx")
    real_rmtree = cw.shutil.rmtree

    def remove(path):
        if path.name == "BrowserMetrics":
            raise PermissionError("locked")
        real_rmtree(path)

    monkeypatch.setattr(cw.shutil, "rmtree", remove)
    with caplog.at_level(logging.INFO, logger=cw.logger.name):
        cw.trim_chrome_profile(tmp_path, threshold_bytes=1)
    assert (tmp_path / "BrowserMetrics/data").read_bytes() == b"xx"
    assert not (tmp_path / "ShaderCache").exists()
    assert "removed=1" in caplog.text


@pytest.mark.parametrize("running", [False, True])
def test_launch_trims_only_when_chrome_is_not_running(tmp_path, monkeypatch, running):
    h = Harness(tmp_path, [FakePlayer()])
    calls = []
    monkeypatch.setattr(h.manager._process_tree, "is_stopped", lambda profile: not running)
    monkeypatch.setattr(cw, "trim_chrome_profile", lambda profile: calls.append(profile))
    h.manager.start(items(1))
    assert h.finish()["state"] == "finished"
    h.wait_thread()
    assert calls == ([] if running else [h.profile])


def test_deletion_failure_does_not_block_launch(tmp_path, monkeypatch):
    h = Harness(tmp_path, [FakePlayer()])
    cache = h.profile / "Default/Cache"
    cache.mkdir(parents=True)
    (cache / "data").write_bytes(b"xx")
    real_trim = cw.trim_chrome_profile
    monkeypatch.setattr(cw, "trim_chrome_profile", lambda profile: real_trim(profile, threshold_bytes=1))
    monkeypatch.setattr(h.manager._process_tree, "is_stopped", lambda profile: True)

    def fail(path):
        raise PermissionError("locked")

    monkeypatch.setattr(cw.shutil, "rmtree", fail)
    h.manager.start(items(1))
    assert h.finish()["state"] == "finished"
    h.wait_thread()
    assert FakePopen.instances
    assert (cache / "data").read_bytes() == b"xx"


def test_explicit_disk_cache_is_measured_and_trimmed(tmp_path):
    assert f"--disk-cache-dir={tmp_path / 'disk-cache'}" in cw.build_chrome_args('chrome', tmp_path)
    cache = tmp_path / 'disk-cache'
    cache.mkdir()
    (cache / 'data').write_bytes(b'123456')
    assert cw._profile_size(tmp_path) == 6
    cw.trim_chrome_profile(tmp_path, threshold_bytes=5)
    assert not cache.exists()


def test_trim_skips_when_residual_already_exceeds_threshold(tmp_path, caplog):
    (tmp_path / 'Cookies').write_bytes(b'123456')
    cache = tmp_path / 'disk-cache'
    cache.mkdir()
    (cache / 'data').write_bytes(b'123456')
    cw.trim_chrome_profile(tmp_path, threshold_bytes=5)
    assert (cache / 'data').exists()
    assert 'profile_trim_skipped over_mb=' in caplog.text
    assert 'reason=residual_over_threshold' in caplog.text
    assert caplog.records[-1].levelno == logging.WARNING


def test_trim_warns_when_deletion_leaves_profile_over_threshold(tmp_path, monkeypatch, caplog):
    cache = tmp_path / 'disk-cache'
    cache.mkdir()
    (cache / 'data').write_bytes(b'123456')
    def fail(path):
        raise PermissionError('locked')
    monkeypatch.setattr(cw.shutil, 'rmtree', fail)
    cw.trim_chrome_profile(tmp_path, threshold_bytes=5)
    assert any(r.levelno == logging.WARNING and 'after_mb=' in r.message for r in caplog.records)


@pytest.mark.parametrize('state', ['stopped', 'running', 'unknown', 'exception'])
def test_legacy_cache_cleanup_requires_known_stopped_chrome(tmp_path, monkeypatch, caplog, state):
    from types import SimpleNamespace
    h = Harness(tmp_path, [FakePlayer()])
    root = tmp_path / 'Caches' / 'bundle'
    legacy = root / 'chrome-watch-profile'
    for name in ('chrome-watch-profile', 'WebKit', 'other'):
        (root / name).mkdir(parents=True)
        (root / name / 'data').write_bytes(b'keep')
    h.manager._app_paths = SimpleNamespace(chrome_legacy_cache_dir=lambda: legacy)
    def find(profile):
        if state == 'exception':
            raise OSError('process lookup failed')
        if state == 'unknown':
            return None
        return state == 'stopped'
    monkeypatch.setattr(h.manager._process_tree, 'is_stopped', find)
    calls = []
    monkeypatch.setattr(cw, 'trim_chrome_profile', lambda profile: calls.append(profile))
    h.manager.start(items(1))
    assert h.finish()['state'] == 'finished'
    h.wait_thread()
    assert legacy.exists() == (state != 'stopped')
    if state != 'stopped':
        assert (legacy / 'data').read_bytes() == b'keep'
        reason = 'process_running' if state == 'running' else 'process_unknown'
        for step in ('legacy', 'trim'):
            assert f'step={step} reason={reason}' in caplog.text
    assert calls == ([h.profile] if state == 'stopped' else [])
    assert (root / 'WebKit/data').read_bytes() == b'keep'
    assert (root / 'other/data').read_bytes() == b'keep'


@pytest.mark.parametrize('failure', ['exit', 'exception'])
def test_macos_process_listing_failure_reports_unknown(monkeypatch, failure):
    import subprocess
    from ssu_lecture_downloader.platforms import macos
    def run(*args, **kwargs):
        if failure == 'exception':
            raise OSError('ps failed')
        return subprocess.CompletedProcess(args, 1, stdout='')
    monkeypatch.setattr(macos.subprocess, 'run', run)
    assert macos.default_list_processes() == []
    platform = MacOSPlatform()
    assert platform.find_main_pid(Path('/profile')) is None
    assert platform.is_stopped(Path('/profile')) is None


def test_legacy_cache_path_is_platform_owned(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    assert MacOSPlatform(environ={}).chrome_legacy_cache_dir() == (
        tmp_path / 'Library/Caches/com.local.ssu-lecture-downloader/chrome-watch-profile'
    )


def test_unexpected_trim_failure_does_not_block_launch(tmp_path, monkeypatch):
    h = Harness(tmp_path, [FakePlayer()])
    assert h.manager._app_paths is None
    monkeypatch.setattr(h.manager._process_tree, 'is_stopped', lambda profile: True)
    def fail(profile):
        raise RuntimeError('unexpected maintenance failure')
    monkeypatch.setattr(cw, 'trim_chrome_profile', fail)
    h.manager.start(items(1))
    assert h.finish()['state'] == 'finished'
    h.wait_thread()


def test_user_paths_are_isolated(tmp_path):
    platform = MacOSPlatform()
    for path in (Path.home(), platform.chrome_legacy_cache_dir(), platform.app_data_dir(), platform.default_download_dir()):
        assert path == tmp_path or tmp_path in path.parents


@pytest.mark.parametrize("relation", ["equal", "profile_inside", "legacy_inside"])
def test_legacy_overlap_is_preserved(tmp_path, monkeypatch, caplog, relation):
    from types import SimpleNamespace
    h = Harness(tmp_path, [FakePlayer()])
    legacy = {"equal": h.profile, "profile_inside": h.profile.parent, "legacy_inside": h.profile / "legacy"}[relation]
    legacy.mkdir(parents=True, exist_ok=True)
    marker = legacy / "keep"
    marker.write_text("keep")
    h.manager._app_paths = SimpleNamespace(chrome_legacy_cache_dir=lambda: legacy)
    monkeypatch.setattr(h.manager._process_tree, "is_stopped", lambda profile: True)
    h.manager.start(items(1))
    assert h.finish()["state"] == "finished"
    h.wait_thread()
    assert marker.read_text() == "keep"
    assert "step=legacy reason=profile_overlap" in caplog.text


def test_legacy_failure_still_trims(tmp_path, monkeypatch, caplog):
    from types import SimpleNamespace
    h = Harness(tmp_path, [FakePlayer()])
    legacy = tmp_path / "chrome-watch-profile"
    legacy.mkdir()
    h.manager._app_paths = SimpleNamespace(chrome_legacy_cache_dir=lambda: legacy)
    monkeypatch.setattr(h.manager._process_tree, "is_stopped", lambda profile: True)
    def fail(path):
        raise PermissionError("private path must not be logged")
    monkeypatch.setattr(cw.shutil, "rmtree", fail)
    calls = []
    monkeypatch.setattr(cw, "trim_chrome_profile", calls.append)
    h.manager.start(items(1))
    assert h.finish()["state"] == "finished"
    h.wait_thread()
    assert calls == [h.profile]
    assert "step=legacy reason=removal_failed" in caplog.text
    assert "private path" not in caplog.text


def test_trim_preserves_models_when_cache_is_enough(tmp_path):
    for name, data in (("disk-cache", b"123456"), ("optimization_guide_model_store", b"123")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "data").write_bytes(data)
    cw.trim_chrome_profile(tmp_path, threshold_bytes=5)
    assert not (tmp_path / "disk-cache").exists()
    assert (tmp_path / "optimization_guide_model_store/data").read_bytes() == b"123"


@pytest.mark.parametrize("residual", [5, 6])
def test_residual_threshold_boundary(tmp_path, residual):
    (tmp_path / "Cookies").write_bytes(b"x" * residual)
    (tmp_path / "disk-cache").mkdir()
    (tmp_path / "disk-cache/data").write_bytes(b"xx")
    cw.trim_chrome_profile(tmp_path, threshold_bytes=5)
    assert (tmp_path / "disk-cache").exists() == (residual > 5)


def test_disk_cache_symlink_not_counted_or_followed(tmp_path, caplog):
    profile = tmp_path / "profile"
    profile.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_bytes(b"secret")
    (profile / "disk-cache").symlink_to(outside, target_is_directory=True)
    assert cw._profile_size(profile) == 0
    cw.trim_chrome_profile(profile, threshold_bytes=1)
    assert (outside / "data").read_bytes() == b"secret"
    assert "step=trim reason=symlink" in caplog.text


def test_windows_has_no_legacy_cache():
    from ssu_lecture_downloader.platforms.windows import WindowsPlatform
    assert WindowsPlatform().chrome_legacy_cache_dir() is None


def test_ps_failure_preserves_nonmaintenance_callers(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from ssu_lecture_downloader.platforms import macos
    def fail(*args, **kwargs):
        raise OSError("ps failed")
    monkeypatch.setattr(macos.subprocess, "run", fail)
    h = Harness(tmp_path, [FakePlayer()])
    h.manager._background_launch = True
    h.manager.start(items(1))
    assert h.finish()["state"] == "finished"
    h.wait_thread()
    playback = object.__new__(cw._Playback)
    playback.m = h.manager
    assert playback._process_alive() is False
    waited = []
    h.manager._process_tree.terminate(SimpleNamespace(wait=lambda **kw: waited.append(kw)), chrome_pid=None,
                                    profile_dir=h.profile, background=True, grace=0)
    assert waited == [{"timeout": 5}]


@pytest.mark.parametrize("case", ["symlinked_parent", "unexpected_name"])
def test_legacy_unsafe_path_is_preserved(tmp_path, monkeypatch, caplog, case):
    from types import SimpleNamespace
    h = Harness(tmp_path, [FakePlayer()])
    root = tmp_path / "cache-root"
    root.mkdir()
    if case == "symlinked_parent":
        link = tmp_path / "cache-link"
        link.symlink_to(root, target_is_directory=True)
        legacy = link / "chrome-watch-profile"
        reason = "symlink"
    else:
        legacy = root / "other-profile"
        reason = "unexpected_name"
    legacy.mkdir()
    marker = legacy / "keep"
    marker.write_bytes(b"keep")
    h.manager._app_paths = SimpleNamespace(chrome_legacy_cache_dir=lambda: legacy)
    monkeypatch.setattr(h.manager._process_tree, "is_stopped", lambda profile: True)
    h.manager.start(items(1))
    assert h.finish()["state"] == "finished"
    h.wait_thread()
    assert marker.read_bytes() == b"keep"
    assert f"step=legacy reason={reason}" in caplog.text


def test_trim_measures_profile_only_before_and_after(tmp_path, monkeypatch):
    for name in ("disk-cache", "ShaderCache", "optimization_guide_model_store"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "data").write_bytes(b"123")
    real_size = cw._profile_size
    calls = []

    def measure(path):
        calls.append(path)
        return real_size(path)

    monkeypatch.setattr(cw, "_profile_size", measure)
    cw.trim_chrome_profile(tmp_path, threshold_bytes=3)
    assert calls.count(tmp_path) == 2
    assert all(calls.count(tmp_path / name) == 1 for name in cw.PROFILE_CACHE_DIRS)
    assert not (tmp_path / "disk-cache").exists()
    assert not (tmp_path / "ShaderCache").exists()
    assert (tmp_path / "optimization_guide_model_store/data").read_bytes() == b"123"


def test_stall_reload_and_skip_have_sanitized_ordered_snapshots(tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    launch = 'https://example.test/courses/1?token=fixture-token'
    page_title = 'private-page-title'
    plan = plan_for('1') | {'launch_url': launch}
    harness = Harness(tmp_path, [FakePlayer(stall=True)], plan=lambda *args: plan)
    formatter = cw._Playback.stall_snapshot

    def snapshot(playback, now):
        # Populate diagnostic inputs at the log boundary; playback still uses the fake player.
        playback.sessions = {'S1', 'S2'}
        playback.child_targets = {'S2': 'child-target'}
        playback.frames = {'top': cw._Frame('S1', None), 'child': cw._Frame('S2', 'top')}
        playback.contexts = {('S1', 9): 'top', ('S2', 2): 'child', ('S2', 3): 'child', ('S1', 1): 'top'}
        video = {'time': 0.04, 'duration': 20.06, 'paused': True, 'ended': False, 'main': True, 'ready': 2, 'tag': 'audio'}
        base = {'host': 'top.example', 'top': False, 'videoCount': 2, 'playingCount': 1,
                'playingLong': 1, 'audioCount': 3, 'playingAudio': 2, 'scanCapped': False, 'front': False, 'resume': False, 'video': video,
                'url': launch, 'cookie': 'secret-cookie', 'title': page_title}
        playback.reports = {
            ('S1', 1): (now - 20, base | {'host': 'stale.example'}),
            ('S2', 3): (now, {'host': 'old.example', 'top': True, 'video': {k: v for k, v in video.items() if k != 'ready'}}),
            ('S2', 2): (now, base | {'host': 'child.example', 'top': True}),
            ('S1', 9): (now, base),
            ('S1', 4): (now, base | {'host': launch, 'videoCount': '2', 'playingCount': -1,
                                   'playingLong': True, 'scanCapped': 1, 'front': 'false', 'resume': [],
                                   'video': video | {'time': float('nan'), 'duration': float('inf'), 'paused': 1,
                                                     'ended': None, 'main': 'yes', 'ready': 9, 'tag': ['audio']}}),
            ('S1', 5): (now, {'host': 'none.example', 'video': None}),
        }
        return formatter(playback, now)

    monkeypatch.setattr(cw._Playback, 'stall_snapshot', snapshot)
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['state'] == 'finished' and status['skipped_count'] == 1
    lines = [r.getMessage() for r in caplog.records if 'chrome_watch stall ' in r.getMessage()]
    assert len(lines) == 2
    assert 'action=stalled_reload' in lines[0] and 'action=skip' in lines[1]
    for line in lines:
        assert 'sessions=2 child_targets=1 frames=2 contexts=4 reports=6 fresh=5 stale=1' in line
        assert line.index('[top host=top.example') < line.index('[child host=child.example') < line.index('[child host=old.example') < line.index('[? host=?') < line.index('[? host=none.example') < line.index('[top host=stale.example')
        assert 'host=stale.example age=20.0' in line
        assert 'videos=2 playing_all=1 playing_long=1 audios=3 playing_audio=2 capped=0 front=0 resume=0 video=t=0.0 d=20.1 paused=1 ended=0 main=1 ready=2 tag=audio' in line
        assert 'videos=? playing_all=? playing_long=? audios=? playing_audio=? capped=?' in line
        assert 'video=t=? d=? paused=? ended=? main=? ready=? tag=?' in line
        assert 'video=none' in line and 'ready=?' in line
        assert line.endswith('truncated=0 minimized=0')
        for secret in ('://', launch, '/courses/', '?token=', 'secret-cookie', items(1)[0]['title'], page_title):
            assert secret not in line


def test_stall_snapshot_caps_entries_and_is_total(tmp_path):
    from types import SimpleNamespace
    playback = object.__new__(cw._Playback)
    playback.m = SimpleNamespace(timings=FAST)
    playback.run = SimpleNamespace(auto_minimized=True)
    playback.sessions = set()
    playback.child_targets = {}
    playback.frames = {}
    playback.contexts = {}
    playback.reports = {('S', n): (10, {'host': f'{n}.example', 'video': None}) for n in reversed(range(7))}
    line = playback.stall_snapshot(10)
    assert line.count('[') == 6 and 'host=6.example' not in line
    assert 'reports=7 fresh=7 stale=0' in line and line.endswith('truncated=1 minimized=1')
    playback.reports = {('S', 1): (10, {'host': ['bad'], 'video': ['bad'], 'videoCount': {}, 'playingCount': 1.2})}
    assert 'host=? videos=? playing_all=?' in playback.stall_snapshot(10)
    # Even unexpectedly corrupted driver bookkeeping cannot make the diagnostic throw.
    playback.reports = None
    assert 'reports=?' in playback.stall_snapshot(10)


@pytest.mark.parametrize('stall', [False, True])
def test_diagnostic_fields_do_not_change_playback_decisions(tmp_path, stall):
    results = []
    for added in (False, True):
        player = FakePlayer(stall=stall)
        poll = player.poll

        def report():
            value = poll()
            if added:
                value.update(videoCount=8, playingCount=7, playingLong=6, scanCapped=True)
                if value.get('video'):
                    value['video']['ready'] = 0
            return value

        player.poll = report
        harness = Harness(tmp_path / str(added), [player])
        harness.manager.start(items(1))
        harness.wait_thread()
        status = harness.manager.status()
        bounds = [m['params']['bounds']['windowState'] for m in harness.browser.sent if m['method'] == 'Browser.setWindowBounds']
        results.append((status['state'], status['done_count'], status['skipped_count'], bounds, len(harness.browser.navigations)))
    assert results[0] == results[1]
    assert results[0][1:3] == ((0, 1) if stall else (1, 0))
    assert ('minimized' in results[0][3]) is (not stall)


def _fresh_browser_connections(harness, browsers):
    def connect(url):
        harness.urls.append(url)
        return browsers[len(harness.urls) - 1]
    harness.connect = connect


def _disconnect_first_navigation(browser):
    navigate = browser.navigate

    def disconnect(message, url):
        navigate(message, url)
        browser.close()
    browser.navigate = disconnect


@pytest.mark.parametrize('failure_at', ['play', 'attach', 'exhausted'])
def test_post_recovery_timeout_reconnects_within_budget(tmp_path, monkeypatch, caplog, failure_at):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer()])
    browsers = [harness.browser] + [FakeBrowser([FakePlayer()]) for _ in range(3)]
    _fresh_browser_connections(harness, browsers)
    _disconnect_first_navigation(browsers[0])
    original = CdpClient.call
    failed_clients = []
    target = 'Target.attachToTarget' if failure_at == 'attach' else 'Page.navigate'

    def call(client, method, *args, **kwargs):
        transport = client._transport
        should_fail = transport in (browsers[1:] if failure_at == 'exhausted' else browsers[1:2])
        if should_fail and method == target:
            failed_clients.append(client)
            raise cw.CdpTimeout(method)
        return original(client, method, *args, **kwargs)

    monkeypatch.setattr(CdpClient, 'call', call)
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert all(client.closed for client in failed_clients)
    assert f'attempt=2 cause=cdp_timeout method={target}' in caplog.text
    assert 'attempt=4' not in caplog.text
    if failure_at == 'exhausted':
        assert status['error_code'] == 'chrome_recover_exhausted'
        assert len(harness.urls) == 4 and len(failed_clients) == 3
    else:
        assert status['state'] == 'finished' and status['done_count'] == 1
        assert len(harness.urls) == 3 and len(failed_clients) == 1


@pytest.mark.parametrize('guard', ['first_item', 'next_item', 'cdp_error'])
def test_timeout_reconnect_guards_preserve_existing_failures(tmp_path, monkeypatch, caplog, guard):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer()])
    replacement = FakeBrowser([FakePlayer(), FakePlayer()])
    _fresh_browser_connections(harness, [harness.browser, replacement])
    if guard != 'first_item':
        _disconnect_first_navigation(harness.browser)
    original = CdpClient.call

    def call(client, method, params=None, **kwargs):
        if method == 'Page.navigate':
            fail = guard == 'first_item' or (client._transport is replacement and (guard == 'cdp_error' or params['url'].endswith('/2')))
            if fail:
                if guard == 'cdp_error':
                    raise CdpError(-32000, 'fixture')
                raise cw.CdpTimeout(method)
        return original(client, method, params, **kwargs)

    monkeypatch.setattr(CdpClient, 'call', call)
    harness.manager.start(items(2 if guard == 'next_item' else 1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['error_code'] == ('chrome_watch_error' if guard == 'cdp_error' else 'chrome_closed')
    assert len(harness.urls) == (1 if guard == 'first_item' else 2)
    assert 'cause=cdp_timeout' not in caplog.text
    if guard == 'next_item':
        assert status['done_count'] == 1


@pytest.mark.parametrize('signal', ['navigate_returned', 'reload_event'])
def test_timeout_after_renderer_revival_started_reconnects(tmp_path, monkeypatch, caplog, signal):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer()])
    browser = harness.browser
    replacement = FakeBrowser([FakePlayer()])
    _fresh_browser_connections(harness, [browser, replacement])
    navigate = browser.navigate

    def crash_once(message, url):
        navigate(message, url)
        if len(browser.navigations) == 1:
            browser.emit('Target.targetCrashed', {'targetId': 'T1'})
    browser.navigate = crash_once
    original = CdpClient.call
    failed = False

    def call(client, method, *args, **kwargs):
        nonlocal failed
        if client._transport is browser and not failed:
            if signal == 'reload_event' and method == 'Page.navigate' and browser.crashed_sessions:
                # The command reached Chrome and queued the reload signal before timing out.
                original(client, method, *args, **kwargs)
                failed = True
                raise cw.CdpTimeout(method)
            if signal == 'navigate_returned' and method == 'Target.attachToTarget' and len(browser.navigations) == 2:
                failed = True
                raise cw.CdpTimeout(method)
        return original(client, method, *args, **kwargs)

    monkeypatch.setattr(CdpClient, 'call', call)
    harness.manager.start(items(1))
    harness.wait_thread()
    assert failed and harness.manager.status()['state'] == 'finished'
    assert len(harness.urls) == 2
    method = 'Page.navigate' if signal == 'reload_event' else 'Target.attachToTarget'
    assert f'attempt=2 cause=cdp_timeout method={method}' in caplog.text


def test_advancing_main_audio_completes_without_stalling(tmp_path, caplog):
    player = FakePlayer(duration=200, step=5)
    poll = player.poll
    reports = []

    def report():
        value = poll()
        if value.get('video'):
            value['video']['tag'] = 'audio'
            value.update(audioCount=1, playingAudio=int(not value['video']['ended']))
            reports.append(value)
        return value

    player.poll = report
    harness = Harness(tmp_path, [player], plan=lambda *args: plan_for('1') | {'duration': 200})
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['state'] == 'finished'
    assert status['done_count'] == 1 and status['skipped_count'] == 0
    assert len(harness.browser.navigations) == 1
    assert len({value['video']['time'] for value in reports}) > 2
    assert reports[-1]['video']['ended'] is True
    assert 'stalled_reload' not in caplog.text


def test_advancing_main_audio_completes_via_ended_binding(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    player = FakePlayer(duration=200, step=5)
    poll = player.poll
    reports = []
    signals = []
    harness = Harness(tmp_path, [player], plan=lambda *args: plan_for('1') | {'duration': 200})

    def report():
        value = poll()
        if value.get('video'):
            media = value['video']
            media.update(tag='audio', ended=False)
            reports.append(value)
            if media['time'] >= player.duration and not signals:
                payload = {'kind': 'ended', 'time': media['time'], 'duration': player.duration,
                           'top': True, 'host': 'fixture.example'}
                signals.append(payload)
                harness.browser.emit('Runtime.bindingCalled', {
                    'name': 'ssuWatchSignal', 'payload': json.dumps(payload), 'executionContextId': 1,
                }, PAGE_SESSION)
        return value

    player.poll = report
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['state'] == 'finished'
    assert status['done_count'] == 1 and status['skipped_count'] == 0
    assert status['items'][0]['state'] == 'done'
    assert len({value['video']['time'] for value in reports}) > 2
    assert all(value['video']['tag'] == 'audio' and value['video']['ended'] is False for value in reports)
    assert len(signals) == 1
    assert 'stalled_reload' not in caplog.text


def test_paused_main_audio_after_minimizing_restores_window(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    player = FakePlayer(duration=200, step=5)
    poll = player.poll
    reports = []
    harness = Harness(tmp_path, [player], plan=lambda *args: plan_for('1') | {'duration': 200})

    def report():
        bounds = [message['params']['bounds']['windowState'] for message in harness.browser.sent
                  if message['method'] == 'Browser.setWindowBounds']
        player.stall = bool(bounds and bounds[-1] == 'minimized')
        value = poll()
        if value.get('video'):
            value['video']['tag'] = 'audio'
            reports.append(value)
        return value

    player.poll = report
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    bounds = [message['params']['bounds'] for message in harness.browser.sent
              if message['method'] == 'Browser.setWindowBounds']
    assert bounds == [{'windowState': 'minimized'}, {'windowState': 'normal'}]
    assert any(value['video']['paused'] and value['video']['main'] for value in reports)
    assert len({value['video']['time'] for value in reports}) > 2
    assert status['state'] == 'finished'
    assert status['done_count'] == 1 and status['skipped_count'] == 0
    assert len(harness.browser.navigations) == 1
    assert any(notice['kind'] == 'minimize_paused' for notice in status['notices'])
    assert 'action=minimize_paused' in caplog.text
    assert 'stalled_reload' not in caplog.text


def test_short_ended_binding_waits_for_main_lecture_end(tmp_path):
    player = FakePlayer(duration=3000, step=500)
    poll = player.poll
    reports = []
    signals = []
    harness = Harness(tmp_path, [player], plan=lambda *args: plan_for('1') | {'duration': 3000})

    def report():
        value = poll()
        media = value.get('video')
        if media:
            reports.append(dict(media))
            if not signals:
                payload = {'kind': 'ended', 'time': 30, 'duration': 30,
                           'top': True, 'host': 'fixture.example'}
                signals.append(payload)
                harness.browser.emit('Runtime.bindingCalled', {
                    'name': 'ssuWatchSignal', 'payload': json.dumps(payload), 'executionContextId': 1,
                }, PAGE_SESSION)
        return value

    player.poll = report
    harness.manager.start(items(1))
    harness.wait_thread()
    status = harness.manager.status()
    assert status['state'] == 'finished'
    assert status['done_count'] == 1 and status['skipped_count'] == 0
    assert status['items'][0]['state'] == 'done'
    assert len(signals) == 1
    assert len({media['time'] for media in reports}) > 2
    assert reports[0]['ended'] is False
    assert reports[-1]['time'] == 3000 and reports[-1]['ended'] is True
