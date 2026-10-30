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
        assert not any(secret in message.lower() for secret in ("cookie", "token", "http", "://", "?", "1주차", "UX방법론"))


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


def test_a_dropped_connection_ends_the_run(tmp_path, caplog):
    caplog.set_level(logging.INFO, logger=cw.logger.name)
    harness = Harness(tmp_path, [FakePlayer(stall=True)])
    harness.manager.start(items(1))
    harness.wait(lambda s: len(harness.browser.navigations) >= 1)
    harness.browser.incoming.put(None)
    status = harness.finish()
    assert status["state"] == "failed" and status["error_code"] == "chrome_closed"
    harness.wait_thread()
    assert "chrome_watch cdp_disconnected run_id=" in caplog.text
    assert "reason=CdpClosed" in caplog.text


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
        assert "chrome_watch window_closed run_id=" in caplog.text
    else:
        record = next(r for r in caplog.records if "chrome_watch worker_error run_id=" in r.getMessage())
        assert record.exc_info is not None
        assert status["error_code"] == "chrome_watch_error"
