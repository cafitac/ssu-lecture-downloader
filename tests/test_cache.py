import threading
import time

import pytest

from ssu_lecture_downloader.cache import TtlCache


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_entries_expire_after_the_ttl():
    clock = Clock()
    cache = TtlCache(10, clock=clock)
    cache.set('k', 'v')

    clock.now = 9.9
    assert cache.get('k') == 'v'
    assert cache.age('k') == pytest.approx(9.9)
    clock.now = 10.0
    assert cache.get('k') is None
    assert cache.age('k') is None


def test_get_or_load_returns_the_cached_value_without_calling_the_loader():
    cache = TtlCache(60)
    calls = []

    def loader():
        calls.append(1)
        return 'value'

    assert cache.get_or_load('k', loader) == 'value'
    assert cache.get_or_load('k', loader) == 'value'
    assert len(calls) == 1
    assert cache.get_or_load('k', loader, refresh=True) == 'value'
    assert len(calls) == 2


def test_concurrent_callers_share_one_load():
    cache = TtlCache(60)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def loader():
        calls.append(1)
        started.set()
        release.wait(5)
        return 'value'

    results = []
    threads = [threading.Thread(target=lambda: results.append(cache.get_or_load('k', loader))) for _ in range(6)]
    threads[0].start()
    assert started.wait(5)
    for thread in threads[1:]:
        thread.start()
    time.sleep(0.05)
    release.set()
    for thread in threads:
        thread.join(5)

    assert results == ['value'] * 6
    assert len(calls) == 1


def test_a_failed_load_reaches_every_waiter_and_is_not_cached():
    cache = TtlCache(60)
    started = threading.Event()
    release = threading.Event()
    attempts = []

    def loader():
        attempts.append(1)
        started.set()
        release.wait(5)
        raise RuntimeError('upstream down')

    errors = []

    def call():
        try:
            cache.get_or_load('k', loader)
        except RuntimeError as exc:
            errors.append(str(exc))

    first = threading.Thread(target=call)
    first.start()
    assert started.wait(5)
    second = threading.Thread(target=call)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(5)
    second.join(5)

    assert errors == ['upstream down', 'upstream down']
    assert len(attempts) == 1
    assert cache.get('k') is None
    assert cache.get_or_load('k', lambda: 'recovered') == 'recovered'


def test_clear_drops_a_load_that_was_running_and_does_not_let_new_callers_join_it():
    cache = TtlCache(60)
    started = threading.Event()
    release = threading.Event()

    def stale_loader():
        started.set()
        release.wait(5)
        return 'stale'

    stale = []
    thread = threading.Thread(target=lambda: stale.append(cache.get_or_load('k', stale_loader)))
    thread.start()
    assert started.wait(5)

    cache.clear()
    assert cache.get_or_load('k', lambda: 'fresh') == 'fresh'
    release.set()
    thread.join(5)

    assert stale == ['stale']  # the old caller still gets its own result ...
    assert cache.get('k') == 'fresh'  # ... but it never overwrote the newer data


def test_a_reset_by_the_loader_itself_keeps_its_result():
    cache = TtlCache(60)

    def loader():
        cache.clear(keep_inflight=True)
        return 'value'

    cache.get_or_load('k', loader)

    assert cache.get('k') == 'value'


def test_cache_if_keeps_rejected_results_out_of_the_cache():
    cache = TtlCache(60)
    calls = []

    def loader():
        calls.append(1)
        return []

    assert cache.get_or_load('k', loader, cache_if=bool) == []
    assert cache.get_or_load('k', loader, cache_if=bool) == []
    assert len(calls) == 2
