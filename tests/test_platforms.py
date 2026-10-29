"""The platform seam: adapter selection, the app-data root and the macOS fallback paths."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from ssu_lecture_downloader.platforms import select_platform
from ssu_lecture_downloader.platforms.base import (
    APP_DATA_DIR_ENV,
    APP_IDENTIFIER,
    PROFILE_DIR_ENV,
    STT_CONFIG_PATH_ENV,
    AppPaths,
    ChromeLocator,
    Platform,
    ProcessTree,
    SecretFileWriter,
    SttCapability,
)
from ssu_lecture_downloader.platforms.macos import MacOSPlatform
from ssu_lecture_downloader.platforms.windows import WindowsPlatform


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home" / "me"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv(PROFILE_DIR_ENV, raising=False)
    monkeypatch.delenv(STT_CONFIG_PATH_ENV, raising=False)
    monkeypatch.delenv(APP_DATA_DIR_ENV, raising=False)
    return home


def test_select_platform_picks_macos_on_darwin():
    assert isinstance(select_platform(platform_name="darwin", environ={}), MacOSPlatform)


def test_select_platform_picks_windows_on_win32():
    assert isinstance(select_platform(platform_name="win32", environ={}), WindowsPlatform)


def test_select_platform_keeps_macos_behavior_on_other_posix_systems():
    assert isinstance(select_platform(platform_name="linux", environ={}), MacOSPlatform)


def test_select_platform_defaults_to_this_machine(monkeypatch):
    monkeypatch.setattr("ssu_lecture_downloader.platforms.sys.platform", "darwin")
    assert isinstance(select_platform(environ={}), MacOSPlatform)


def test_the_macos_adapter_implements_every_protocol():
    adapter = MacOSPlatform()
    for protocol in (AppPaths, ChromeLocator, ProcessTree, SttCapability, SecretFileWriter, Platform):
        assert isinstance(adapter, protocol)


def test_the_windows_placeholder_has_the_same_surface_and_says_it_is_not_implemented():
    adapter = WindowsPlatform()
    assert isinstance(adapter, Platform)
    for call in (
        adapter.app_data_dir,
        adapter.chrome_profile_dir,
        adapter.stt_config_path,
        adapter.default_download_dir,
        adapter.find_chrome,
        adapter.local_engine_supported,
        lambda: adapter.write_private(Path("x"), b""),
        lambda: adapter.spawn(Path("c"), [], background=False),
        lambda: adapter.find_main_pid(Path("p")),
        lambda: adapter.terminate(None, chrome_pid=None, profile_dir=Path("p"), background=False, grace=1.0),
        lambda: adapter.kill_stale(1, Path("p"), 1.0),
    ):
        with pytest.raises(NotImplementedError, match="not implemented yet"):
            call()


def test_ssu_app_data_dir_is_the_app_data_root(tmp_path, fake_home):
    root = tmp_path / "shell-data"
    platform = select_platform(platform_name="darwin", environ={APP_DATA_DIR_ENV: str(root)})
    assert platform.app_data_dir() == root
    assert platform.chrome_profile_dir() == root / "chrome-watch-profile"
    assert platform.stt_config_path() == root / "stt.json"


def test_ssu_app_data_dir_is_read_from_the_process_environment(tmp_path, monkeypatch, fake_home):
    monkeypatch.setenv(APP_DATA_DIR_ENV, str(tmp_path / "from-env"))
    assert select_platform().app_data_dir() == tmp_path / "from-env"


def test_ssu_app_data_dir_expands_a_home_prefix_and_ignores_blank(fake_home):
    assert select_platform(platform_name="darwin", environ={APP_DATA_DIR_ENV: "~/data"}).app_data_dir() == fake_home / "data"
    blank = select_platform(platform_name="darwin", environ={APP_DATA_DIR_ENV: "  "})
    assert blank.app_data_dir() == fake_home / "Library" / "Application Support" / APP_IDENTIFIER


def test_specific_env_overrides_keep_precedence_over_the_app_data_root(tmp_path, monkeypatch, fake_home):
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path / "profile"))
    monkeypatch.setenv(STT_CONFIG_PATH_ENV, str(tmp_path / "stt-elsewhere.json"))
    platform = select_platform(platform_name="darwin", environ={APP_DATA_DIR_ENV: str(tmp_path / "root")})
    assert platform.chrome_profile_dir() == tmp_path / "profile"
    assert platform.stt_config_path() == tmp_path / "stt-elsewhere.json"


def test_without_ssu_app_data_dir_the_paths_are_exactly_the_previous_macos_ones(fake_home):
    # The literal expressions the code used before the platform split.
    old_support = Path.home() / "Library" / "Application Support" / "com.local.ssu-lecture-downloader"
    platform = select_platform(platform_name="darwin", environ={})
    assert platform.app_data_dir() == old_support
    assert platform.chrome_profile_dir() == old_support / "chrome-watch-profile"
    assert platform.stt_config_path() == old_support / "stt.json"
    assert platform.default_download_dir() == Path.home() / "Downloads" / "SSU Lecture Downloader"
    assert str(platform.stt_config_path()) == f"{fake_home}/Library/Application Support/com.local.ssu-lecture-downloader/stt.json"


def test_the_shell_value_and_the_fallback_agree_on_macos(fake_home):
    shell_value = fake_home / "Library" / "Application Support" / APP_IDENTIFIER
    with_env = select_platform(platform_name="darwin", environ={APP_DATA_DIR_ENV: str(shell_value)})
    without_env = select_platform(platform_name="darwin", environ={})
    assert with_env.chrome_profile_dir() == without_env.chrome_profile_dir()
    assert with_env.stt_config_path() == without_env.stt_config_path()


def test_write_private_creates_a_0600_file_and_leaves_no_partial(tmp_path):
    target = tmp_path / "support" / "secret.json"
    target.parent.mkdir()
    MacOSPlatform().write_private(target, b'{"k": 1}')
    assert target.read_bytes() == b'{"k": 1}'
    assert stat.S_IMODE(os.stat(target).st_mode) == 0o600
    MacOSPlatform().write_private(target, b"{}")
    assert target.read_bytes() == b"{}"
    assert not list(target.parent.glob("*.part"))


def test_the_app_context_hands_the_selected_platform_to_the_remote_stt_store(tmp_path, monkeypatch):
    from ssu_lecture_downloader.core.stt_remote_config import RemoteSTTConfig
    from ssu_lecture_downloader.service.app_context import AppContext
    from ssu_lecture_downloader.service.security import MediaPathPolicy
    from ssu_lecture_downloader.core.jobs import JobRunner

    monkeypatch.delenv(STT_CONFIG_PATH_ENV, raising=False)  # conftest isolates it; here the app-data root decides
    platform = select_platform(platform_name="darwin", environ={APP_DATA_DIR_ENV: str(tmp_path / "root")})
    context = AppContext(
        service=object(),
        credentials=object(),
        default_output_dir=tmp_path,
        media_paths=MediaPathPolicy(tmp_path),
        runner=JobRunner(error_code_for=lambda exc: "x"),
        platform=platform,
    )
    context.remote_stt.store.save(RemoteSTTConfig(api_key="k"))
    saved = tmp_path / "root" / "stt.json"
    assert saved.is_file() and stat.S_IMODE(os.stat(saved).st_mode) == 0o600
