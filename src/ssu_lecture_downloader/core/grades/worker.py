"""A dedicated worker thread with its own asyncio loop for the gateway's coroutines.

UniFFI polls a Rust future on the thread that awaits it, so a slow or blocking call made on the API's
event loop would stall every request. Running them here keeps the API loop free; the caller awaits a
wrapped future, so `asyncio.wait_for` timeouts still apply and cancelling the wait cancels the task
on the worker loop (at its next await point).
"""

from __future__ import annotations

import asyncio
import threading
import weakref
from collections.abc import Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


class GatewayWorker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=self._run, args=(loop,), name="grades-gateway", daemon=True)
                thread.start()
                self._loop, self._thread = loop, thread
                weakref.finalize(self, _stop, loop)  # an abandoned worker does not leave its thread running
            return self._loop

    @staticmethod
    def _run(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_forever()
        finally:
            loop.close()

    async def run(self, coroutine: Coroutine[Any, Any, T]) -> T:
        """Run `coroutine` on the worker loop and await its result from the calling loop."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        return await asyncio.wrap_future(future)

    def close(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._thread
            self._loop = self._thread = None
        if loop is not None:
            _stop(loop)
            if thread is not None:
                thread.join(timeout=2)


def _stop(loop: asyncio.AbstractEventLoop) -> None:
    if not loop.is_closed():
        try:
            loop.call_soon_threadsafe(loop.stop)
        except RuntimeError:  # closed between the check and the call
            pass
