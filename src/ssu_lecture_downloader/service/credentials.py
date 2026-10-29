from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from dotenv import load_dotenv


@dataclass(frozen=True)
class CredentialPair:
    username: str
    password: str


class CredentialStore(Protocol):
    def get(self) -> CredentialPair | None: ...
    def status(self) -> dict[str, object]: ...
    def save(self, pair: CredentialPair) -> None: ...
    def delete(self) -> None: ...


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


class MacOSKeychainCredentialStore:
    def __init__(self, *, service_name: str = "ssu-lecture-downloader", runner=None) -> None:
        self.service_name = service_name
        self.runner = runner or self._run_security

    @staticmethod
    def _run_security(args: list[str]) -> str:
        completed = subprocess.run(args, check=True, text=True, capture_output=True)
        return completed.stdout.strip()

    def _find(self, account: str) -> str | None:
        try:
            value = self.runner(["security", "find-generic-password", "-w", "-s", self.service_name, "-a", account])
        except Exception:
            return None
        value = str(value).strip()
        return value or None

    def get(self) -> CredentialPair | None:
        username = self._find("SSU_USERNAME")
        password = self._find("SSU_PASSWORD")
        if not username or not password:
            return None
        return CredentialPair(username=username, password=password)

    def status(self) -> dict[str, object]:
        return {
            "source": "keychain",
            "username_present": self._find("SSU_USERNAME") is not None,
            "password_present": self._find("SSU_PASSWORD") is not None,
            "can_write": True,
        }

    def save(self, pair: CredentialPair) -> None:
        self._save_value("SSU_USERNAME", pair.username)
        self._save_value("SSU_PASSWORD", pair.password)

    def _save_value(self, account: str, value: str) -> None:
        self.runner([
            "security",
            "add-generic-password",
            "-U",
            "-s",
            self.service_name,
            "-a",
            account,
            "-w",
            value,
        ])

    def delete(self) -> None:
        self._delete_value("SSU_USERNAME")
        self._delete_value("SSU_PASSWORD")

    def _delete_value(self, account: str) -> None:
        try:
            self.runner(["security", "delete-generic-password", "-s", self.service_name, "-a", account])
        except Exception:
            pass


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
