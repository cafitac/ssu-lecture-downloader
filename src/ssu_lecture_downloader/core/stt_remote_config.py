"""Persisted settings of the remote subtitle (STT) server.

Stored as a JSON file in the app data folder, written through the injected private-file writer
(owner-only because it may hold an API key).
The key is write-only: `public_view` only says whether one is set and nothing else ever
returns or logs it.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel

from ssu_lecture_downloader.core.ssh_tunnel import is_valid_port, is_valid_ssh_host

RemoteKind = Literal['whisper_cpp_server', 'openai_compatible']
REMOTE_KINDS = ('whisper_cpp_server', 'openai_compatible')
DEFAULT_OPENAI_MODEL = 'whisper-1'


class ConfigError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class TunnelConfig(BaseModel):
    host: str
    remote_port: int
    local_port: int | None = None


class RemoteSTTConfig(BaseModel):
    enabled: bool = False
    kind: RemoteKind = 'whisper_cpp_server'
    base_url: str = ''
    model: str = ''
    api_key: str = ''
    tunnel: TunnelConfig | None = None

    def public_view(self) -> dict[str, Any]:
        """Everything the UI may see; the API key is reduced to a boolean."""
        return {
            'enabled': self.enabled,
            'kind': self.kind,
            'base_url': self.base_url,
            'model': self.model,
            'api_key_set': bool(self.api_key),
            'tunnel': self.tunnel.model_dump() if self.tunnel else None,
        }


def validate_base_url(value: str) -> str:
    text = value.strip().rstrip('/')
    parts = urlsplit(text)
    if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ConfigError('invalid_stt_base_url')
    try:
        parts.port  # noqa: B018 - raises ValueError for a malformed port
    except ValueError as exc:
        raise ConfigError('invalid_stt_base_url') from exc
    return text


def validate_tunnel(raw: Any) -> TunnelConfig | None:
    if raw is None:
        return None
    data = raw.model_dump() if isinstance(raw, BaseModel) else dict(raw)
    host = str(data.get('host') or '').strip()
    if not host:
        return None
    if not is_valid_ssh_host(host):
        raise ConfigError('invalid_ssh_host')
    remote_port = data.get('remote_port')
    local_port = data.get('local_port')
    if not is_valid_port(remote_port) or (local_port is not None and not is_valid_port(local_port)):
        raise ConfigError('invalid_port')
    return TunnelConfig(host=host, remote_port=remote_port, local_port=local_port)


def apply_patch(current: RemoteSTTConfig, patch: dict[str, Any]) -> RemoteSTTConfig:
    """`current` with the keys present in `patch` replaced (validated). `api_key`: '' clears it, absent keeps it."""
    data = current.model_dump()
    if 'enabled' in patch and patch['enabled'] is not None:
        data['enabled'] = bool(patch['enabled'])
    if 'kind' in patch and patch['kind'] is not None:
        if patch['kind'] not in REMOTE_KINDS:
            raise ConfigError('invalid_stt_kind')
        data['kind'] = patch['kind']
    if 'tunnel' in patch:
        tunnel = validate_tunnel(patch['tunnel'])
        data['tunnel'] = tunnel.model_dump() if tunnel else None
    if 'base_url' in patch and patch['base_url'] is not None:
        text = str(patch['base_url']).strip()
        data['base_url'] = validate_base_url(text) if text else ''
    if 'model' in patch and patch['model'] is not None:
        data['model'] = str(patch['model']).strip()[:200]
    if 'api_key' in patch and patch['api_key'] is not None:
        data['api_key'] = str(patch['api_key']).strip()
    config = RemoteSTTConfig(**data)
    if config.enabled and not config.tunnel and not config.base_url:
        raise ConfigError('missing_stt_base_url')
    return config


class RemoteSTTSettingsStore:
    """`writer` is a SecretFileWriter and `path_provider` yields the default path (AppPaths.stt_config_path)."""

    def __init__(self, path: Path | None = None, *, writer: Any, path_provider: Callable[[], Path] | None = None) -> None:
        self._path = path
        self._path_provider = path_provider
        self._writer = writer
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        if self._path is not None:
            return self._path
        if self._path_provider is None:
            raise ValueError('RemoteSTTSettingsStore needs path or path_provider')
        return self._path_provider()

    def load(self) -> RemoteSTTConfig:
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                return RemoteSTTConfig(**raw)
            except (OSError, ValueError, TypeError):
                return RemoteSTTConfig()

    def save(self, config: RemoteSTTConfig) -> None:
        with self._lock:
            path = self.path
            path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(config.model_dump(), ensure_ascii=False, indent=2)
            self._writer.write_private(path, text.encode('utf-8'))
