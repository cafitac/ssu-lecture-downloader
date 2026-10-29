import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def loopback_test_client(monkeypatch):
    """The API rejects non-loopback Host headers, so test clients talk to 127.0.0.1."""
    original_init = TestClient.__init__

    def init(self, app, base_url="http://127.0.0.1:8000", *args, **kwargs):
        original_init(self, app, base_url, *args, **kwargs)

    monkeypatch.setattr(TestClient, "__init__", init)


@pytest.fixture(autouse=True)
def isolated_stt_settings(monkeypatch, tmp_path_factory):
    """Never read or write the real ~/Library/Application Support remote-STT settings in tests."""
    monkeypatch.setenv("SSU_STT_CONFIG_PATH", str(tmp_path_factory.mktemp("stt-settings") / "stt.json"))


@pytest.fixture(autouse=True)
def no_real_chrome(monkeypatch, tmp_path_factory):
    """Default Chrome lookups find nothing and the playback profile is a temp dir: no test launches Chrome."""
    from ssu_lecture_downloader.platforms import macos
    from ssu_lecture_downloader.platforms.base import APP_DATA_DIR_ENV, PROFILE_DIR_ENV

    monkeypatch.setattr(macos, "chrome_candidates", lambda: [])
    monkeypatch.setenv(PROFILE_DIR_ENV, str(tmp_path_factory.mktemp("chrome-profile") / "chrome-watch-profile"))
    # The desktop shell exports this for the real app; tests must not inherit it.
    monkeypatch.delenv(APP_DATA_DIR_ENV, raising=False)
