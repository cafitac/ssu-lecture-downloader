"""macOS adapter. Bodies moved verbatim from core/chrome_watch.py, core/stt_remote_config.py,
core/stt.py, service/app_context.py and service/credentials.py (platform split, S1-PY-PLATFORMS)."""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ssu_lecture_downloader.core.credentials import CredentialPair
from ssu_lecture_downloader.core.library_layout import MACOS_RULES, PathRules
from ssu_lecture_downloader.platforms.base import (
    APP_DOWNLOAD_FOLDER_NAME,
    APP_IDENTIFIER,
    PROFILE_DIR_ENV,
    STT_CONFIG_PATH_ENV,
    ProcessHandle,
)

CHROME_RELATIVE_PATH = "Google Chrome.app/Contents/MacOS/Google Chrome"


def chrome_candidates() -> list[Path]:
    return [
        Path("/Applications") / CHROME_RELATIVE_PATH,
        Path.home() / "Applications" / CHROME_RELATIVE_PATH,
    ]


def find_chrome(candidates: list[Path] | None = None) -> Path | None:
    for candidate in candidates if candidates is not None else chrome_candidates():
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        except OSError:
            continue
    return None


def default_process_command(pid: int) -> str | None:
    try:
        completed = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    text = completed.stdout.strip()
    return text or None


def build_background_launch(chrome: Path | str, chrome_args: list[str]) -> list[str]:
    """Start Chrome through `open -g` so it does not come to the front and take the keyboard.

    `-n` forces a separate instance (own profile), `-W` keeps `open` alive until that instance
    quits, so the launcher process still tells whether Chrome is running.
    """
    chrome = Path(chrome)
    bundle = next((parent for parent in chrome.parents if parent.suffix == ".app"), None)
    if bundle is None:
        return chrome_args
    return ["/usr/bin/open", "-g", "-n", "-W", "-a", str(bundle), "--args", *chrome_args[1:]]


def default_list_processes() -> list[tuple[int, str]]:
    try:
        completed = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    rows: list[tuple[int, str]] = []
    for line in completed.stdout.splitlines():
        head, _, command = line.strip().partition(" ")
        if head.isdigit():
            rows.append((int(head), command.strip()))
    return rows


def find_chrome_main_pid(processes: list[tuple[int, str]], profile_dir: Path | str) -> int | None:
    """The browser process of the dedicated profile (helpers carry a --type= flag)."""
    marker = f"--user-data-dir={profile_dir}"
    for pid, command in processes:
        if marker in command and "--type=" not in command and not command.startswith("/usr/bin/open"):
            return pid
    return None


def default_killpg(pgid: int, sig: int) -> None:
    os.killpg(pgid, sig)


class MacOSPlatform:
    """AppPaths + ChromeLocator + ProcessTree + SttCapability + SecretFileWriter for macOS.

    The keyword arguments are test seams; production uses the defaults.
    """

    def __init__(
        self,
        *,
        app_data_dir: Path | None = None,
        environ: Mapping[str, str] | None = None,
        candidates: list[Path] | None = None,
        popen: Callable[..., Any] | None = None,
        killpg: Callable[[int, int], None] | None = None,
        process_command: Callable[[int], str | None] | None = None,
        list_processes: Callable[[], list[tuple[int, str]]] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._app_data_dir = app_data_dir
        self._environ = environ  # None: the live process environment
        self._candidates = candidates
        self._popen = popen or subprocess.Popen
        self._killpg = killpg or default_killpg
        self._process_command = process_command or default_process_command
        self._list_processes = list_processes or default_list_processes
        self._clock = clock
        self._sleep = sleep

    def _env(self) -> Mapping[str, str]:
        return os.environ if self._environ is None else self._environ

    # ------------------------------------------------------------------ AppPaths

    def app_data_dir(self) -> Path:
        if self._app_data_dir is not None:
            return self._app_data_dir
        return Path.home() / "Library" / "Application Support" / APP_IDENTIFIER

    def chrome_profile_dir(self) -> Path:
        override = self._env().get(PROFILE_DIR_ENV, "").strip()
        if override:
            return Path(override).expanduser()
        return self.app_data_dir() / "chrome-watch-profile"

    def stt_config_path(self) -> Path:
        override = self._env().get(STT_CONFIG_PATH_ENV, "").strip()
        if override:
            return Path(override).expanduser()
        return self.app_data_dir() / "stt.json"

    def default_download_dir(self) -> Path:
        """Where downloads go when the request names no folder.

        Deliberately not relative to the working directory: a packaged app starts with
        its cwd inside the .app bundle, which is read-only or discarded on update.
        """
        return Path.home() / "Downloads" / APP_DOWNLOAD_FOLDER_NAME

    # -------------------------------------------------------------- ChromeLocator

    def find_chrome(self) -> Path | None:
        return find_chrome(self._candidates)

    # ----------------------------------------------------------------- ProcessTree

    def spawn(self, chrome: Path, chrome_args: list[str], *, background: bool) -> ProcessHandle:
        return self._popen(
            build_background_launch(chrome, chrome_args) if background else chrome_args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def find_main_pid(self, profile_dir: Path) -> int | None:
        return find_chrome_main_pid(self._list_processes(), profile_dir)

    def terminate(self, handle: ProcessHandle, *, chrome_pid: int | None, profile_dir: Path, background: bool, grace: float) -> None:
        """SIGTERM the whole process group, then SIGKILL what is left. The profile stays."""
        proc = handle
        if background:
            # `proc` is the `open -W` launcher; Chrome itself is a separate process group.
            pid = chrome_pid or find_chrome_main_pid(self._list_processes(), profile_dir)
            if pid is not None:
                self._signal_group(pid, signal.SIGTERM)
                deadline = self._clock() + grace
                while self._process_command(pid) and self._clock() < deadline:
                    self._sleep(0.1)
                if self._process_command(pid):
                    self._signal_group(pid, signal.SIGKILL)
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            return
        pgid = getattr(proc, "pid", None)
        if isinstance(pgid, int) and pgid > 0:
            self._signal_group(pgid, signal.SIGTERM)
            deadline = self._clock() + grace
            while proc.poll() is None and self._clock() < deadline:
                self._sleep(0.05)
            if proc.poll() is None:
                self._signal_group(pgid, signal.SIGKILL)
                try:
                    proc.wait(timeout=2)
                except Exception:  # noqa: BLE001
                    pass

    def kill_stale(self, pid: int, profile_dir: Path, grace: float) -> None:
        """A Chrome of an earlier backend run that is still around (its pid file names it)."""
        command = self._process_command(pid)
        if command and str(profile_dir) in command:
            self._signal_group(pid, signal.SIGTERM)
            deadline = self._clock() + grace
            while self._process_command(pid) and self._clock() < deadline:
                self._sleep(0.1)
            if self._process_command(pid):
                self._signal_group(pid, signal.SIGKILL)

    def _signal_group(self, pgid: int, sig: int) -> None:
        try:
            self._killpg(pgid, sig)
        except (ProcessLookupError, PermissionError):
            pass
        except OSError:
            pass

    # ---------------------------------------------------------------- SttCapability

    def local_engine_supported(self) -> bool:
        # faster-whisper's CTranslate2 wheels are not published for Intel Macs (see pyproject markers).
        return not (sys.platform == "darwin" and platform.machine() == "x86_64")

    # -------------------------------------------------------------- PathRulesProvider

    def path_rules(self) -> PathRules:
        return MACOS_RULES

    # -------------------------------------------------------------- SecretFileWriter

    def write_private(self, path: Path, data: bytes) -> None:
        partial = path.with_name(f"{path.name}.part")
        try:
            # 0600 from the first byte: the file may contain an API key.
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(partial, path)
        finally:
            partial.unlink(missing_ok=True)


class MacOSKeychainCredentialStore:
    def __init__(self, *, service_name: str = "ssu-lecture-downloader", runner=None) -> None:
        self.service_name = service_name
        self.runner = runner or self._run_security

    @staticmethod
    def _run_security(args: list[str]) -> str:
        completed = subprocess.run(args, check=True, text=True, capture_output=True)
        return completed.stdout.strip()

    def _find(self, account: str) -> str | None:
        try:
            value = self.runner(["security", "find-generic-password", "-w", "-s", self.service_name, "-a", account])
        except Exception:
            return None
        value = str(value).strip()
        return value or None

    def get(self) -> CredentialPair | None:
        username = self._find("SSU_USERNAME")
        password = self._find("SSU_PASSWORD")
        if not username or not password:
            return None
        return CredentialPair(username=username, password=password)

    def status(self) -> dict[str, object]:
        return {
            "source": "keychain",
            "username_present": self._find("SSU_USERNAME") is not None,
            "password_present": self._find("SSU_PASSWORD") is not None,
            "can_write": True,
        }

    def save(self, pair: CredentialPair) -> None:
        self._save_value("SSU_USERNAME", pair.username)
        self._save_value("SSU_PASSWORD", pair.password)

    def _save_value(self, account: str, value: str) -> None:
        self.runner([
            "security",
            "add-generic-password",
            "-U",
            "-s",
            self.service_name,
            "-a",
            account,
            "-w",
            value,
        ])

    def delete(self) -> None:
        self._delete_value("SSU_USERNAME")
        self._delete_value("SSU_PASSWORD")

    def _delete_value(self, account: str) -> None:
        try:
            self.runner(["security", "delete-generic-password", "-s", self.service_name, "-a", account])
        except Exception:
            pass
