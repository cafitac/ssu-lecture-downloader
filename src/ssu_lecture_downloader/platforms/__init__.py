"""OS adapters. Only the composition roots (`service/app_context.py`, `cli.py`) import this package."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from ssu_lecture_downloader.platforms.base import APP_DATA_DIR_ENV, Platform


def select_platform(*, platform_name: str | None = None, environ: Mapping[str, str] | None = None) -> Platform:
    """The adapter for this OS, using `SSU_APP_DATA_DIR` (from the desktop shell) as app-data root when set.

    Only Windows gets its own adapter; every other OS keeps the macOS behavior the code always had.
    """
    name = sys.platform if platform_name is None else platform_name
    env = os.environ if environ is None else environ
    override = env.get(APP_DATA_DIR_ENV, "").strip()
    app_data_dir = Path(override).expanduser() if override else None
    if name == "win32":
        from ssu_lecture_downloader.platforms.windows import WindowsPlatform

        return WindowsPlatform(app_data_dir=app_data_dir)
    from ssu_lecture_downloader.platforms.macos import MacOSPlatform

    return MacOSPlatform(app_data_dir=app_data_dir)


__all__ = ["Platform", "select_platform"]
