"""The seam between OS-neutral code (`core`) and the operating system.

`core` never imports this package. The composition roots (`service/app_context.py`, `cli.py`) pick an
adapter with `select_platform()` and hand it to core objects, which only see these structural
protocols (core declares what it needs by parameter, not by import).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ssu_lecture_downloader.core.library_layout import PathRules

APP_IDENTIFIER = "com.local.ssu-lecture-downloader"
APP_DOWNLOAD_FOLDER_NAME = "SSU Lecture Downloader"

# Set by the Tauri shell: the app-data root (same folder the shell itself uses).
APP_DATA_DIR_ENV = "SSU_APP_DATA_DIR"
# Existing, more specific overrides; they keep precedence over the app-data root.
PROFILE_DIR_ENV = "SSU_CHROME_WATCH_PROFILE_DIR"
STT_CONFIG_PATH_ENV = "SSU_STT_CONFIG_PATH"

ProcessHandle = Any  # what ProcessTree.spawn returns (a subprocess.Popen-like object)


@runtime_checkable
class AppPaths(Protocol):
    def app_data_dir(self) -> Path: ...

    def chrome_profile_dir(self) -> Path: ...

    def chrome_legacy_cache_dir(self) -> Path | None: ...

    def stt_config_path(self) -> Path: ...

    def default_download_dir(self) -> Path: ...


@runtime_checkable
class ChromeLocator(Protocol):
    def find_chrome(self) -> Path | None: ...


@runtime_checkable
class ProcessTree(Protocol):
    """Starts and stops the dedicated Chrome and everything it spawns."""

    def spawn(self, chrome: Path, chrome_args: list[str], *, background: bool) -> ProcessHandle: ...

    def find_main_pid(self, profile_dir: Path) -> int | None: ...

    def terminate(self, handle: ProcessHandle, *, chrome_pid: int | None, profile_dir: Path, background: bool, grace: float) -> None: ...

    def kill_stale(self, pid: int, profile_dir: Path, grace: float) -> None: ...


@runtime_checkable
class SttCapability(Protocol):
    def local_engine_supported(self) -> bool: ...


@runtime_checkable
class SecretFileWriter(Protocol):
    def write_private(self, path: Path, data: bytes) -> None: ...


@runtime_checkable
class PathRulesProvider(Protocol):
    """What this OS allows in file and folder names (reserved names, length limits). Designed and tested
    for every platform; the service only activates the Windows rules with the Windows platform work."""

    def path_rules(self) -> PathRules: ...


@runtime_checkable
class Platform(AppPaths, ChromeLocator, ProcessTree, SttCapability, SecretFileWriter, PathRulesProvider, Protocol):
    """Everything one operating system adapter provides."""
