"""Characterization tests: the macOS behavior that later platform-split PRs must keep byte-identical.

These pin what the code does TODAY (design/07-consistency-migration.md, "macOS invariants" 1-5).
They are not a spec of what is ideal. A failing test here means a macOS behavior changed; fix the
change, not the expectation, unless the behavior change is the explicit goal of the PR.
"""

from __future__ import annotations

import importlib.util
import os
import signal
import stat
import subprocess
import sys
import threading
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from chrome_fakes import FakeBrowser, FakeClock, FakePopen, KillRecorder, make_chrome_binary

from ssu_lecture_downloader.service import app_context
from ssu_lecture_downloader.core import stt, stt_remote_config
from ssu_lecture_downloader.core import chrome_watch as cw
from ssu_lecture_downloader.platforms import macos, select_platform
from ssu_lecture_downloader.platforms.base import APP_IDENTIFIER, PROFILE_DIR_ENV, STT_CONFIG_PATH_ENV
from ssu_lecture_downloader.platforms.macos import MacOSPlatform

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="pins macOS behavior; other platforms get their own characterization")

# conftest hides the real candidates from every test; keep a handle on the real function.
REAL_CHROME_CANDIDATES = macos.chrome_candidates

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ID = "com.local.ssu-lecture-downloader"
CHROME_REL = "Google Chrome.app/Contents/MacOS/Google Chrome"


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home" / "me"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(PROFILE_DIR_ENV, raising=False)
    monkeypatch.delenv(STT_CONFIG_PATH_ENV, raising=False)
    return home


@pytest.fixture(autouse=True)
def reset_popen():
    FakePopen.instances = []
    FakePopen.silent = False
    yield


# ------------------------------------------------------------ invariant 1: app-data paths


def test_chrome_watch_profile_dir_under_application_support(fake_home):
    assert str(select_platform().chrome_profile_dir()) == f"{fake_home}/Library/Application Support/{APP_ID}/chrome-watch-profile"


def test_chrome_watch_profile_dir_env_override_is_expanded(fake_home, monkeypatch):
    monkeypatch.setenv(PROFILE_DIR_ENV, "~/custom-profile")
    assert str(select_platform().chrome_profile_dir()) == f"{fake_home}/custom-profile"
    assert PROFILE_DIR_ENV == "SSU_CHROME_WATCH_PROFILE_DIR"


def test_chrome_watch_pid_file_sits_next_to_the_profile_dir(fake_home):
    # Inert process hooks: a manager registers itself for an atexit shutdown() that would otherwise
    # run the real `ps`/`killpg` against the owner's real watch Chrome once HOME is restored.
    platform = MacOSPlatform(process_command=lambda pid: None, killpg=lambda *a: None)
    manager = cw.ChromeWatchManager(
        lambda *a: {},
        chrome_locator=platform,
        process_tree=platform,
        profile_dir=platform.chrome_profile_dir(),
    )
    assert str(manager._pidfile()) == f"{fake_home}/Library/Application Support/{APP_ID}/chrome-watch.pid"
    # Default profile resolution (no profile_dir given), still with inert hooks.
    default_manager = cw.ChromeWatchManager(lambda *a: {}, chrome_locator=platform, process_tree=platform, app_paths=platform)
    try:
        assert default_manager.profile_dir == platform.chrome_profile_dir()
    finally:
        # Registered for the atexit sweep with the real (default) profile dir; keep it out of that
        # sweep so the interpreter exit never reaches the owner's real watch Chrome pidfile.
        cw._LIVE.discard(default_manager)


def test_stt_json_path(fake_home):
    assert str(select_platform().stt_config_path()) == f"{fake_home}/Library/Application Support/{APP_ID}/stt.json"
    assert STT_CONFIG_PATH_ENV == "SSU_STT_CONFIG_PATH"
    assert APP_IDENTIFIER == APP_ID


def test_stt_json_is_written_with_mode_0600(tmp_path):
    path = tmp_path / "support" / "stt.json"
    store = stt_remote_config.RemoteSTTSettingsStore(path, writer=MacOSPlatform())
    store.save(stt_remote_config.RemoteSTTConfig(api_key="k"))
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not list(path.parent.glob("*.part"))


def test_default_download_dir(fake_home):
    assert str(app_context.default_download_dir()) == f"{fake_home}/Downloads/SSU Lecture Downloader"


def test_sidecar_build_output_layout():
    spec = importlib.util.spec_from_file_location("build_backend_sidecar_under_test", REPO_ROOT / "scripts" / "build_backend_sidecar.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rel = module.OUTPUT.relative_to(module.ROOT)
    assert rel.as_posix() == "frontend/src-tauri/resources/backend-sidecar/ssu-api/ssu-api"
    assert module.OUTPUT_DIR.relative_to(module.ROOT).as_posix() == "frontend/src-tauri/resources/backend-sidecar/ssu-api"
    assert module.CONTENTS_DIRECTORY == "_internal"
    source = (REPO_ROOT / "scripts" / "build_backend_sidecar.py").read_text(encoding="utf-8")
    assert "OUTPUT.chmod(mode | 0o755)" in source


# ------------------------------------------------------------ invariant 3: Chrome binary, argv, kill


def test_chrome_candidates_are_system_then_user_applications(fake_home):
    assert REAL_CHROME_CANDIDATES() == [
        Path(f"/Applications/{CHROME_REL}"),
        Path(f"{fake_home}/Applications/{CHROME_REL}"),
    ]


def test_chrome_args_exact_argv():
    chrome = f"/Applications/{CHROME_REL}"
    profile = f"/Users/me/Library/Application Support/{APP_ID}/chrome-watch-profile"
    assert cw.build_chrome_args(chrome, profile) == [
        chrome,
        f"--user-data-dir={profile}",
        f"--disk-cache-dir={profile}/disk-cache",
        "--disk-cache-size=104857600",
        "--remote-debugging-port=0",
        "--no-first-run",
        "--no-default-browser-check",
        "--mute-audio",
        "--window-size=1000,700",
        "--new-window",
        "about:blank",
    ]


def test_background_launch_argv_goes_through_open(tmp_path):
    chrome = f"/Applications/{CHROME_REL}"
    args = cw.build_chrome_args(chrome, "/p")
    assert macos.build_background_launch(chrome, args) == [
        "/usr/bin/open",
        "-g",
        "-n",
        "-W",
        "-a",
        "/Applications/Google Chrome.app",
        "--args",
        *args[1:],
    ]


class RecordingPopen(FakePopen):
    """FakePopen that remembers wait() calls (the `open -W` launcher is reaped with it)."""

    waits: list = []

    def wait(self, timeout=None):
        RecordingPopen.waits.append(timeout)
        return super().wait(timeout)


def _manager(tmp_path, *, ignore_term=False, background=False, rows=None):
    """A manager with every process hook faked: nothing here can signal or list a real process."""
    profile = tmp_path / "support" / "chrome-watch-profile"
    chrome = make_chrome_binary(tmp_path)
    clock = FakeClock(step=0.5)
    recorder = KillRecorder(FakePopen.instances, ignore_term=ignore_term)
    alive = {"yes": True}
    signals: list[tuple[int, int, float]] = []  # (pgid, sig, fake time of the call)

    def killpg(pgid, sig):
        recorder(pgid, sig)
        signals.append((pgid, sig, clock.t))
        if sig == signal.SIGKILL or (sig == signal.SIGTERM and not ignore_term):
            alive["yes"] = False

    platform = MacOSPlatform(
        candidates=[chrome],
        popen=RecordingPopen,
        killpg=killpg,
        process_command=lambda pid: f"{chrome} --user-data-dir={profile}" if alive["yes"] else None,
        list_processes=lambda: rows or [],
        clock=clock,
        sleep=clock.sleep,
    )
    manager = cw.ChromeWatchManager(
        lambda *a: {},
        chrome_locator=platform,
        process_tree=platform,
        background_launch=background,
        profile_dir=profile,
        connect=lambda url: FakeBrowser([]),
        clock=clock,
        sleep=clock.sleep,
        timings=cw.Timings(terminate_grace=3.0),
    )
    return SimpleNamespace(manager=manager, profile=profile, chrome=chrome, clock=clock, kills=recorder, signals=signals)


@pytest.fixture(autouse=True)
def reset_waits():
    RecordingPopen.waits = []
    yield


def _pidfile(env) -> Path:
    return env.profile.parent / "chrome-watch.pid"


def _assert_grace_respected(env, grace=3.0):
    (_, _term, t_term), (_, _kill, t_kill) = env.signals[:2]
    assert (_term, _kill) == (signal.SIGTERM, signal.SIGKILL)
    assert t_kill - t_term >= grace  # SIGKILL only after the grace period elapsed


STD_KWARGS = {
    "stdin": subprocess.DEVNULL,
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
    "start_new_session": True,
}


def test_launch_spawns_chrome_in_its_own_session_with_exact_argv_and_std_streams(tmp_path):
    env = _manager(tmp_path)
    proc, client, _events = env.manager._launch(SimpleNamespace(run_id="test-run", stop=threading.Event()))
    try:
        popen = FakePopen.instances[0]
        assert proc is popen
        assert popen.args == cw.build_chrome_args(env.chrome, env.profile)
        assert popen.args[0] == str(env.chrome)
        assert popen.kwargs == STD_KWARGS
        assert _pidfile(env).read_text(encoding="utf-8") == "4242"
    finally:
        client.close()


def test_background_launch_is_the_production_default():
    # routers/watch.py builds the manager without background_launch, so this default is what ships.
    import inspect

    assert inspect.signature(cw.ChromeWatchManager).parameters["background_launch"].default is True


def test_background_launch_goes_through_open_and_records_the_chrome_main_pid(tmp_path):
    profile = tmp_path / "support" / "chrome-watch-profile"
    rows = [
        (10, f"/usr/bin/open -g -n -W -a /Applications/Google Chrome.app --args --user-data-dir={profile}"),
        (11, f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir={profile} --remote-debugging-port=0"),
        (12, f"/x/Google Chrome Helper --type=renderer --user-data-dir={profile}"),
    ]
    env = _manager(tmp_path, background=True, rows=rows)
    proc, client, _events = env.manager._launch(SimpleNamespace(run_id="test-run", stop=threading.Event()))
    try:
        popen = FakePopen.instances[0]
        assert proc is popen
        assert popen.args == macos.build_background_launch(env.chrome, cw.build_chrome_args(env.chrome, env.profile))
        assert popen.args[:6] == ["/usr/bin/open", "-g", "-n", "-W", "-a", f"{tmp_path}/Google Chrome.app"]
        assert popen.kwargs == STD_KWARGS
        assert _pidfile(env).read_text(encoding="utf-8") == "11"  # Chrome itself, not the launcher (4242)
    finally:
        client.close()


def test_terminate_sends_sigterm_to_the_group_and_stops_when_chrome_exits(tmp_path):
    env = _manager(tmp_path)
    proc = RecordingPopen(["x", f"--user-data-dir={tmp_path}"])
    _pidfile(env).parent.mkdir(parents=True, exist_ok=True)
    _pidfile(env).write_text("4242", encoding="utf-8")
    env.manager._terminate(proc)
    assert env.kills.calls == [(4242, signal.SIGTERM)]
    assert not _pidfile(env).exists()


def test_terminate_escalates_to_sigkill_on_the_group_after_the_grace_period(tmp_path):
    env = _manager(tmp_path, ignore_term=True)
    proc = RecordingPopen(["x", f"--user-data-dir={tmp_path}"])
    env.manager._terminate(proc)
    assert env.kills.calls == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
    _assert_grace_respected(env)
    assert env.manager.timings.terminate_grace == 3.0
    assert cw.Timings().terminate_grace == 5.0  # production grace


def _background_launched(tmp_path, *, ignore_term):
    profile = tmp_path / "support" / "chrome-watch-profile"
    rows = [(11, f"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir={profile}")]
    env = _manager(tmp_path, background=True, ignore_term=ignore_term, rows=rows)
    proc, client, _events = env.manager._launch(SimpleNamespace(run_id="test-run", stop=threading.Event()))
    client.close()
    assert _pidfile(env).read_text(encoding="utf-8") == "11"
    return env, proc


def test_background_terminate_signals_chrome_group_then_reaps_the_launcher(tmp_path):
    env, launcher = _background_launched(tmp_path, ignore_term=False)
    env.manager._terminate(launcher)
    assert env.kills.calls == [(11, signal.SIGTERM)]  # Chrome exited after SIGTERM: no SIGKILL
    assert RecordingPopen.waits == [5]  # `open -W` launcher waited for
    assert not _pidfile(env).exists()
    assert env.manager._chrome_pid is None


def test_background_terminate_escalates_to_sigkill_when_chrome_stays_alive(tmp_path):
    env, launcher = _background_launched(tmp_path, ignore_term=True)
    # process_command keeps reporting Chrome as running until SIGKILL.
    env.manager._terminate(launcher)
    assert env.kills.calls == [(11, signal.SIGTERM), (11, signal.SIGKILL)]
    _assert_grace_respected(env)
    assert RecordingPopen.waits == [5]
    assert not _pidfile(env).exists()
    assert env.manager._chrome_pid is None


def test_default_killpg_is_os_killpg(monkeypatch):
    calls = []
    monkeypatch.setattr(macos.os, "killpg", lambda pgid, sig: calls.append((pgid, sig)))
    macos.default_killpg(77, signal.SIGTERM)
    assert calls == [(77, signal.SIGTERM)]


# ------------------------------------------------------------ invariant 5: local STT predicate


@pytest.mark.parametrize(
    ("platform_name", "machine", "expected"),
    [
        ("darwin", "x86_64", False),
        ("darwin", "arm64", True),
        ("linux", "x86_64", True),
        ("win32", "AMD64", True),
    ],
)
def test_local_stt_predicate(monkeypatch, platform_name, machine, expected):
    monkeypatch.setattr(macos.sys, "platform", platform_name)
    monkeypatch.setattr(macos.platform, "machine", lambda: machine)
    assert MacOSPlatform().local_engine_supported() is expected


def test_unsupported_intel_mac_message_is_unchanged(monkeypatch):
    monkeypatch.setattr(stt, "_has_faster_whisper", lambda: False)
    monkeypatch.setattr(macos.sys, "platform", "darwin")
    monkeypatch.setattr(macos.platform, "machine", lambda: "x86_64")
    status = stt.check_stt_provider("faster_whisper", stt_capability=MacOSPlatform())
    assert (status.available, status.platform_supported) == (False, False)
    assert status.install_hint == "이 Mac(Intel)에서는 자막 생성을 지원하지 않아요. Apple 실리콘 Mac에서 사용할 수 있어요."
    monkeypatch.setattr(macos.platform, "machine", lambda: "arm64")
    status = stt.check_stt_provider("faster_whisper", stt_capability=MacOSPlatform())
    assert status.platform_supported is True
    assert status.install_hint == stt.faster_whisper_install_hint()


def test_pyproject_gates_faster_whisper_the_same_way():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    entries = [dep for dep in data["project"]["dependencies"] if dep.startswith("faster-whisper")]
    assert entries == ["faster-whisper>=1.1.0; sys_platform != 'darwin' or platform_machine == 'arm64'"]
