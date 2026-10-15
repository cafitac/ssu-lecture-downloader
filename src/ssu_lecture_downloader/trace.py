from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ssu_lecture_downloader.redaction import redact_headers, redact_mapping, redact_url


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


class NetworkTrace:
    def __init__(self) -> None:
        self._entries: list[TraceEntry] = []

    def add(self, entry: TraceEntry) -> None:
        self._entries.append(entry)

    def to_dict(self) -> dict[str, Any]:
        return {"entries": [entry.to_dict() for entry in self._entries]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
