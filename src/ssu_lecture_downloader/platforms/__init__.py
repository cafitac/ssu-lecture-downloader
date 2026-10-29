"""OS adapters. Only the composition roots (`service/app_context.py`, `cli.py`) import this package."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from pathlib import Path

from ssu_lecture_downloader.platforms.base import APP_DATA_DIR_ENV, Platform

logger = logging.getLogger("ssu_lecture_downloader.platforms")
_warned_relative_app_data_dir = False


def _app_data_dir_from(env: Mapping[str, str]) -> Path | None:
    """`SSU_APP_DATA_DIR` as an absolute path, or None (blank, unset or relative: relative is ignored with one warning)."""
    global _warned_relative_app_data_dir
    override = env.get(APP_DATA_DIR_ENV, "").strip()
    if not override:
        return None
    path = Path(override).expanduser()
    if not path.is_absolute():
        if not _warned_relative_app_data_dir:
            _warned_relative_app_data_dir = True
            logger.warning("%s must be an absolute path; ignoring %r and using the default app data folder", APP_DATA_DIR_ENV, override)
        return None
    return path


def select_platform(*, platform_name: str | None = None, environ: Mapping[str, str] | None = None) -> Platform:
    """The adapter for this OS, using `SSU_APP_DATA_DIR` (from the desktop shell) as app-data root when set.

    `environ` (default: the process environment) is also what the adapter reads the more specific
    overrides from. A relative `SSU_APP_DATA_DIR` is ignored with a one-time warning.

    Only Windows gets its own adapter; every other OS keeps the macOS behavior the code always had.
    """
    name = sys.platform if platform_name is None else platform_name
    env = os.environ if environ is None else environ
    app_data_dir = _app_data_dir_from(env)
    if name == "win32":
        from ssu_lecture_downloader.platforms.windows import WindowsPlatform

        return WindowsPlatform(app_data_dir=app_data_dir)
    from ssu_lecture_downloader.platforms.macos import MacOSPlatform

    return MacOSPlatform(app_data_dir=app_data_dir, environ=env)


__all__ = ["Platform", "select_platform"]
