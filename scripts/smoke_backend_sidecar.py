#!/usr/bin/env python3
"""Smoke-test the BUILT backend sidecar: it must start and report the grades backend as available.

`GET /api/grades/status` reports `available: true` only when the sidecar could `import rusaint`,
which loads `librusaint_ffi.dylib` (`.dll` on Windows) from `_internal/rusaint/`. A sidecar built
without the native library answers `available: false`. The check touches no network: the status
route reads only the local consent file, which lives in a throwaway app-data directory, and
nothing signs in to u-SAINT.

Usage: python scripts/smoke_backend_sidecar.py [path/to/ssu-api]
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SIDECAR = ROOT / "frontend" / "src-tauri" / "resources" / "backend-sidecar" / "ssu-api" / (
    "ssu-api.exe" if sys.platform == "win32" else "ssu-api"
)
START_TIMEOUT_SECONDS = 60


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as response:  # noqa: S310 - fixed http://127.0.0.1 URL
        return json.load(response)


def main() -> int:
    sidecar = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SIDECAR
    if not sidecar.is_file():
        print(f"sidecar executable not found: {sidecar}", file=sys.stderr)
        return 1

    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as app_data, tempfile.TemporaryFile("w+b") as log:
        env = {**os.environ, "SSU_APP_DATA_DIR": app_data}
        env.pop("PYTHONPATH", None)
        process = subprocess.Popen(
            [str(sidecar), "--host", "127.0.0.1", "--port", str(port)],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + START_TIMEOUT_SECONDS
            while True:
                if process.poll() is not None:
                    return fail(f"sidecar exited early with code {process.returncode}", log)
                try:
                    get_json(f"{base}/api/health")
                    break
                except (urllib.error.URLError, OSError, ValueError):
                    if time.monotonic() > deadline:
                        return fail(f"sidecar did not answer /api/health within {START_TIMEOUT_SECONDS}s", log)
                    time.sleep(0.5)

            status = get_json(f"{base}/api/grades/status")
            print(f"/api/grades/status: available={status.get('available')!r}")
            if status.get("available") is not True:
                return fail("grades backend unavailable: rusaint or its native library is missing from the sidecar", log)
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    print("sidecar smoke OK")
    return 0


def fail(message: str, log) -> int:
    print(f"sidecar smoke FAILED: {message}", file=sys.stderr)
    log.flush()
    log.seek(0)
    print("--- sidecar output ---", file=sys.stderr)
    print(log.read().decode("utf-8", errors="replace")[-4000:], file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
