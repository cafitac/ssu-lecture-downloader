"""A fake Chrome for the Chrome playback driver: in-memory CDP transport, Popen and clock.

Nothing here starts a real browser or touches the LMS.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import threading
import time
from pathlib import Path
from typing import Any

PAGE_SESSION = "S1"
FRAME_SESSION = "S2"
TOP_FRAME = "F1"
PLAYER_FRAME = "F2"


class FakeClock:
    """Time that moves on every read, so 90 s of stall takes a blink."""

    def __init__(self, step: float = 0.5) -> None:
        self.t = 0.0
        self.step = step
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            self.t += self.step
            return self.t

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.t += seconds
        time.sleep(0.0005)


class FakePlayer:
    """The Commons player's flow: start button -> (resume prompt) -> lecture video."""

    def __init__(self, *, duration: float = 20.0, resume: bool = False, stall: bool = False, in_frame: bool = False, step: float = 5.0) -> None:
        self.duration = duration
        self.resume = resume
        self.stall = stall
        self.in_frame = in_frame
        self.step = step
        self.state = "front"
        self.time = 0.0
        self.clicks: list[str] = []

    def reload(self) -> None:
        self.state = "front"
        self.time = 0.0

    def poll(self) -> dict[str, Any]:
        if self.state == "front":
            return {"kind": "state", "front": True, "resume": False, "video": None}
        if self.state == "resume":
            return {"kind": "state", "front": False, "resume": True, "video": None}
        if not self.stall:
            self.time = min(self.duration, self.time + self.step)
        ended = self.time >= self.duration
        return {
            "kind": "state",
            "front": False,
            "resume": False,
            "video": {"time": self.time, "duration": self.duration, "paused": self.stall, "ended": ended, "main": True},
        }

    def locate(self, what: str) -> dict[str, float] | None:
        if (what == "front" and self.state == "front") or (what == "yes" and self.state == "resume"):
            return {"x": 10.0, "y": 20.0, "w": 100.0, "h": 40.0, "vw": 1000.0, "vh": 700.0}
        return None

    def click(self) -> None:
        self.clicks.append(self.state)
        if self.state == "front":
            self.state = "resume" if self.resume else "main"
        elif self.state == "resume":
            self.state = "main"


class FakeBrowser:
    """Transport + command handler. Players are handed out in order, one per distinct lecture URL."""

    def __init__(self, players: list[FakePlayer]) -> None:
        self.players = list(players)
        self.incoming: "queue.Queue[str | None]" = queue.Queue()
        self.sent: list[dict[str, Any]] = []
        self.current_url: str | None = None
        self.player: FakePlayer | None = None
        self.navigations: list[str] = []
        self.cookies: list[dict[str, Any]] = []
        self.mouse: list[tuple[str | None, str, float, float]] = []
        self.destroyed_page = False
        self.crashed_sessions: set[str] = set()
        self.fail_methods: dict[str, str] = {}
        self._located: str | None = None

    # -- Transport
    def send(self, text: str) -> None:
        message = json.loads(text)
        self.sent.append(message)
        self.handle(message)

    def recv(self) -> str | None:
        return self.incoming.get()

    def close(self) -> None:
        self.incoming.put(None)

    # -- helpers
    def emit(self, method: str, params: dict[str, Any], session: str | None = None) -> None:
        if method == "Inspector.targetCrashed" and session == PAGE_SESSION:
            self.crashed_sessions.add(PAGE_SESSION)
        elif method == "Target.targetCrashed" and params.get("targetId") == "T1":
            self.crashed_sessions.add(PAGE_SESSION)
        message: dict[str, Any] = {"method": method, "params": params}
        if session:
            message["sessionId"] = session
        self.incoming.put(json.dumps(message))

    def reply(self, message: dict[str, Any], result: dict[str, Any] | None = None) -> None:
        self.incoming.put(json.dumps({"id": message["id"], "result": result or {}}))

    def methods(self) -> list[str]:
        return [message["method"] for message in self.sent]

    def close_page(self) -> None:
        self.emit("Target.targetDestroyed", {"targetId": "T1"})

    # -- command handling
    def handle(self, message: dict[str, Any]) -> None:
        method = message["method"]
        params = message.get("params") or {}
        session = message.get("sessionId")
        if method in {"Page.enable", "Runtime.enable"} and session in self.crashed_sessions:
            return  # Real Chrome never replies until Page.navigate revives the target.
        if method == "Inspector.enable" and session in self.crashed_sessions:
            self.emit("Inspector.targetCrashed", {}, session)
        if method == "Page.navigate":
            if session in self.crashed_sessions:
                self.emit("Inspector.targetReloadedAfterCrash", {}, session)
            self.crashed_sessions.clear()
        if method in self.fail_methods:
            self.incoming.put(json.dumps({"id": message["id"], "error": {"code": -32000, "message": self.fail_methods[method]}}))
            return
        if method == "Target.getTargets":
            return self.reply(message, {"targetInfos": [{"targetId": "T1", "type": "page", "url": "about:blank"}]})
        if method == "Target.attachToTarget":
            return self.reply(message, {"sessionId": PAGE_SESSION})
        if method == "Page.getFrameTree":
            if session == FRAME_SESSION:
                return self.reply(message, {"frameTree": {"frame": {"id": PLAYER_FRAME, "parentId": TOP_FRAME}}})
            return self.reply(message, {"frameTree": {"frame": {"id": TOP_FRAME}}})
        if method == "Runtime.enable" and session == FRAME_SESSION:
            self.reply(message)
            self.emit("Runtime.executionContextCreated", {"context": {"id": 2, "auxData": {"isDefault": True, "frameId": PLAYER_FRAME}}}, FRAME_SESSION)
            return
        if method == "Page.navigate":
            return self.navigate(message, params["url"])
        if method == "Network.setCookie":
            self.cookies.append(params)
            return self.reply(message, {"success": True})
        if method == "Runtime.evaluate":
            return self.evaluate(message, params, session)
        if method == "DOM.getFrameOwner":
            return self.reply(message, {"backendNodeId": 7})
        if method == "DOM.getBoxModel":
            return self.reply(message, {"model": {"content": [200.0, 100.0, 900.0, 100.0, 900.0, 600.0, 200.0, 600.0]}})
        if method == "Input.dispatchMouseEvent":
            self.mouse.append((session, params["type"], params["x"], params["y"]))
            if params["type"] == "mousePressed" and self.player is not None:
                self.player.click()
            return self.reply(message)
        if method == "Browser.getWindowForTarget":
            return self.reply(message, {"windowId": 3})
        return self.reply(message)

    def navigate(self, message: dict[str, Any], url: str) -> None:
        self.navigations.append(url)
        if url == self.current_url and self.player is not None:
            self.player.reload()
        else:
            self.current_url = url
            self.player = self.players.pop(0)
        self.emit("Page.frameNavigated", {"frame": {"id": TOP_FRAME, "url": url}}, PAGE_SESSION)
        self.emit("Runtime.executionContextCreated", {"context": {"id": 1, "auxData": {"isDefault": True, "frameId": TOP_FRAME}}}, PAGE_SESSION)
        if self.player.in_frame:
            self.emit("Page.frameAttached", {"frameId": PLAYER_FRAME, "parentFrameId": TOP_FRAME}, PAGE_SESSION)
            self.emit("Target.attachedToTarget", {"sessionId": FRAME_SESSION, "targetInfo": {"targetId": PLAYER_FRAME, "type": "iframe"}, "waitingForDebugger": False}, PAGE_SESSION)
        self.reply(message, {"frameId": TOP_FRAME, "loaderId": "L1"})

    def evaluate(self, message: dict[str, Any], params: dict[str, Any], session: str | None) -> None:
        expression = params.get("expression", "")
        context = params.get("contextId")
        player = self.player
        in_player_context = player is not None and ((player.in_frame and session == FRAME_SESSION and context == 2) or (not player.in_frame and session == PAGE_SESSION and context == 1))
        if "__ssuWatchState" in expression and "window.__ssuWatchState ?" in expression:
            value = player.poll() if in_player_context and player is not None else {"kind": "state", "front": False, "resume": False, "video": None}
            return self.reply(message, {"result": {"value": value}})
        if "__ssuWatchLocate" in expression:
            what = "yes" if '"yes"' in expression else "front"
            value = player.locate(what) if in_player_context and player is not None else None
            return self.reply(message, {"result": {"value": value}})
        return self.reply(message, {"result": {}})


class FakePopen:
    instances: list["FakePopen"] = []

    def __init__(self, args: list[str], **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.pid = 4242
        self.returncode: int | None = None
        profile = next(arg.split("=", 1)[1] for arg in args if arg.startswith("--user-data-dir="))
        if not FakePopen.silent:
            Path(profile, "DevToolsActivePort").write_text("9222\n/devtools/browser/abc\n", encoding="utf-8")
        FakePopen.instances.append(self)

    silent = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode


class KillRecorder:
    def __init__(self, popen_list: list[FakePopen], *, ignore_term: bool = False) -> None:
        self.calls: list[tuple[int, int]] = []
        self.popen_list = popen_list
        self.ignore_term = ignore_term

    def __call__(self, pgid: int, sig: int) -> None:
        self.calls.append((pgid, sig))
        for proc in self.popen_list:
            if proc.pid == pgid and (sig == signal.SIGKILL or not self.ignore_term):
                proc.returncode = -sig


def make_chrome_binary(tmp_path: Path) -> Path:
    chrome = tmp_path / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"
    chrome.parent.mkdir(parents=True)
    chrome.write_text("#!/bin/sh\n", encoding="utf-8")
    os.chmod(chrome, 0o755)
    return chrome
