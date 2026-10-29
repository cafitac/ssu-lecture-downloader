from __future__ import annotations

import asyncio
import threading
import time

from fastapi.testclient import TestClient
from grades_fakes import make_grades_app

from ssu_lecture_downloader.core.grades.worker import GatewayWorker


def test_close_cancels_pending_work_finishes_async_generators_and_ends_the_thread():
    worker = GatewayWorker()
    events = []
    threads = []

    async def agen():
        try:
            yield 1
            await asyncio.sleep(60)
        finally:
            events.append("agen closed")

    async def pending():
        try:
            async for _ in agen():
                pass
        except asyncio.CancelledError:
            events.append("cancelled")
            raise

    async def go():
        task = asyncio.create_task(worker.run(pending()))
        await asyncio.sleep(0.1)
        threads.append(worker._thread)
        await asyncio.get_running_loop().run_in_executor(None, worker.close)
        outcomes = await asyncio.gather(task, return_exceptions=True)
        return outcomes[0]

    outcome = asyncio.run(go())
    assert isinstance(outcome, asyncio.CancelledError)
    assert "cancelled" in events and "agen closed" in events
    assert threads and not threads[0].is_alive()


def test_a_closed_worker_starts_a_fresh_thread_on_the_next_use():
    worker = GatewayWorker()

    async def where():
        return threading.current_thread()

    first = asyncio.run(worker.run(where()))
    worker.close()
    second = asyncio.run(worker.run(where()))
    worker.close()
    assert first is not second and first.name == second.name == "grades-gateway"
    assert first is not threading.main_thread()


def test_the_app_lifespan_closes_the_grades_worker_off_the_event_loop(tmp_path):
    env = make_grades_app(tmp_path, consented=True)
    closed = []
    original = env.service.close
    env.service.close = lambda: (closed.append(threading.current_thread() is threading.main_thread()), original())
    with TestClient(env.app) as client:
        assert client.get("/api/grades").status_code == 200
    deadline = time.monotonic() + 3
    while not closed and time.monotonic() < deadline:
        time.sleep(0.02)
    assert closed == [False]  # called, and on a thread-pool thread rather than the loop thread
