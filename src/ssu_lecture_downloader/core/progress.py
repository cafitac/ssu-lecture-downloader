"""The one progress-callback convention shared by download, media, STT and the service."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

ProgressCallback = Callable[[dict[str, Any]], None]
CancelCheck = Callable[[], bool]


def emit_progress(on_progress: ProgressCallback | None, event: dict[str, Any]) -> None:
    """Deliver `event` when a callback is registered (callers pass `None` for 'no listener')."""
    if on_progress:
        on_progress(event)
