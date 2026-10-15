#!/usr/bin/env python3
"""Build the packaged Python/FastAPI backend sidecar for the Tauri app.

This creates a standalone `ssu-api` onedir bundle under
`frontend/src-tauri/resources/backend-sidecar/`:

    backend-sidecar/ssu-api/ssu-api        <- the executable Tauri launches
    backend-sidecar/ssu-api/_internal/...  <- its libraries, extracted once at build time

Tauri includes that directory as a bundle resource and prefers the executable at
runtime; development still falls back to `uv run uvicorn ...` when the sidecar has
not been built. A onedir bundle starts without the unpack-to-temp step a onefile
executable pays on every launch.
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
OUTPUT_DIR = RESOURCE_DIR / "ssu-api"
OUTPUT = OUTPUT_DIR / "ssu-api"
CONTENTS_DIRECTORY = "_internal"

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
        "--onedir",
        "--contents-directory",
        CONTENTS_DIRECTORY,
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

    built_dir = DIST_ROOT / "ssu-api"
    built_executable = built_dir / "ssu-api"
    if not built_executable.is_file():
        print(f"PyInstaller completed but output was missing: {built_executable}", file=sys.stderr)
        return 1

    # Drop whatever an earlier build left at the output path, including the legacy
    # single-file executable, so a stale binary can never shadow the new layout.
    if OUTPUT_DIR.is_symlink() or OUTPUT_DIR.is_file():
        OUTPUT_DIR.unlink()
    elif OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    shutil.copytree(built_dir, OUTPUT_DIR, symlinks=True)
    mode = OUTPUT.stat().st_mode
    OUTPUT.chmod(mode | 0o755)

    print(f"Built backend sidecar: {OUTPUT}")
    print("Next: cd frontend && npm run tauri:build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
