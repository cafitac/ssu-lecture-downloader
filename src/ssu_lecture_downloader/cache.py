"""Small thread-safe TTL cache with single-flight loading.

`get_or_load` is the one primitive the service layer needs for LMS lookups:

* a fresh entry is returned without calling the loader;
* concurrent callers for the same missing key share ONE loader call (the others wait
  for its result, or its exception);
* `clear()` invalidates entries *and* loads that are still running, so a response
  fetched before a logout / credential change can never repopulate the cache after it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from time import monotonic
from typing import Any, Generic, TypeVar

T = TypeVar("T")

_MISSING: Any = object()


class _Flight:
    """One in-progress load that other callers of the same key can wait on."""

    __slots__ = ("done", "error", "generation", "value")

    def __init__(self, generation: int) -> None:
        self.generation = generation
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


class TtlCache(Generic[T]):
    def __init__(self, ttl_seconds: float, *, clock: Callable[[], float] = monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._values: dict[Hashable, tuple[float, T]] = {}
        self._flights: dict[Hashable, _Flight] = {}
        self._generation = 0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ plain access

    def _lookup_locked(self, key: Hashable) -> Any:
        entry = self._values.get(key)
        if entry is None:
            return _MISSING
        expires_at, value = entry
        if expires_at <= self._clock():
            del self._values[key]
            return _MISSING
        return value

    def get(self, key: Hashable) -> T | None:
        with self._lock:
            value = self._lookup_locked(key)
        return None if value is _MISSING else value

    def age(self, key: Hashable) -> float | None:
        """Seconds since the entry was stored, or None when it is missing or expired."""
        with self._lock:
            entry = self._values.get(key)
            if entry is None or entry[0] <= self._clock():
                return None
            return self.ttl_seconds - (entry[0] - self._clock())

    def set(self, key: Hashable, value: T) -> T:
        with self._lock:
            self._values[key] = (self._clock() + self.ttl_seconds, value)
        return value

    def discard(self, key: Hashable) -> None:
        with self._lock:
            self._values.pop(key, None)

    def clear(self, *, keep_inflight: bool = False) -> None:
        """Drop every entry. Loads still running are dropped too, unless `keep_inflight`
        (for a reset performed by the loader itself, whose result is still wanted)."""
        with self._lock:
            self._values.clear()
            if not keep_inflight:
                self._generation += 1

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)

    # ------------------------------------------------------------------ single flight

    def get_or_load(
        self,
        key: Hashable,
        loader: Callable[[], T],
        *,
        refresh: bool = False,
        cache_if: Callable[[T], bool] | None = None,
    ) -> T:
        """Cached value, or the result of one shared `loader()` call.

        `refresh=True` skips a fresh cached value but still joins a load that is
        already running (it is as fresh as a new one would be). A loader exception is
        re-raised in every waiting caller and nothing is cached. Waiters still receive
        a value that `cache_if` rejects (e.g. a failure result); it just is not stored.

        The loader must not wait on another flight of this cache (no lock cycles).
        """
        with self._lock:
            if not refresh:
                value = self._lookup_locked(key)
                if value is not _MISSING:
                    return value
            flight = self._flights.get(key)
            # A load that started before clear() belongs to the old generation: joining
            # it would hand out exactly the data the clear was meant to drop.
            leader = flight is None or flight.generation != self._generation
            if leader:
                flight = _Flight(self._generation)
                self._flights[key] = flight
        if not leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value

        try:
            value = loader()
        except BaseException as exc:
            flight.error = exc
            raise
        else:
            flight.value = value
            with self._lock:
                if flight.generation == self._generation and (cache_if is None or cache_if(value)):
                    self._values[key] = (self._clock() + self.ttl_seconds, value)
            return value
        finally:
            with self._lock:
                if self._flights.get(key) is flight:
                    del self._flights[key]
            flight.done.set()
