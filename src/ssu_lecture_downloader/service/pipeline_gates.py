"""Process-wide work limits for the per-lecture artifact pipeline.

Every lecture runs its own chain (download -> audio -> transcript -> cleanup) in its own
thread. Only work that loads this computer is limited, and the limits are shared by every
artifact job of the sidecar process:

* `media`: ffmpeg-class work (audio extraction, MP4 reconstruction, STT WAV conversion, repair cuts).
* `local_stt`: the local speech-to-text engine.

Remote speech-to-text requests are deliberately not gated (the server owns its queue).

A gate is taken for one step at a time and always released in `finally`; waiting is
cancel-aware (the caller's `should_cancel` is polled), and waiters are served first come,
first served. `core` never imports this module: it receives a gate object that has the
`hold(name, should_cancel=..., on_wait=...)` shape (see `core.stt.WorkGates`).
"""

from __future__ import annotations

import threading
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ssu_lecture_downloader.core.postprocess_media import MediaCancelled
from ssu_lecture_downloader.core.stt import LOCAL_STT, MEDIA, TranscriptionCancelled

__all__ = ["GATE_POLL_SECONDS", "LOCAL_STT", "LOCAL_STT_CONCURRENCY", "MEDIA", "MEDIA_CONCURRENCY", "GateCancelled", "StageGates"]

MEDIA_CONCURRENCY = 2
LOCAL_STT_CONCURRENCY = 1
GATE_POLL_SECONDS = 0.25


class GateCancelled(TranscriptionCancelled, MediaCancelled):
    """The caller asked to stop while it was waiting for a gate (it never held the gate)."""


class StageGates:
    def __init__(self, *, media: int = MEDIA_CONCURRENCY, local_stt: int = LOCAL_STT_CONCURRENCY, poll_seconds: float = GATE_POLL_SECONDS) -> None:
        self._limits = {MEDIA: media, LOCAL_STT: local_stt}
        self._in_use = {name: 0 for name in self._limits}
        self._peak = {name: 0 for name in self._limits}
        self._waiting: dict[str, deque[object]] = {name: deque() for name in self._limits}
        self._condition = threading.Condition()
        self._poll_seconds = poll_seconds

    def limit(self, name: str) -> int:
        return self._limits[name]

    def in_use(self, name: str) -> int:
        with self._condition:
            return self._in_use[name]

    def peak(self, name: str) -> int:
        with self._condition:
            return self._peak[name]

    def waiting(self, name: str) -> int:
        with self._condition:
            return len(self._waiting[name])

    @contextmanager
    def hold(
        self,
        name: str,
        *,
        should_cancel: Callable[[], bool] | None = None,
        on_wait: Callable[[], None] | None = None,
    ) -> Iterator[None]:
        """Hold one slot of gate `name` for the `with` body.

        `on_wait` is called once, before the first wait, when no slot is free right now.
        Raises `GateCancelled` (holding nothing) when `should_cancel()` turns true while waiting.
        """
        self._acquire(name, should_cancel, on_wait)
        try:
            yield
        finally:
            self._release(name)

    def _acquire(self, name: str, should_cancel: Callable[[], bool] | None, on_wait: Callable[[], None] | None) -> None:
        ticket = object()
        queue = self._waiting[name]
        waited = False
        with self._condition:
            queue.append(ticket)
            try:
                while True:
                    if should_cancel is not None and should_cancel():
                        raise GateCancelled(f"cancelled while waiting for {name}")
                    if queue[0] is ticket and self._in_use[name] < self._limits[name]:
                        self._in_use[name] += 1
                        self._peak[name] = max(self._peak[name], self._in_use[name])
                        return
                    if not waited:
                        waited = True
                        if on_wait is not None:
                            # Outside the lock: the callback emits a progress event and may take its own locks.
                            self._condition.release()
                            try:
                                on_wait()
                            finally:
                                self._condition.acquire()
                            continue
                    self._condition.wait(self._poll_seconds)
            finally:
                queue.remove(ticket)
                self._condition.notify_all()

    def _release(self, name: str) -> None:
        with self._condition:
            self._in_use[name] -= 1
            self._condition.notify_all()
