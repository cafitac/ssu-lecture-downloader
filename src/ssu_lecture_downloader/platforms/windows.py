"""Windows adapter placeholder. Sprint 2 (S2) implements it; until then every operation says so."""

from __future__ import annotations

from pathlib import Path

from ssu_lecture_downloader.core.library_layout import WINDOWS_RULES, PathRules
from ssu_lecture_downloader.platforms.base import ProcessHandle

_MESSAGE = "Windows support is not implemented yet (platform split sprint 2): {what}"


def _todo(what: str) -> NotImplementedError:
    return NotImplementedError(_MESSAGE.format(what=what))


class WindowsPlatform:
    def __init__(self, *, app_data_dir: Path | None = None) -> None:
        self._app_data_dir = app_data_dir

    # AppPaths
    def app_data_dir(self) -> Path:
        raise _todo("app data directory (%LOCALAPPDATA%)")

    def chrome_profile_dir(self) -> Path:
        raise _todo("Chrome watch profile directory")

    def chrome_legacy_cache_dir(self) -> Path | None:
        return None

    def stt_config_path(self) -> Path:
        raise _todo("remote STT settings path")

    def default_download_dir(self) -> Path:
        raise _todo("default download directory")

    # ChromeLocator
    def find_chrome(self) -> Path | None:
        raise _todo("locating Google Chrome")

    # ProcessTree
    def spawn(self, chrome: Path, chrome_args: list[str], *, background: bool) -> ProcessHandle:
        raise _todo("starting Chrome (Job Object)")

    def find_main_pid(self, profile_dir: Path) -> int | None:
        raise _todo("finding the Chrome main process")

    def is_stopped(self, profile_dir: Path) -> bool | None:
        return None

    def terminate(self, handle: ProcessHandle, *, chrome_pid: int | None, profile_dir: Path, background: bool, grace: float) -> None:
        raise _todo("stopping the Chrome process tree")

    def kill_stale(self, pid: int, profile_dir: Path, grace: float) -> None:
        raise _todo("stopping a stale Chrome process tree")

    # SttCapability
    def local_engine_supported(self) -> bool:
        raise _todo("local STT engine capability")

    # SecretFileWriter
    def write_private(self, path: Path, data: bytes) -> None:
        raise _todo("private file write")

    # PathRulesProvider
    def path_rules(self) -> PathRules:
        # Usable before the rest of the adapter exists: pure data, no OS calls.
        return WINDOWS_RULES
