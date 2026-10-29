"""LMS credential value types shared by the stores in `service` and the OS-specific adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CredentialPair:
    username: str
    password: str


class CredentialStore(Protocol):
    def get(self) -> CredentialPair | None: ...
    def status(self) -> dict[str, object]: ...
    def save(self, pair: CredentialPair) -> None: ...
    def delete(self) -> None: ...
