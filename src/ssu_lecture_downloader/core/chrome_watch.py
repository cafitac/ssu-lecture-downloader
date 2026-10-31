"""Auto playback of a lecture queue in a dedicated Google Chrome window, driven over CDP.

The in-app web view only plays while its small window stays visible. A real Chrome window keeps
playing while other tabs or other apps are in front, so this module launches a separate Chrome
instance (own persistent profile, never the user's default one), opens each lecture in a tab with
the LMS session cookies, presses the player's buttons with trusted mouse events and watches the
main video until it ends.

Rules this module keeps:
- Nothing fakes visibility or speed: no visibility overrides, no backgrounding flags, no
  `video.play()` on the intro, no playback-rate change, no seeking. The player runs its own flow
  (start button, resume prompt, short intro, lecture); the page helper only observes; audio is silenced with --mute-audio.
- A window that is minimized or covered is not treated specially: if the lecture time really
  stops for `stall_after` seconds the item is reloaded once and then skipped, and the status
  and notices say so (with a hint to bring the window back) instead of failing silently.
- One queue at a time; the Chrome process group is killed on stop, finish, failure and backend
  exit. The profile directory is kept.
"""

from __future__ import annotations

import atexit
import json
import logging
import os
import queue
import re
import shutil
import threading
import time
import uuid
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ssu_lecture_downloader.core.chrome_cdp import CdpClient, CdpClosed, CdpError, CdpTimeout, Transport, WebSocketTransport
from ssu_lecture_downloader.core.chrome_watch_helper import BINDING_NAME, HELPER_JS

logger = logging.getLogger("ssu_lecture_downloader.chrome_watch")

MAX_QUEUE_ITEMS = 200
WINDOW_SIZE = (1000, 700)
PROFILE_TRIM_THRESHOLD_BYTES = 200 * 1024 * 1024
PROFILE_CACHE_DIRS = (
    "disk-cache", "GraphiteDawnCache", "GrShaderCache", "ShaderCache", "Default/Cache",
    "Default/Code Cache", "Default/GPUCache", "Default/DawnGraphiteCache", "Default/DawnWebGPUCache",
    "BrowserMetrics", "optimization_guide_model_store", "component_crx_cache", "WasmTtsEngine",
    "OnDeviceHeadSuggestModel",
)

STALL_SKIP_REASON = "재생이 멈춰 건너뛰었어요"
USER_SKIP_REASON = "직접 넘겼어요"
MINIMIZED_HINT = "Chrome 창이 최소화돼 있으면 다시 열어 주세요."

PHASE_OPENING = "opening"
PHASE_PLAYING = "playing"
PHASE_STALLED = "stalled"
MINIMIZE_CHECK_SECONDS = 10.0
MINIMIZE_FALLBACK_NOTICE = "최소화하면 영상이 멈춰서, Chrome 창을 다른 창 뒤에 그대로 두고 재생해요."


class ChromeWatchError(Exception):
    """A failure with a stable error code (the status reports it as `error_code`)."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


class ChromeWatchBusy(ChromeWatchError):
    def __init__(self) -> None:
        super().__init__("chrome_watch_busy", "이미 자동 재생이 진행 중이에요.")


class ChromeWatchPlanError(Exception):
    """The lecture plan could not be fetched (`auth`: the LMS login is gone)."""

    def __init__(self, code: str, *, auth: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.auth = auth


PlanProvider = Callable[[str, str, str], dict[str, Any]]


@dataclass(frozen=True)
class Timings:
    port_timeout: float = 30.0
    port_poll: float = 0.2
    tick: float = 0.25
    poll_interval: float = 2.0
    stall_after: float = 90.0
    front_click_every: float = 5.0
    resume_click_every: float = 2.5
    resume_max_clicks: int = 6
    terminate_grace: float = 5.0
    reconnect_backoff: float = 0.5
    call_timeout: float = 15.0
    stop_join: float = 15.0


# --------------------------------------------------------------------- launch helpers


def build_chrome_args(chrome: Path | str, profile_dir: Path | str, *, window_size: tuple[int, int] = WINDOW_SIZE) -> list[str]:
    """Command line of the dedicated instance: own profile, DevTools on a free port, no prompts.

    Deliberately no flags that change how Chrome treats hidden or covered windows.
    """
    return [
        str(chrome),
        f"--user-data-dir={profile_dir}",
        f"--disk-cache-dir={Path(profile_dir) / 'disk-cache'}",
        "--disk-cache-size=104857600",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        # Silence at the browser's audio output (the page still plays normally).
        "--mute-audio",
        f"--window-size={window_size[0]},{window_size[1]}",
        "--new-window",
        "about:blank",
    ]


def _profile_size(profile_dir: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(profile_dir, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        for name in files:
            path = Path(root) / name
            try:
                if not path.is_symlink():
                    total += path.stat().st_size
            except OSError:
                continue
    return total


def _maintenance_warning(step: str, reason: str) -> None:
    logger.warning("chrome_watch maintenance_skipped step=%s reason=%s", step, reason)


def trim_chrome_profile(profile_dir: Path, *, threshold_bytes: int = PROFILE_TRIM_THRESHOLD_BYTES) -> None:
    """Best-effort cleanup of regenerable directories; caller must check Chrome is stopped."""
    if profile_dir.is_symlink():
        _maintenance_warning("trim", "symlink")
        return
    for relative in PROFILE_CACHE_DIRS:
        path = profile_dir / relative
        if path.is_symlink() or path.parent.is_symlink():
            _maintenance_warning("trim", "symlink")
    before = _profile_size(profile_dir)
    if before <= threshold_bytes:
        return
    candidates = [profile_dir / relative for relative in PROFILE_CACHE_DIRS
                  if not (profile_dir / relative).is_symlink()
                  and not (profile_dir / relative).parent.is_symlink()]
    allowlisted = sum(_profile_size(path) for path in candidates)
    residual = before - allowlisted
    if residual > threshold_bytes:
        _maintenance_warning("trim", "residual_over_threshold")
        logger.warning(
            "chrome_watch profile_trim_skipped over_mb=%.2f reason=residual_over_threshold",
            (residual - threshold_bytes) / (1024 * 1024),
        )
        return
    removed = 0
    for relative in PROFILE_CACHE_DIRS:
        if _profile_size(profile_dir) <= threshold_bytes:
            break
        path = profile_dir / relative
        try:
            # In particular, Default itself must not redirect us outside the profile.
            if path.is_symlink() or path.parent.is_symlink():
                continue
            if path.is_dir():
                shutil.rmtree(path)
                removed += 1
        except OSError:
            _maintenance_warning("trim", "removal_failed")
    after = _profile_size(profile_dir)
    if after > threshold_bytes:
        logger.warning("chrome_watch profile_trim after_mb=%.2f reason=still_over_threshold", after / (1024 * 1024))
    logger.info(
        "chrome_watch profile_trim before_mb=%.2f after_mb=%.2f removed=%s",
        before / (1024 * 1024), after / (1024 * 1024), removed,
    )


def parse_devtools_active_port(text: str) -> tuple[int, str] | None:
    """(port, browser websocket path) from the `DevToolsActivePort` file, or None while incomplete."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or not lines[0].isdigit():
        return None
    port = int(lines[0])
    path = lines[1]
    if not (0 < port < 65536) or not path.startswith("/"):
        return None
    return port, path


def wait_for_devtools_port(
    profile_dir: Path,
    *,
    timeout: float,
    poll: float,
    alive: Callable[[], bool],
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, str]:
    started = clock()
    port_file = profile_dir / "DevToolsActivePort"
    while True:
        try:
            parsed = parse_devtools_active_port(port_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            parsed = None
        if parsed is not None:
            return parsed
        if not alive():
            raise ChromeWatchError("chrome_exited", "Chrome이 바로 종료됐어요.")
        if clock() - started >= timeout:
            raise ChromeWatchError("chrome_launch_timeout", "Chrome 창이 열리지 않았어요.")
        sleep(poll)


def map_same_site(value: object, secure: bool) -> str | None:
    """Bridge cookie `same_site` text -> CDP `sameSite` (None: leave it unset)."""
    if not isinstance(value, str):
        return None
    mapped = {"lax": "Lax", "strict": "Strict", "none": "None", "no_restriction": "None"}.get(value.strip().lower())
    if mapped == "None" and not secure:
        return None  # SameSite=None is only valid together with Secure
    return mapped


def cookie_params(cookie: dict[str, Any]) -> dict[str, Any] | None:
    """`Network.setCookie` parameters for one bridge cookie, or None when it is unusable."""
    name = cookie.get("name")
    value = cookie.get("value")
    domain = cookie.get("domain")
    if not isinstance(name, str) or not name or not isinstance(value, str) or not isinstance(domain, str) or not domain:
        return None
    secure = bool(cookie.get("secure"))
    params: dict[str, Any] = {
        "name": name,
        "value": value,
        "domain": domain,
        "path": cookie.get("path") if isinstance(cookie.get("path"), str) and cookie.get("path") else "/",
        "secure": secure,
        "httpOnly": bool(cookie.get("http_only")),
    }
    same_site = map_same_site(cookie.get("same_site"), secure)
    if same_site:
        params["sameSite"] = same_site
    return params


# ----------------------------------------------------------------------------- queue


@dataclass
class WatchItem:
    course_id: str
    course_name: str
    module_item_id: str
    title: str
    duration: float | None = None
    state: str = "pending"  # pending | playing | done | skipped
    reason: str | None = None


@dataclass
class _Run:
    run_id: str
    items: list[WatchItem]
    state: str = "starting"  # starting | playing | finished | stopped | failed
    index: int = 0
    phase: str = PHASE_OPENING
    position: float | None = None
    duration: float | None = None
    paused: bool = False
    error_code: str | None = None
    error_message: str | None = None
    notices: list[dict[str, Any]] = field(default_factory=list)
    next_notice_id: int = 1
    stop: threading.Event = field(default_factory=threading.Event)
    skip: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    client: CdpClient | None = None
    page_target: str | None = None
    # Minimize the Chrome window when each lecture really starts playing, so nobody has to look
    # at it (moving to the next lecture can bring the window back, e.g. for a Leave-site prompt).
    # Once the user asks to see the window, it stays where they put it for the rest of the run.
    auto_minimize: bool = True
    auto_minimized: bool = False

    @property
    def active(self) -> bool:
        return self.state in {"starting", "playing"}


class _Stopped(Exception):
    pass


class _RendererGone(Exception):
    pass


class _WindowClosed(Exception):
    pass


@dataclass
class _Frame:
    session: str
    parent: str | None


def _clean_items(raw: list[dict[str, Any]]) -> list[WatchItem]:
    seen: set[tuple[str, str]] = set()
    items: list[WatchItem] = []
    for entry in raw:
        course_id = str(entry.get("course_id", "")).strip()
        module_item_id = str(entry.get("module_item_id", "")).strip()
        if not course_id or not module_item_id or (course_id, module_item_id) in seen:
            continue
        seen.add((course_id, module_item_id))
        if len(items) >= MAX_QUEUE_ITEMS:
            break
        duration = entry.get("duration")
        title = str(entry.get("title") or "").strip() or "강의"
        items.append(
            WatchItem(
                course_id=course_id,
                course_name=str(entry.get("course_name") or "").strip(),
                module_item_id=module_item_id,
                title=title,
                duration=float(duration) if isinstance(duration, (int, float)) and duration > 0 else None,
            )
        )
    return items


_LIVE: "weakref.WeakSet[ChromeWatchManager]" = weakref.WeakSet()


def _shutdown_all() -> None:
    for manager in list(_LIVE):
        manager.shutdown()


atexit.register(_shutdown_all)


class ChromeWatchManager:
    def __init__(
        self,
        plan_provider: PlanProvider,
        *,
        chrome_locator: Any,
        process_tree: Any,
        app_paths: Any | None = None,
        profile_dir: Path | None = None,
        connect: Callable[[str], Transport] | None = None,
        background_launch: bool = True,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timings: Timings | None = None,
    ) -> None:
        self._plan_provider = plan_provider
        # Injected by the composition root (core never imports the platform adapters):
        # a ChromeLocator, a ProcessTree and (unless profile_dir is given) an AppPaths.
        self._chrome_locator = chrome_locator
        self._process_tree = process_tree
        self._app_paths = app_paths
        self._profile_dir = profile_dir
        self._connect = connect or WebSocketTransport
        self._background_launch = background_launch
        self._chrome_pid: int | None = None
        self._clock = clock
        self._sleep = sleep
        self.timings = timings or Timings()
        self._lock = threading.RLock()
        self._run: _Run | None = None
        _LIVE.add(self)

    # ------------------------------------------------------------------ public API

    @property
    def profile_dir(self) -> Path:
        if self._profile_dir is not None:
            return self._profile_dir
        if self._app_paths is None:
            raise ValueError("ChromeWatchManager needs profile_dir or app_paths")
        return self._app_paths.chrome_profile_dir()

    def chrome_path(self) -> Path | None:
        return self._chrome_locator.find_chrome()

    def start(self, raw_items: list[dict[str, Any]]) -> dict[str, Any]:
        items = _clean_items(raw_items)
        if not items:
            raise ChromeWatchError("empty_watch_queue", "자동 재생할 강의가 없어요.")
        if self.chrome_path() is None:
            raise ChromeWatchError("chrome_not_found", "Google Chrome을 찾지 못했어요.")
        with self._lock:
            previous = self._run
            if previous is not None and previous.active:
                raise ChromeWatchBusy()
        if previous is not None and previous.thread is not None and previous.thread.is_alive():
            # The last run is still closing its Chrome; the pid file and profile are shared.
            previous.thread.join(self.timings.stop_join)
            if previous.thread.is_alive():
                raise ChromeWatchBusy()
        with self._lock:
            if self._run is not None and self._run.active:
                raise ChromeWatchBusy()
            run = _Run(run_id=uuid.uuid4().hex[:12], items=items)
            self._run = run
            run.thread = threading.Thread(target=self._work, args=(run,), name="chrome-watch", daemon=True)
            run.thread.start()
            return self._status_locked()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._status_locked()

    def next(self) -> dict[str, Any]:
        with self._lock:
            run = self._run
            if run is not None and run.active:
                run.skip.set()
            return self._status_locked()

    def stop(self) -> dict[str, Any]:
        with self._lock:
            run = self._run
            thread = run.thread if run is not None and run.active else None
            if run is not None and run.active:
                logger.info("chrome_watch user_stop run_id=%s", run.run_id)
                run.stop.set()
        if thread is not None:
            thread.join(self.timings.stop_join)
            with self._lock:
                if run is not None and run.active:
                    run.state = "stopped"
        return self.status()

    def show(self) -> dict[str, Any]:
        """Bring the Chrome window back (un-minimize and raise it)."""
        with self._lock:
            run = self._run
            client = run.client if run is not None and run.active else None
            target = run.page_target if run is not None else None
        if client is None or target is None:
            raise ChromeWatchError("chrome_window_missing", "Chrome 창이 열려 있지 않아요.")
        with self._lock:
            run.auto_minimize = False  # the user wants to see it
        try:
            window = client.call("Browser.getWindowForTarget", {"targetId": target}, timeout=self.timings.call_timeout)
            if "windowId" in window:
                client.call("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "normal"}}, timeout=self.timings.call_timeout)
            client.call("Target.activateTarget", {"targetId": target}, timeout=self.timings.call_timeout)
        except (CdpError, CdpClosed, CdpTimeout) as exc:
            raise ChromeWatchError("chrome_window_missing", "Chrome 창을 보여주지 못했어요.") from exc
        return self.status()

    def shutdown(self) -> None:
        """Stop a running queue and make sure no Chrome process is left (backend exit)."""
        try:
            self.stop()
            with self._lock:
                thread = self._run.thread if self._run is not None else None
            if thread is not None and thread.is_alive():
                thread.join(self.timings.stop_join)  # a finished run may still be closing its Chrome
        except Exception:  # noqa: BLE001 - exit path
            logger.exception("chrome_watch shutdown_error")
        self._kill_stale()

    # ---------------------------------------------------------------------- status

    def _status_locked(self) -> dict[str, Any]:
        run = self._run
        available = self.chrome_path() is not None
        if run is None:
            return {
                "ok": True,
                "state": "idle",
                "run_id": None,
                "active": False,
                "chrome_available": available,
                "index": 0,
                "total": 0,
                "done_count": 0,
                "skipped_count": 0,
                "current": None,
                "items": [],
                "error_code": None,
                "error_message": None,
                "notices": [],
            }
        done = sum(1 for item in run.items if item.state == "done")
        skipped = sum(1 for item in run.items if item.state == "skipped")
        current = None
        if run.active and 0 <= run.index < len(run.items):
            item = run.items[run.index]
            current = {
                "module_item_id": item.module_item_id,
                "course_id": item.course_id,
                "course_name": item.course_name,
                "title": item.title,
                "phase": run.phase,
                "position": run.position,
                "duration": run.duration if run.duration is not None else item.duration,
                "paused": run.paused,
            }
        return {
            "ok": True,
            "state": run.state,
            "run_id": run.run_id,
            "active": run.active,
            "chrome_available": available,
            "index": min(run.index, len(run.items)),
            "total": len(run.items),
            "done_count": done,
            "skipped_count": skipped,
            "current": current,
            "items": [
                {
                    "module_item_id": item.module_item_id,
                    "course_id": item.course_id,
                    "course_name": item.course_name,
                    "title": item.title,
                    "state": item.state,
                    "reason": item.reason,
                    "duration": item.duration,
                }
                for item in run.items
            ],
            "error_code": run.error_code,
            "error_message": run.error_message,
            "notices": list(run.notices),
        }

    def _notice(self, run: _Run, kind: str, level: str, text: str, *, item_title: str | None = None, reason: str | None = None) -> None:
        with self._lock:
            run.notices.append({"id": run.next_notice_id, "kind": kind, "level": level, "text": text, "item_title": item_title, "reason": reason})
            run.next_notice_id += 1
            del run.notices[:-50]

    # ----------------------------------------------------------------------- worker

    def _work(self, run: _Run) -> None:
        proc: Any = None
        playback: _Playback | None = None
        client: CdpClient | None = None
        logger.info("chrome_watch run_start run_id=%s item_count=%s background=%s", run.run_id, len(run.items), self._background_launch)
        try:
            proc, client, events = self._launch(run)
            playback = _Playback(self, run, client, events, proc)
            playback.attach_page()
            with self._lock:
                run.client = client
                run.state = "playing"
            while run.index < len(run.items):
                if run.stop.is_set():
                    raise _Stopped()
                playback.play(run.index)
                with self._lock:
                    run.index += 1
            done = sum(1 for item in run.items if item.state == "done")
            skipped = sum(1 for item in run.items if item.state == "skipped")
            tail = f" · {skipped}개 건너뜀" if skipped else ""
            with self._lock:
                run.state = "finished"
                run.phase = PHASE_PLAYING
            self._notice(run, "finished", "success", f"자동 재생을 마쳤어요. {done}개 시청{tail}")
        except _Stopped:
            with self._lock:
                run.state = "stopped"
        except _WindowClosed:
            logger.info("chrome_watch window_closed run_id=%s", run.run_id)
            self._fail(run, "chrome_window_closed", "Chrome 창이 닫혀 자동 재생을 멈췄어요.")
        except ChromeWatchError as exc:
            logger.info("chrome_watch run_failed run_id=%s error_code=%s", run.run_id, exc.code or type(exc).__name__)
            self._fail(run, exc.code, exc.message)
        except (CdpClosed, CdpTimeout) as exc:
            logger.info("chrome_watch cdp_disconnected run_id=%s reason=%s expected=%s", run.run_id, type(exc).__name__, str(run.stop.is_set()).lower())
            if run.stop.is_set():
                with self._lock:
                    run.state = "stopped"
            else:
                self._fail(run, "chrome_closed", "Chrome과의 연결이 끊겨 자동 재생을 멈췄어요.")
        except Exception:  # noqa: BLE001 - the worker must always leave a terminal state
            logger.exception("chrome_watch worker_error run_id=%s", run.run_id)
            self._fail(run, "chrome_watch_error", "자동 재생 중 예상하지 못한 오류가 생겼어요.")
        finally:
            with self._lock:
                run.client = None
            if playback is not None:
                playback.client.close()
            if client is not None:
                client.close()
            if proc is not None:
                self._terminate(proc)
                logger.info("chrome_watch chrome_stop run_id=%s exit_code=%s", run.run_id, proc.poll())
            logger.info(
                "chrome_watch run_end run_id=%s state=%s phase=%s error_code=%s done_count=%s skipped_count=%s",
                run.run_id, run.state, run.phase, run.error_code or "-", sum(item.state == "done" for item in run.items),
                sum(item.state == "skipped" for item in run.items),
            )

    def _fail(self, run: _Run, code: str, message: str, *, level: str = "error") -> None:
        if run.stop.is_set():
            with self._lock:
                run.state = "stopped"
            return
        with self._lock:
            run.state = "failed"
            run.error_code = code
            run.error_message = message
        self._notice(run, "failed", level, message)

    # ----------------------------------------------------------------------- launch

    def _launch(self, run: _Run) -> tuple[Any, CdpClient, "queue.Queue[tuple[str, dict[str, Any], str | None]]"]:
        chrome = self.chrome_path()
        if chrome is None:
            raise ChromeWatchError("chrome_not_found", "Google Chrome을 찾지 못했어요.")
        profile = self.profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        self._kill_stale()
        try:
            stopped = self._process_tree.is_stopped(profile)
        except Exception:
            stopped = None
        if stopped is True:
            try:
                if self._app_paths is not None:
                    legacy = self._app_paths.chrome_legacy_cache_dir()
                    if legacy is not None:
                        resolved, live = legacy.resolve(), profile.resolve()
                        if legacy.is_symlink() or any(p.is_symlink() for p in legacy.parents):
                            _maintenance_warning("legacy", "symlink")
                        elif resolved == live or resolved in live.parents or live in resolved.parents:
                            _maintenance_warning("legacy", "profile_overlap")
                        elif legacy.is_dir():
                            shutil.rmtree(legacy)
            except Exception:
                _maintenance_warning("legacy", "removal_failed")
            try:
                trim_chrome_profile(profile)
            except Exception:
                _maintenance_warning("trim", "failed")
        else:
            for step in ("legacy", "trim"):
                _maintenance_warning(step, "process_unknown" if stopped is None else "process_running")
        try:
            (profile / "DevToolsActivePort").unlink()
        except FileNotFoundError:
            pass
        try:
            args = build_chrome_args(chrome, profile)
            proc = self._process_tree.spawn(chrome, args, background=self._background_launch)
        except OSError as exc:
            raise ChromeWatchError("chrome_launch_failed", "Chrome을 실행하지 못했어요.") from exc
        self._chrome_pid = None if self._background_launch else proc.pid
        if self._chrome_pid is not None:
            self._write_pidfile(self._chrome_pid)
        try:
            port, path = wait_for_devtools_port(
                profile,
                timeout=self.timings.port_timeout,
                poll=self.timings.port_poll,
                alive=lambda: proc.poll() is None and not run.stop.is_set(),
                clock=self._clock,
                sleep=self._sleep,
            )
        except ChromeWatchError as exc:
            if exc.code == "chrome_exited":
                logger.info("chrome_watch chrome_exit run_id=%s exit_code=%s expected=%s", run.run_id, proc.poll(), str(run.stop.is_set()).lower())
            self._terminate(proc)
            if run.stop.is_set():
                raise _Stopped() from exc
            raise
        if self._background_launch:
            self._chrome_pid = self._process_tree.find_main_pid(profile)
            if self._chrome_pid is not None:
                self._write_pidfile(self._chrome_pid)
        logger.info("chrome_watch chrome_launch run_id=%s pid=%s pid_found=%s", run.run_id, self._chrome_pid, self._chrome_pid is not None)
        try:
            client, events = self._connect_client(run, port, path)
        except Exception:
            self._terminate(proc)
            raise
        return proc, client, events

    def _connect_client(self, run: _Run, port: int, path: str, *, recovering: bool = False) -> tuple[CdpClient, "queue.Queue[tuple[str, dict[str, Any], str | None]]"]:
        events: "queue.Queue[tuple[str, dict[str, Any], str | None]]" = queue.Queue()
        try:
            transport = self._connect(f"ws://127.0.0.1:{port}{path}")
        except Exception as exc:
            raise ChromeWatchError("chrome_connect_failed", "Chrome에 연결하지 못했어요.") from exc
        holder: list[CdpClient] = []

        def on_event(method: str, params: dict[str, Any], session: str | None) -> None:
            # A page that asks "Leave site?" blocks the app's own navigation to the next lecture
            # (Page.navigate would wait for it), so answer it right here on the reader thread.
            if method == "Page.javascriptDialogOpening" and params.get("type") == "beforeunload" and holder:
                holder[0].send_nowait("Page.handleJavaScriptDialog", {"accept": True}, session_id=session)
            events.put((method, params, session))

        client = CdpClient(transport, on_event, default_timeout=self.timings.call_timeout)
        holder.append(client)
        try:
            version = client.call("Browser.getVersion")
        except CdpError:
            if recovering:
                client.close()
                raise
            version = {}
        except (CdpClosed, CdpTimeout):
            client.close()
            raise
        logger.info("chrome_watch cdp_connected run_id=%s chrome=%s", run.run_id, version.get("product", "unknown"))
        return client, events

    def _pidfile(self) -> Path:
        return self.profile_dir.parent / "chrome-watch.pid"

    def _write_pidfile(self, pid: int) -> None:
        try:
            self._pidfile().write_text(str(pid), encoding="utf-8")
        except OSError:
            pass

    def _terminate(self, proc: Any) -> None:
        """Stop the Chrome process tree (the platform's ProcessTree decides how). The profile stays."""
        self._process_tree.terminate(
            proc,
            chrome_pid=self._chrome_pid,
            profile_dir=self.profile_dir,
            background=self._background_launch,
            grace=self.timings.terminate_grace,
        )
        if self._background_launch:
            self._chrome_pid = None
        try:
            self._pidfile().unlink()
        except OSError:
            pass

    def _kill_stale(self) -> None:
        """A Chrome of an earlier backend run that is still around (its pid file names it)."""
        pidfile = self._pidfile()
        try:
            pid = int(pidfile.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return
        self._process_tree.kill_stale(pid, self.profile_dir, self.timings.terminate_grace)
        try:
            pidfile.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------------- playback


class _Playback:
    """The CDP side of one run: sessions, frames, page reports, clicks and the per-lecture loop."""

    def __init__(self, manager: ChromeWatchManager, run: _Run, client: CdpClient, events: "queue.Queue[tuple[str, dict[str, Any], str | None]]", proc: Any) -> None:
        self.m = manager
        self.run = run
        self.client = client
        self.events = events
        self.proc = proc
        self.page_session: str | None = None
        self.page_target: str | None = None
        self.sessions: set[str] = set()
        self.frames: dict[str, _Frame] = {}
        self.session_root: dict[str, str] = {}
        self.contexts: dict[tuple[str, int], str] = {}
        self.reports: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
        self.accepting = False
        self.ended = False
        self.window_closed = False
        self.connection_closed = False
        self.reviving_session: str | None = None
        self.revival_started = False
        self.renderer_gone = False
        self.crash_details: dict[str, Any] = {}
        self.crash_logged = False
        self.child_targets: dict[str, str] = {}
        self.expected_duration: float | None = None

    def restore_window(self) -> None:
        """Un-minimize without bringing Chrome to the front."""
        if not self.page_target:
            return
        logger.info("chrome_watch window run_id=%s action=restore", self.run.run_id)
        try:
            window = self.call("Browser.getWindowForTarget", {"targetId": self.page_target})
            if "windowId" in window:
                self.call("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "normal"}})
        except (CdpError, CdpClosed, CdpTimeout):
            pass

    def minimize_window(self) -> None:
        if not self.page_target:
            return
        logger.info("chrome_watch window run_id=%s action=minimize", self.run.run_id)
        try:
            window = self.call("Browser.getWindowForTarget", {"targetId": self.page_target})
            if "windowId" in window:
                self.call("Browser.setWindowBounds", {"windowId": window["windowId"], "bounds": {"windowState": "minimized"}})
        except (CdpError, CdpClosed, CdpTimeout):
            pass

    # ---------------------------------------------------------------------- setup

    def call(self, method: str, params: dict[str, Any] | None = None, session: str | None = None, timeout: float | None = None) -> dict[str, Any]:
        return self.client.call(method, params, session_id=session, timeout=timeout)

    def attach_page(self) -> None:
        self.call("Target.setDiscoverTargets", {"discover": True})
        targets = self.call("Target.getTargets").get("targetInfos", [])
        page = next((t for t in targets if t.get("targetId") == self.page_target), None)
        if self.page_target is None:
            page = next((t for t in targets if t.get("type") == "page" and not str(t.get("url", "")).startswith("chrome-extension://")), None)
        if page is None:
            target_id = self.call("Target.createTarget", {"url": "about:blank", "newWindow": True})["targetId"]
        else:
            target_id = page["targetId"]
        session = self.call("Target.attachToTarget", {"targetId": target_id, "flatten": True})["sessionId"]
        self.page_session = session
        self.page_target = target_id
        with self.m._lock:
            self.run.page_target = target_id
        if not self.renderer_gone:
            self.setup_session(session, is_page=True)

    def setup_session(self, session: str, *, is_page: bool) -> None:
        self.sessions.add(session)
        for method in ("Page.enable", "Runtime.enable", "DOM.enable", "Inspector.enable"):
            self.safe_call(method, session=session)
        if is_page:
            self.safe_call("Network.enable", session=session)
        self.safe_call("Runtime.addBinding", {"name": BINDING_NAME}, session=session)
        self.safe_call("Page.addScriptToEvaluateOnNewDocument", {"source": HELPER_JS, "runImmediately": True}, session=session)
        self.safe_call("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True}, session=session)
        tree = self.safe_call("Page.getFrameTree", session=session)
        if tree:
            self.register_tree(session, tree.get("frameTree"), None)
        if not is_page:
            # The document may already exist; the helper is idempotent.
            self.safe_call("Runtime.evaluate", {"expression": HELPER_JS}, session=session)

    def safe_call(self, method: str, params: dict[str, Any] | None = None, *, session: str | None = None) -> dict[str, Any] | None:
        try:
            return self.call(method, params, session)
        except CdpError as exc:
            logger.debug("cdp %s failed: %s", method, exc.message)
            return None

    def set_frame(self, frame_id: str, session: str, parent_id: str | None) -> None:
        """Remember a frame; an unknown parent never erases one learned from the parent's session."""
        known = self.frames.get(frame_id)
        self.frames[frame_id] = _Frame(session, parent_id or (known.parent if known else None))

    def register_tree(self, session: str, node: dict[str, Any] | None, parent: str | None, *, root: bool = True) -> None:
        if not node:
            return
        frame = node.get("frame") or {}
        frame_id = frame.get("id")
        if not frame_id:
            return
        self.set_frame(frame_id, session, frame.get("parentId") or parent)
        if root:
            self.session_root[session] = frame_id
        for child in node.get("childFrames") or []:
            self.register_tree(session, child, frame_id, root=False)

    # --------------------------------------------------------------------- events

    def pump(self, timeout: float) -> None:
        try:
            method, params, session = self.events.get(timeout=timeout)
        except queue.Empty:
            return
        self.handle(method, params, session)
        # Drain what arrived meanwhile without waiting again.
        while True:
            try:
                method, params, session = self.events.get_nowait()
            except queue.Empty:
                return
            self.handle(method, params, session)

    def handle(self, method: str, params: dict[str, Any], session: str | None) -> None:
        if method == "CdpClient.closed":
            self.connection_closed = True
        elif (method == "Target.targetCrashed" and params.get("targetId") in {self.page_target, *self.child_targets.values()}) or (method == "Inspector.targetCrashed" and session in self.sessions and session != self.reviving_session):
            self.renderer_gone = True
            self.crash_details.update({key: params[key] for key in ("status", "errorCode") if key in params})
        elif method == "Inspector.targetReloadedAfterCrash" and session == self.page_session:
            self.renderer_gone = False
            if session == self.reviving_session:
                self.revival_started = True
        elif method == "Target.attachedToTarget":
            info = params.get("targetInfo") or {}
            child = params.get("sessionId")
            if child and info.get("type") == "iframe" and session in self.sessions:
                self.child_targets[child] = info.get("targetId")
                self.setup_session(child, is_page=False)
                if params.get("waitingForDebugger"):
                    self.safe_call("Runtime.runIfWaitingForDebugger", session=child)
        elif method == "Target.detachedFromTarget":
            gone = params.get("sessionId")
            if gone:
                self.drop_session(gone)
        elif method == "Target.targetDestroyed":
            if params.get("targetId") == self.page_target:
                self.window_closed = True
        elif method == "Page.frameAttached" and session:
            frame_id = params.get("frameId")
            if frame_id:
                self.set_frame(frame_id, session, params.get("parentFrameId"))
        elif method == "Page.frameDetached":
            self.frames.pop(params.get("frameId"), None)
        elif method == "Page.frameNavigated" and session:
            frame = params.get("frame") or {}
            frame_id = frame.get("id")
            if frame_id:
                self.set_frame(frame_id, session, frame.get("parentId"))
            if session == self.page_session and not frame.get("parentId"):
                # The new lecture page is on screen: its reports count from here on.
                self.session_root[session] = frame_id
                self.reports.clear()
                self.ended = False
                self.accepting = True
        elif method == "Runtime.executionContextCreated" and session:
            context = params.get("context") or {}
            aux = context.get("auxData") or {}
            if aux.get("isDefault") and aux.get("frameId") and isinstance(context.get("id"), int):
                self.contexts[(session, context["id"])] = aux["frameId"]
        elif method == "Runtime.executionContextDestroyed" and session:
            key = (session, params.get("executionContextId"))
            self.contexts.pop(key, None)  # type: ignore[arg-type]
            self.reports.pop(key, None)  # type: ignore[arg-type]
        elif method == "Runtime.executionContextsCleared" and session:
            for key in [k for k in self.contexts if k[0] == session]:
                self.contexts.pop(key, None)
                self.reports.pop(key, None)
        elif method == "Runtime.bindingCalled" and session and params.get("name") == BINDING_NAME:
            self.on_binding(session, params)

    def drop_session(self, session: str) -> None:
        self.sessions.discard(session)
        self.child_targets.pop(session, None)
        self.session_root.pop(session, None)
        for key in [k for k in self.contexts if k[0] == session]:
            self.contexts.pop(key, None)
            self.reports.pop(key, None)
        for frame_id in [f for f, info in self.frames.items() if info.session == session]:
            self.frames.pop(frame_id, None)

    def on_binding(self, session: str, params: dict[str, Any]) -> None:
        if not self.accepting:
            return
        try:
            data = json.loads(params.get("payload") or "")
        except ValueError:
            return
        if not isinstance(data, dict):
            return
        context = params.get("executionContextId")
        if data.get("kind") == "ended":
            if self.is_main(data.get("duration")):
                self.ended = True
        elif data.get("kind") == "state" and isinstance(context, int):
            self.reports[(session, context)] = (self.m._clock(), self.normalize_report(data))

    # ------------------------------------------------------------------- lecture

    def play(self, index: int) -> None:
        run = self.run
        item = run.items[index]
        run.auto_minimized = False  # minimize again once this lecture plays
        with self.m._lock:
            item.state = "playing"
            run.phase = PHASE_OPENING
            run.position = None
            run.duration = None
            run.paused = False
        run.skip.clear()
        started = self.m._clock()
        logger.info("chrome_watch item_start run_id=%s index=%s total=%s module_item_id=%s", run.run_id, index + 1, len(run.items), item.module_item_id)
        plan = self._fetch_plan(item)
        recoveries = 0
        needs_attach = False
        while True:
            try:
                recovered_navigation = False
                if needs_attach:
                    recovered_navigation = self._recover_page(plan)
                    needs_attach = False
                outcome = plan if isinstance(plan, str) else self._play_item(item, plan, already_open=recovered_navigation)
                break
            except (_RendererGone, CdpClosed, CdpTimeout, CdpError) as exc:
                self.pump(0)
                if run.stop.is_set():
                    raise _Stopped() from exc
                if self.window_closed:
                    raise _WindowClosed() from exc
                if isinstance(exc, (CdpTimeout, CdpError)) and not needs_attach and not (self.renderer_gone or self.connection_closed or self.client.closed):
                    raise
                if isinstance(exc, CdpClosed):
                    self.connection_closed = True
                if run.stop.is_set():
                    raise _Stopped() from exc
                if isinstance(exc, CdpClosed) or self.connection_closed or self.client.closed:
                    self._wait_for_exit()
                if not self._process_alive():
                    raise ChromeWatchError("chrome_quit_unexpectedly", "Chrome이 갑자기 종료되어 자동 재생을 멈췄어요.") from exc
                self._log_renderer_crash()
                if recoveries >= 3:
                    raise ChromeWatchError("chrome_recover_exhausted", "Chrome 연결이 반복해서 끊겨 자동 재생을 멈췄어요.") from exc
                if recoveries:
                    self._recovery_pause(self.m.timings.reconnect_backoff)
                recoveries += 1
                cause = "cdp_closed" if self.connection_closed or self.client.closed else "renderer_gone"
                logger.info("chrome_watch recover run_id=%s module_item_id=%s attempt=%s cause=%s", run.run_id, item.module_item_id, recoveries, cause)
                with self.m._lock:
                    run.phase = PHASE_OPENING
                    run.position = run.duration = None
                    run.paused = False
                    run.auto_minimized = False
                self.m._notice(run, "recover", "info", "강의에 다시 연결하는 중이에요.", item_title=item.title)
                needs_attach = True
        with self.m._lock:
            if outcome == "done":
                item.state = "done"
            logger.info("chrome_watch item_end run_id=%s module_item_id=%s outcome=%s elapsed_seconds=%.1f", run.run_id, item.module_item_id, outcome, self.m._clock() - started)
            run.position = None
            run.duration = None

    def skip_item(self, item: WatchItem, reason: str, reason_code: str, *, notice: str | None = None, level: str = "error") -> str:
        with self.m._lock:
            item.state = "skipped"
            item.reason = reason
        logger.info("chrome_watch skip run_id=%s module_item_id=%s reason=%s", self.run.run_id, item.module_item_id, reason_code)
        if notice:
            self.m._notice(self.run, "skipped", level, notice, item_title=item.title, reason=reason)
        return "skipped"

    def _play_item(self, item: WatchItem, plan: dict[str, Any], *, already_open: bool = False) -> str:
        run = self.run
        timings = self.m.timings
        try:
            self.expected_duration = float(plan.get("duration") or item.duration or 0) or None
        except (TypeError, ValueError):
            self.expected_duration = None
        retried = False
        if not already_open:
            self._open(plan)
        clock = self.m._clock
        activity_at = clock()
        progress_started = activity_at
        milestones: set[int] = set()
        next_progress_at = activity_at + 300
        last_poll = 0.0
        last_front = -1e9
        last_resume = -1e9
        resume_clicks = 0
        last_time: dict[bool, float] = {}
        minimized_at: float | None = None
        while True:
            self.check_alive()
            if run.stop.is_set():
                raise _Stopped()
            if run.skip.is_set():
                return self.skip_item(item, USER_SKIP_REASON, "user_skip")
            self.pump(timings.tick)
            self.check_alive()
            now = clock()
            if self.accepting and now - last_poll >= timings.poll_interval:
                last_poll = now
                self.poll_reports()
            if self.accepting:
                fresh = self.fresh_reports(now)
                main = self.pick(fresh, main=True)
                if self.ended or (main is not None and main[2]["video"].get("ended")):
                    return "done"
                advanced = False
                for report in fresh:
                    video = report[2].get("video")
                    if not video:
                        continue
                    key = bool(video.get("main"))
                    time_now = float(video.get("time") or 0)
                    previous = last_time.get(key)
                    if previous is None or abs(time_now - previous) >= 0.25:
                        if previous is not None or time_now > 0:
                            advanced = True
                        last_time[key] = time_now
                if advanced:
                    activity_at = now
                    with self.m._lock:
                        if run.phase != PHASE_PLAYING:
                            run.phase = PHASE_PLAYING
                if main is not None:
                    video = main[2]["video"]
                    with self.m._lock:
                        run.position = float(video.get("time") or 0)
                        run.duration = float(video.get("duration") or 0) or None
                        run.paused = bool(video.get("paused"))
                    if advanced and not video.get("paused") and run.auto_minimize and not run.auto_minimized:
                        run.auto_minimized = True
                        self.minimize_window()
                        minimized_at = now
                    if (
                        minimized_at is not None
                        and video.get("paused")
                        and now - minimized_at <= MINIMIZE_CHECK_SECONDS
                    ):
                        # This player stops when minimized: keep the window un-minimized (behind
                        # other windows, not activated) for the rest of the run.
                        minimized_at = None
                        run.auto_minimize = False
                        logger.info("chrome_watch window run_id=%s action=minimize_paused", run.run_id)
                        self.restore_window()
                        self.m._notice(run, "minimize_paused", "info", MINIMIZE_FALLBACK_NOTICE)
                    elif minimized_at is not None and now - minimized_at > MINIMIZE_CHECK_SECONDS:
                        minimized_at = None
                elif fresh and any(r[2].get("video") for r in fresh):
                    video = next(r[2]["video"] for r in fresh if r[2].get("video"))
                    with self.m._lock:
                        run.position = float(video.get("time") or 0)
                        run.duration = None
                        run.paused = bool(video.get("paused"))
                duration = run.duration or self.expected_duration
                if duration and run.position is not None and main is not None:
                    for percent in (25, 50, 75):
                        if percent not in milestones and run.position >= duration * percent / 100:
                            milestones.add(percent)
                            logger.info("chrome_watch progress run_id=%s module_item_id=%s percent=%s", run.run_id, item.module_item_id, percent)
                elif not duration and now >= next_progress_at:
                    logger.info("chrome_watch progress run_id=%s module_item_id=%s elapsed_seconds=%.1f", run.run_id, item.module_item_id, now - progress_started)
                    next_progress_at = now + 300
                main_playing = main is not None and not main[2]["video"].get("paused")
                resume_report = next((r for r in fresh if r[2].get("resume")), None)
                front_report = next((r for r in fresh if r[2].get("front")), None)
                if resume_report is not None and resume_clicks < timings.resume_max_clicks and now - last_resume >= timings.resume_click_every:
                    last_resume = now
                    resume_clicks += 1
                    clicked = self.click(resume_report[0], resume_report[1], "yes")
                    logger.info("chrome_watch click run_id=%s kind=resume ok=%s", run.run_id, clicked)
                    if clicked:
                        activity_at = now  # the player loads again after the answer
                elif resume_report is None and front_report is not None and not main_playing and now - last_front >= timings.front_click_every:
                    last_front = now
                    clicked = self.click(front_report[0], front_report[1], "front")
                    logger.info("chrome_watch click run_id=%s kind=front ok=%s", run.run_id, clicked)
            if now - activity_at >= timings.stall_after:
                logger.info("chrome_watch stall run_id=%s module_item_id=%s seconds_without_progress=%.1f action=%s paused=%s accepting=%s phase=%s", run.run_id, item.module_item_id, now - activity_at, "skip" if retried else "stalled_reload", run.paused, self.accepting, run.phase)
                activity_at = now
                if not retried:
                    retried = True
                    resume_clicks = 0
                    last_front = last_resume = -1e9
                    last_time.clear()
                    text = f"‘{item.title}’ 재생 시간이 {int(timings.stall_after)}초 넘게 늘지 않아 한 번 다시 불러와요. {MINIMIZED_HINT}"
                    with self.m._lock:
                        run.phase = PHASE_STALLED
                    self.m._notice(run, "stalled", "error", text, item_title=item.title)
                    self._open(plan)
                    activity_at = clock()
                else:
                    text = f"‘{item.title}’ 재생이 멈춰 건너뛰었어요. {MINIMIZED_HINT}"
                    return self.skip_item(item, STALL_SKIP_REASON, "stalled", notice=text)

    def _process_alive(self) -> bool:
        if self.m._background_launch:
            pid = self.m._process_tree.find_main_pid(self.m.profile_dir)
            return pid is not None and (self.m._chrome_pid is None or pid == self.m._chrome_pid)
        return self.proc.poll() is None

    def _recovery_pause(self, seconds: float) -> None:
        deadline = self.m._clock() + seconds
        while self.m._clock() < deadline:
            if self.run.stop.is_set():
                raise _Stopped()
            self.m._sleep(min(self.m.timings.port_poll, seconds))
        if self.run.stop.is_set():
            raise _Stopped()

    def _wait_for_exit(self) -> None:
        deadline = self.m._clock() + self.m.timings.terminate_grace
        while True:
            if self.run.stop.is_set():
                raise _Stopped()
            self.pump(0)
            if self.window_closed:
                raise _WindowClosed()
            if not self._process_alive():
                raise ChromeWatchError("chrome_quit_unexpectedly", "Chrome이 갑자기 종료되어 자동 재생을 멈췄어요.")
            if self.m._clock() >= deadline:
                return
            self.m._sleep(self.m.timings.port_poll)

    def _recover_page(self, plan: dict[str, Any]) -> bool:
        self.revival_started = False
        try:
            return self._recover_page_attempt(plan)
        except Exception:
            self.pump(0)
            if self.reviving_session is not None and not self.revival_started:
                self.renderer_gone = True
            self._log_renderer_crash()
            self.page_session = None
            self.sessions.clear()
            self.child_targets.clear()
            self.frames.clear()
            self.session_root.clear()
            self.contexts.clear()
            self.reports.clear()
            self.accepting = self.ended = False
            raise
        finally:
            self.reviving_session = None

    def _recover_page_attempt(self, plan: dict[str, Any]) -> bool:
        self.pump(0)
        if self.run.stop.is_set():
            raise _Stopped()
        if self.window_closed:
            raise _WindowClosed()
        self._log_renderer_crash()
        navigated = False
        if self.page_session is None and self.renderer_gone and not (self.connection_closed or self.client.closed):
            self.attach_page()
        if self.connection_closed or self.client.closed:
            self.client.close()
            try:
                port, path = wait_for_devtools_port(
                    self.m.profile_dir, timeout=self.m.timings.port_timeout,
                    poll=self.m.timings.port_poll,
                    alive=lambda: self._process_alive() and not self.run.stop.is_set(),
                    clock=self.m._clock, sleep=self.m._sleep,
                )
            except ChromeWatchError as exc:
                if self.run.stop.is_set():
                    raise _Stopped() from exc
                raise CdpClosed("DevTools port unavailable") from exc
            if self.run.stop.is_set():
                raise _Stopped()
            try:
                self.client, self.events = self.m._connect_client(self.run, port, path, recovering=True)
            except ChromeWatchError as exc:
                if exc.code != "chrome_connect_failed":
                    raise
                raise CdpClosed("reconnect failed") from exc
            with self.m._lock:
                self.run.client = self.client
        elif self.page_session is not None:
            # A crashed renderer hangs on Page/Runtime.enable. Revive it on the
            # existing session before any domain setup or re-attachment.
            assert self.page_session is not None
            self.child_targets.clear()
            self.sessions = {self.page_session}
            self.reviving_session = self.page_session
            self.frames.clear()
            self.session_root.clear()
            self.contexts.clear()
            self.reports.clear()
            self.renderer_gone = False
            self.accepting = self.ended = False
            result = self.call("Page.navigate", {"url": plan["launch_url"]}, self.page_session)
            self.revival_started = True
            self.crash_details.clear()
            self.crash_logged = False
            if result.get("errorText"):
                raise ChromeWatchError("chrome_navigation_failed", "강의 페이지를 열지 못했어요.")
            navigated = True
            # Navigation destroys the old iframe sessions; auto-attach rebuilds them.
            self.safe_call("Target.detachFromTarget", {"sessionId": self.page_session})
            # Retain a user close even when it arrives during session teardown.
            self.pump(0)
            if self.window_closed:
                raise _WindowClosed()
            if self.renderer_gone:
                raise _RendererGone()
        if not navigated:
            self.child_targets.clear()
            self.sessions.clear()
            self.frames.clear()
            self.session_root.clear()
            self.contexts.clear()
            self.reports.clear()
        self.page_session = None
        self.window_closed = self.connection_closed = self.renderer_gone = False
        self.crash_details.clear()
        self.crash_logged = False
        if not navigated:
            self.accepting = self.ended = False
        if self.run.stop.is_set():
            raise _Stopped()
        self.attach_page()
        if navigated:
            # The navigation may have completed on the session just detached.
            self.accepting = True
        return navigated

    def _log_renderer_crash(self) -> None:
        if self.renderer_gone and not self.crash_logged:
            item = self.run.items[self.run.index]
            logger.warning("chrome_watch renderer_gone run_id=%s module_item_id=%s status=%s error_code=%s", self.run.run_id, item.module_item_id, self.crash_details.get("status", "unknown"), self.crash_details.get("errorCode", "unknown"))
            self.crash_logged = True

    def check_alive(self) -> None:
        if self.run.stop.is_set():
            raise _Stopped()
        if self.window_closed:
            raise _WindowClosed()
        if self.renderer_gone:
            self._log_renderer_crash()
            raise _RendererGone()
        if self.connection_closed or self.client.closed:
            if self.run.stop.is_set():
                raise _Stopped()
            raise CdpClosed("connection closed")
        exit_code = self.proc.poll()
        if exit_code is not None:
            logger.info("chrome_watch chrome_exit run_id=%s exit_code=%s expected=%s", self.run.run_id, exit_code, str(self.run.stop.is_set()).lower())
            if self.run.stop.is_set():
                raise _Stopped()
            raise ChromeWatchError("chrome_quit_unexpectedly", "Chrome이 갑자기 종료되어 자동 재생을 멈췄어요.")

    def _fetch_plan(self, item: WatchItem) -> dict[str, Any] | str:
        try:
            plan = self.m._plan_provider(item.course_id, item.module_item_id, item.course_name)
        except ChromeWatchPlanError as exc:
            if exc.auth:
                raise ChromeWatchError(exc.code, "LMS 로그인이 풀렸어요. 다시 로그인해 주세요.") from exc
            reason = plan_error_reason(exc.code)
            reason_code = exc.code if re.fullmatch(r"[a-z_]+", exc.code) else "plan_error"
            return self.skip_item(item, reason, reason_code, notice=f"‘{item.title}’ {reason}")
        except Exception as exc:  # noqa: BLE001 - one lecture must not end the queue
            logger.warning("chrome_watch plan_failed run_id=%s error=%s", self.run.run_id, type(exc).__name__)
            reason = plan_error_reason(None)
            return self.skip_item(item, reason, "plan_exception", notice=f"‘{item.title}’ {reason}")
        if self.run.stop.is_set():
            raise _Stopped()
        launch_url = plan.get("launch_url")
        if not isinstance(launch_url, str) or not launch_url.startswith("https://"):
            reason = plan_error_reason(None)
            return self.skip_item(item, reason, "launch_url_missing", notice=f"‘{item.title}’ {reason}")
        duration = plan.get("duration")
        if isinstance(duration, (int, float)) and duration > 0 and item.duration is None:
            item.duration = float(duration)
        return plan

    def _open(self, plan: dict[str, Any]) -> None:
        """Cookies, then the lecture page in the playback tab."""
        session = self.page_session
        assert session is not None
        self.accepting = False
        self.ended = False
        self.reports.clear()
        for cookie in plan.get("cookies") or []:
            params = cookie_params(cookie) if isinstance(cookie, dict) else None
            if params is not None:
                self.safe_call("Network.setCookie", params, session=session)
        result = self.call("Page.navigate", {"url": plan["launch_url"]}, session)
        if result.get("errorText"):
            raise ChromeWatchError("chrome_navigation_failed", "강의 페이지를 열지 못했어요.")

    # ------------------------------------------------------------- reports, clicks

    def fresh_reports(self, now: float) -> list[tuple[str, int, dict[str, Any]]]:
        limit = max(self.m.timings.poll_interval * 4, 8.0)
        return [(s, c, data) for (s, c), (at, data) in self.reports.items() if now - at <= limit]

    def is_main(self, duration: Any) -> bool:
        """The lecture itself, not the intro/notice clip before it.

        With the LMS's own length for the item, the main video is the one at least half as long
        (intro clips on some courses run well over 10 s); without it, anything over 10 s.
        """
        try:
            seconds = float(duration or 0)
        except (TypeError, ValueError):
            return False
        expected = self.expected_duration
        if expected and expected > 60:
            return seconds >= expected * 0.5
        return seconds > 10

    def normalize_report(self, value: dict[str, Any]) -> dict[str, Any]:
        video = value.get("video")
        if isinstance(video, dict):
            video["main"] = self.is_main(video.get("duration"))
        return value

    @staticmethod
    def pick(reports: list[tuple[str, int, dict[str, Any]]], *, main: bool) -> tuple[str, int, dict[str, Any]] | None:
        for report in reports:
            video = report[2].get("video")
            if video and bool(video.get("main")) == main:
                return report
        return None

    def poll_reports(self) -> None:
        """Ask every frame's helper for its state (timer-independent, works in background tabs)."""
        for (session, context) in list(self.contexts):
            try:
                result = self.call(
                    "Runtime.evaluate",
                    {"expression": "window.__ssuWatchState ? window.__ssuWatchState() : null", "contextId": context, "returnByValue": True},
                    session,
                    timeout=5.0,
                )
            except (CdpError, CdpTimeout):
                continue
            value = (result.get("result") or {}).get("value")
            if isinstance(value, dict) and value.get("kind") == "state":
                value = self.normalize_report(value)
                self.reports[(session, context)] = (self.m._clock(), value)
                if value.get("video") and value["video"].get("ended") and value["video"].get("main"):
                    self.ended = True

    def click(self, session: str, context: int, what: str) -> bool:
        """Press the player's button `what` ('front' | 'yes') with trusted mouse events."""
        try:
            located = self.call(
                "Runtime.evaluate",
                {"expression": f"window.__ssuWatchLocate ? window.__ssuWatchLocate({json.dumps(what)}) : null", "contextId": context, "returnByValue": True},
                session,
                timeout=5.0,
            )
        except (CdpError, CdpTimeout):
            return False
        box = (located.get("result") or {}).get("value")
        if not isinstance(box, dict):
            return False
        try:
            x, y, w, h = float(box["x"]), float(box["y"]), float(box["w"]), float(box["h"])
        except (KeyError, TypeError, ValueError):
            return False
        local_x, local_y = x + w / 2, y + h / 2
        if not (0 <= local_x <= float(box.get("vw") or 1e9) and 0 <= local_y <= float(box.get("vh") or 1e9)):
            return False  # still off screen after scrolling
        frame_id = self.contexts.get((session, context))
        offset: tuple[float, float] | None = None
        if frame_id is not None:
            try:
                offset = self.frame_offset(frame_id)
            except (CdpError, CdpTimeout, KeyError, IndexError, TypeError):
                offset = None
        if offset is not None:
            self.mouse_click(self.page_session, offset[0] + local_x, offset[1] + local_y)
        elif session != self.page_session:
            # Without the frame's position, ask the frame's own session (local coordinates).
            self.mouse_click(session, local_x, local_y)
        else:
            return False
        return True

    def frame_offset(self, frame_id: str) -> tuple[float, float] | None:
        """Where `frame_id`'s viewport sits in the playback tab's viewport (CSS pixels).

        Each step asks the parent's session for the iframe element's box; that box is relative to
        the root of the parent's session, so the walk continues from that root frame.
        """
        offset_x = offset_y = 0.0
        current = frame_id
        for _ in range(16):
            info = self.frames.get(current)
            if info is None:
                return None
            if info.parent is None:
                return offset_x, offset_y
            parent = self.frames.get(info.parent)
            if parent is None:
                return None
            owner = self.call("DOM.getFrameOwner", {"frameId": current}, parent.session)
            node = owner.get("backendNodeId")
            if node is None:
                return None
            self.safe_call("DOM.scrollIntoViewIfNeeded", {"backendNodeId": node}, session=parent.session)
            quad = self.call("DOM.getBoxModel", {"backendNodeId": node}, parent.session)["model"]["content"]
            offset_x += float(quad[0])
            offset_y += float(quad[1])
            root = self.session_root.get(parent.session)
            if root is None:
                return None
            current = root
        return None

    def mouse_click(self, session: str | None, x: float, y: float) -> None:
        base = {"x": x, "y": y, "button": "left", "pointerType": "mouse"}
        self.call("Input.dispatchMouseEvent", {**base, "type": "mouseMoved", "buttons": 0}, session)
        self.call("Input.dispatchMouseEvent", {**base, "type": "mousePressed", "buttons": 1, "clickCount": 1}, session)
        self.call("Input.dispatchMouseEvent", {**base, "type": "mouseReleased", "buttons": 0, "clickCount": 1}, session)


def plan_error_reason(error_code: str | None) -> str:
    if error_code in {"network_error", "network_timeout"}:
        return "LMS에 연결하지 못했어요. 인터넷 연결을 확인해 주세요."
    return "시청 주소를 찾지 못했어요."
