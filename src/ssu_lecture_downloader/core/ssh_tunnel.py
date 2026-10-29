"""A managed `ssh -L` port forward to the remote STT server.

The remote server only listens on the remote machine's loopback, so the app opens an
SSH tunnel on demand, reuses it while it is alive and closes it when the backend exits.
`ssh` is always started with an argument list (never through a shell) and the host
alias is validated so it can never be read as an option.
"""

from __future__ import annotations

import atexit
import os
import re
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from typing import IO

SSH_FALLBACK_PATH = '/usr/bin/ssh'
TUNNEL_READY_TIMEOUT_SECONDS = 20.0
TUNNEL_POLL_SECONDS = 0.1

# An ssh_config alias or a hostname, optionally `user@`. Must start alphanumerically so it
# cannot be an option; no spaces, quotes or shell metacharacters.
_HOST_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._@:-]{0,252}$')


class TunnelError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_valid_ssh_host(host: object) -> bool:
    return isinstance(host, str) and bool(_HOST_PATTERN.match(host)) and '..' not in host


def is_valid_port(port: object) -> bool:
    return isinstance(port, int) and not isinstance(port, bool) and 0 < port < 65536


def build_ssh_command(ssh: str, host: str, remote_port: int, local_port: int) -> list[str]:
    """The exact argv for the forward; raises ValueError for an unsafe host or a bad port."""
    if not is_valid_ssh_host(host):
        raise ValueError('invalid_ssh_host')
    if not is_valid_port(remote_port) or not is_valid_port(local_port):
        raise ValueError('invalid_port')
    return [
        ssh,
        '-N',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ServerAliveInterval=30',
        # No terminal is attached: fail instead of waiting for a password or host-key prompt.
        '-o', 'BatchMode=yes',
        '-o', 'ConnectTimeout=10',
        '-L', f'{local_port}:127.0.0.1:{remote_port}',
        host,
    ]


def find_ssh() -> str | None:
    found = shutil.which('ssh')
    if found:
        return found
    return SSH_FALLBACK_PATH if os.path.exists(SSH_FALLBACK_PATH) else None


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1', 0))
        return int(sock.getsockname()[1])


def port_is_listening(port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=timeout):
            return True
    except OSError:
        return False


class SshTunnel:
    """At most one managed forward; `ensure` starts it when needed and reuses a live one."""

    def __init__(
        self,
        *,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        is_listening: Callable[[int], bool] = port_is_listening,
        free_port: Callable[[], int] = pick_free_port,
        ssh_path: Callable[[], str | None] = find_ssh,
        ready_timeout: float = TUNNEL_READY_TIMEOUT_SECONDS,
        register_atexit: bool = True,
    ) -> None:
        self._popen = popen
        self._is_listening = is_listening
        self._free_port = free_port
        self._ssh_path = ssh_path
        self._ready_timeout = ready_timeout
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._stderr: IO[bytes] | None = None
        self._key: tuple[str, int, int] | None = None
        self._auto_port: int | None = None
        self._external = False
        if register_atexit:
            atexit.register(self.close)

    # ------------------------------------------------------------------ state

    def running(self) -> bool:
        with self._lock:
            return self._external_alive() or (self._process is not None and self._process.poll() is None)

    def active_port(self) -> int | None:
        with self._lock:
            return self._key[2] if self._key and self.running() else None

    def _external_alive(self) -> bool:
        return self._external and self._key is not None and self._is_listening(self._key[2])

    # ------------------------------------------------------------------- open

    def ensure(self, host: str, remote_port: int, local_port: int | None) -> int:
        """The local port that forwards to `host`'s `remote_port`, opening the tunnel if needed."""
        if not is_valid_ssh_host(host):
            raise TunnelError('invalid_ssh_host', 'SSH 호스트 이름이 올바르지 않아요.')
        if not is_valid_port(remote_port) or (local_port is not None and not is_valid_port(local_port)):
            raise TunnelError('invalid_port', '포트 번호가 올바르지 않아요.')
        with self._lock:
            port = local_port if local_port is not None else (self._auto_port or self._free_port())
            key = (host, remote_port, port)
            if self._key == key and self.running():
                return port
            self.close()
            if local_port is not None and self._is_listening(port):
                # Something (typically a tunnel the user opened by hand) already forwards this port.
                self._key = key
                self._external = True
                return port
            if local_port is None:
                self._auto_port = port
            ssh = self._ssh_path()
            if not ssh:
                raise TunnelError('ssh_not_found', 'ssh 실행 파일을 찾지 못했어요.')
            command = build_ssh_command(ssh, host, remote_port, port)
            stderr = tempfile.TemporaryFile()
            try:
                process = self._popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr)
            except OSError as exc:
                stderr.close()
                raise TunnelError('ssh_start_failed', f'ssh를 시작하지 못했어요: {exc}') from exc
            self._process, self._stderr, self._key, self._external = process, stderr, key, False
            self._wait_until_ready(process, port)
            return port

    def _wait_until_ready(self, process: subprocess.Popen, port: int) -> None:
        deadline = time.monotonic() + self._ready_timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                detail = self._stderr_tail()
                self.close()
                raise TunnelError('ssh_tunnel_failed', f'SSH 터널을 열지 못했어요. {detail}'.strip())
            if self._is_listening(port):
                return
            time.sleep(TUNNEL_POLL_SECONDS)
        self.close()
        raise TunnelError('ssh_tunnel_timeout', 'SSH 터널이 시간 안에 열리지 않았어요. 호스트와 네트워크를 확인해 주세요.')

    def _stderr_tail(self) -> str:
        stream = self._stderr
        if stream is None:
            return ''
        try:
            stream.seek(0)
            text = stream.read().decode('utf-8', 'replace').strip()
        except (OSError, ValueError):
            return ''
        lines = [line for line in text.splitlines() if line.strip()]
        return ' '.join(lines[-2:])[:300]

    # ------------------------------------------------------------------ close

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
            stderr, self._stderr = self._stderr, None
            self._key = None
            self._external = False
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=3)
                except Exception:
                    pass
        if stderr is not None:
            try:
                stderr.close()
            except OSError:
                pass
