from __future__ import annotations

import json
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ssu_lecture_downloader.core.redaction import redact_headers, redact_mapping, redact_url


@dataclass(frozen=True)
class TraceEntry:
    step: str
    method: str
    url: str
    status_code: int | None = None
    final_url: str | None = None
    elapsed_ms: float | None = None
    request_headers: dict[str, Any] = field(default_factory=dict)
    response_headers: dict[str, Any] = field(default_factory=dict)
    cookie_names: list[str] = field(default_factory=list)
    body_preview: Any | None = None
    body_size: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "method": self.method,
            "url": redact_url(self.url),
            "status_code": self.status_code,
            "final_url": redact_url(self.final_url) if self.final_url else None,
            "elapsed_ms": self.elapsed_ms,
            "request_headers": redact_headers(self.request_headers),
            "response_headers": redact_headers(self.response_headers),
            "cookie_names": list(self.cookie_names),
            "body_preview": redact_mapping(self.body_preview),
            "body_size": self.body_size,
        }


# A long-lived backend traces every LMS request; keeping them all would grow without
# bound. One CLI run or one troubleshooting session stays far below this.
DEFAULT_MAX_TRACE_ENTRIES = 1000


class NetworkTrace:
    def __init__(self, max_entries: int = DEFAULT_MAX_TRACE_ENTRIES) -> None:
        self._entries: deque[TraceEntry] = deque(maxlen=max_entries)
        self._lock = threading.Lock()

    def add(self, entry: TraceEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def last_entry(self) -> dict[str, Any] | None:
        """The most recent request, redacted, without converting the whole history."""
        with self._lock:
            entry = self._entries[-1] if self._entries else None
        return entry.to_dict() if entry is not None else None

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            entries = list(self._entries)
        return {"entries": [entry.to_dict() for entry in entries]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
