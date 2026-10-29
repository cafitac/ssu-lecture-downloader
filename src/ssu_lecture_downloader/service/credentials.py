from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from ssu_lecture_downloader.core.credentials import CredentialPair, CredentialStore

__all__ = [
    "CompositeCredentialStore",
    "CredentialPair",
    "CredentialStore",
    "EnvCredentialStore",
    "InMemoryCredentialStore",
    "default_credential_store",
]


class EnvCredentialStore:
    def __init__(self, dotenv_path: Path | None = None) -> None:
        self.dotenv_path = dotenv_path or (Path.cwd() / ".env")

    def get(self) -> CredentialPair | None:
        load_dotenv(dotenv_path=self.dotenv_path)
        username = os.getenv("SSU_USERNAME")
        password = os.getenv("SSU_PASSWORD")
        if not username or not password:
            return None
        return CredentialPair(username=username, password=password)

    def status(self) -> dict[str, object]:
        load_dotenv(dotenv_path=self.dotenv_path)
        return {
            "source": "env",
            "username_present": bool(os.getenv("SSU_USERNAME")),
            "password_present": bool(os.getenv("SSU_PASSWORD")),
            "can_write": False,
        }

    def save(self, pair: CredentialPair) -> None:
        raise RuntimeError("env credential store is read-only")

    def delete(self) -> None:
        raise RuntimeError("env credential store is read-only")


class InMemoryCredentialStore:
    def __init__(self) -> None:
        self._pair: CredentialPair | None = None

    def get(self) -> CredentialPair | None:
        return self._pair

    def status(self) -> dict[str, object]:
        return {
            "source": "memory",
            "username_present": self._pair is not None,
            "password_present": self._pair is not None,
            "can_write": True,
        }

    def save(self, pair: CredentialPair) -> None:
        self._pair = pair

    def delete(self) -> None:
        self._pair = None


class CompositeCredentialStore:
    def __init__(self, primary: CredentialStore, fallback: CredentialStore) -> None:
        self.primary = primary
        self.fallback = fallback

    def get(self) -> CredentialPair | None:
        return self.primary.get() or self.fallback.get()

    def status(self) -> dict[str, object]:
        primary_status = self.primary.status()
        if primary_status.get("username_present") and primary_status.get("password_present"):
            return primary_status
        fallback_status = self.fallback.status()
        if fallback_status.get("username_present") or fallback_status.get("password_present"):
            return {**fallback_status, "can_write": bool(primary_status.get("can_write")) or bool(fallback_status.get("can_write"))}
        return primary_status

    def save(self, pair: CredentialPair) -> None:
        self.primary.save(pair)

    def delete(self) -> None:
        self.primary.delete()


def default_credential_store() -> CredentialStore:
    return InMemoryCredentialStore()
