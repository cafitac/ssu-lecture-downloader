from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))  # grades_fakes


@pytest.fixture(autouse=True)
def isolated_app_data(monkeypatch, tmp_path):
    """The real consent file lives in the user's app-data folder; no grades test may touch it."""
    from ssu_lecture_downloader.platforms.base import APP_DATA_DIR_ENV

    app_data = tmp_path / "app-data"
    monkeypatch.setenv(APP_DATA_DIR_ENV, str(app_data))
    return app_data
