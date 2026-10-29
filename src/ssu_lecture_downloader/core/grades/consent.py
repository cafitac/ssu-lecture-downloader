"""The one file this feature writes: `grades-consent.json` (schema, consent_version, granted_at).

It holds no username and no grade data. A missing, empty, malformed, or outdated file means "no
consent" (fail closed: ask again, never fetch).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from ssu_lecture_downloader.core.grades.models import GradesConsent

# Bumped when the consent copy changes, so a newer app re-asks.
CURRENT_CONSENT_VERSION = 1
CONSENT_FILE_NAME = "grades-consent.json"
_SCHEMA = 1


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class GradesConsentStore:
    def __init__(self, path: Path | Callable[[], Path], *, now: Callable[[], datetime] = _utc_now) -> None:
        self._path = path
        self._now = now

    @property
    def path(self) -> Path:
        return self._path() if callable(self._path) else self._path

    def read(self) -> GradesConsent | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        version, granted_at, schema = data.get("consent_version"), data.get("granted_at"), data.get("schema")
        if schema != _SCHEMA or isinstance(version, bool) or version != CURRENT_CONSENT_VERSION:
            return None
        if not isinstance(granted_at, str) or not granted_at:
            return None
        return GradesConsent(consent_version=CURRENT_CONSENT_VERSION, granted_at=granted_at, schema=_SCHEMA)

    def grant(self, version: int) -> GradesConsent:
        if isinstance(version, bool) or version != CURRENT_CONSENT_VERSION:
            raise ValueError("unsupported consent version")
        consent = GradesConsent(consent_version=version, granted_at=_iso(self._now()), schema=_SCHEMA)
        payload = json.dumps(
            {"schema": consent.schema, "consent_version": consent.consent_version, "granted_at": consent.granted_at}
        ).encode("utf-8")
        self._write_atomic(self.path, payload)
        return consent

    def revoke(self) -> None:
        self.path.unlink(missing_ok=True)

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        """Temp file in the same folder, flush + fsync, then `os.replace` (atomic on macOS and Windows)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".grades-consent-", suffix=".tmp", dir=path.parent)  # mkstemp opens 0o600
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise
