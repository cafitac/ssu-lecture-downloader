#!/usr/bin/env python3
"""Build the packaged Python/FastAPI backend sidecar for the Tauri app.

This creates a standalone `ssu-api` executable under
`frontend/src-tauri/resources/backend-sidecar/`. Tauri includes that directory as a
bundle resource and prefers the executable at runtime; development still falls
back to `uv run uvicorn ...` when the sidecar has not been built.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "ssu_api_sidecar.py"
BUILD_ROOT = ROOT / ".local" / "pyinstaller" / "ssu-api"
DIST_ROOT = ROOT / ".local" / "pyinstaller" / "dist"
RESOURCE_DIR = ROOT / "frontend" / "src-tauri" / "resources" / "backend-sidecar"
OUTPUT = RESOURCE_DIR / "ssu-api"

HIDDEN_IMPORTS = [
    # PyInstaller can miss dynamically imported runtime providers.
    "imageio_ffmpeg",
    "faster_whisper",
    "ctranslate2",
    "onnxruntime",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]


def run(command: list[str]) -> None:
    print("+", " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> int:
    if not ENTRYPOINT.exists():
        print(f"missing entrypoint: {ENTRYPOINT}", file=sys.stderr)
        return 1

    RESOURCE_DIR.mkdir(parents=True, exist_ok=True)
    if BUILD_ROOT.exists():
        shutil.rmtree(BUILD_ROOT)
    if DIST_ROOT.exists():
        shutil.rmtree(DIST_ROOT)

    command = [
        "uv",
        "run",
        "--with",
        "pyinstaller",
        "pyinstaller",
        "--clean",
        "--noconfirm",
        "--onefile",
        "--name",
        "ssu-api",
        "--distpath",
        str(DIST_ROOT),
        "--workpath",
        str(BUILD_ROOT),
        "--specpath",
        str(BUILD_ROOT),
    ]
    for module in HIDDEN_IMPORTS:
        command.extend(["--hidden-import", module])
    command.append(str(ENTRYPOINT))

    run(command)

    built = DIST_ROOT / "ssu-api"
    if not built.exists():
        print(f"PyInstaller completed but output was missing: {built}", file=sys.stderr)
        return 1

    if OUTPUT.exists():
        OUTPUT.unlink()
    shutil.copy2(built, OUTPUT)
    mode = OUTPUT.stat().st_mode
    OUTPUT.chmod(mode | 0o755)

    print(f"Built backend sidecar: {OUTPUT}")
    print("Next: cd frontend && npm run tauri:build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
