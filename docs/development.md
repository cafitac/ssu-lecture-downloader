# Development guide

How to run and verify the backend, frontend and desktop app locally. Architecture is in [architecture.md](architecture.md), the API in [api.md](api.md).

## Local setup

1. Copy `.env.example` to `.env` only for terminal CLI commands.
2. Fill in `SSU_USERNAME` and `SSU_PASSWORD` locally for CLI fallback. The API/UI login flow ignores `.env`, does not use Keychain by default, and keeps entered credentials only in the running backend process memory.
3. Do not commit `.env` or `.local/` artifacts.

## CLI commands

Login only:

```bash
uv run ssu-lecture-downloader login --trace .local/traces/login.json
uv run ssu-lecture-downloader login --trace .local/traces/login.json --json
```

Login and summarize LearningX courses/modules/lecture-like items:

```bash
uv run ssu-lecture-downloader discover-lms --trace .local/traces/discover-lms.json
uv run ssu-lecture-downloader discover-lms --trace .local/traces/discover-lms.json --json
```

`discover-lms` now defaults to the latest active/current term by scanning LearningX term ids and selecting courses where `ended=False`. Use repeated `--term-id` flags only when you intentionally want to inspect older completed terms, for example `--term-id 43 --term-id 40`.

Login and list lecture rows for one course so a UI/user can choose lectures without manually discovering raw module item ids:

```bash
uv run ssu-lecture-downloader course-detail --course-id 44070 --course-name 'UX방법론' --json
```

Resolve one Canvas module item to Commons download metadata without downloading bytes:

```bash
uv run ssu-lecture-downloader resolve-media --course-id 42045 --module-item-id 3206622 --trace .local/traces/resolve-media.json --json
```

This performs login, launches the LTI context, fetches LearningX/Commons metadata, and performs a HEAD request against the Commons download URL.

Plan a download path without writing bytes:

```bash
uv run ssu-lecture-downloader download --course-id 42045 --module-item-id 3206622 --course-name '데이터사이언스' --output-dir .local/downloads --dry-run --json
```

Download a lecture video to a selected output directory:

```bash
uv run ssu-lecture-downloader download --course-id 42045 --module-item-id 3206622 --course-name '데이터사이언스' --output-dir .local/downloads --trace .local/traces/download-live.json --json
```

`download` creates a safe course folder and filename, avoids overwriting existing files by adding numbered suffixes, streams bytes through a `.part` temp file, atomically renames the temp file only after success, reports byte-progress events, and resumes partial files with `Range` when Commons supports it. If a server ignores `Range`, the partial file is discarded and the download restarts from byte 0.

Plan selected lectures without writing bytes:

```bash
uv run ssu-lecture-downloader batch-download --course-id 44070 --course-name 'UX방법론' --module-item-id 3268578 --output-dir .local/downloads --dry-run --json
```

Plan or download all currently uncompleted lectures in a course:

```bash
uv run ssu-lecture-downloader batch-download --course-id 44070 --course-name 'UX방법론' --uncompleted --output-dir .local/downloads --dry-run --json
uv run ssu-lecture-downloader batch-download --course-id 44070 --course-name 'UX방법론' --uncompleted --output-dir .local/downloads --json
```

`batch-download` emits progress events (`resolving`, `planned`, `downloading`, `download_started`, `download_progress`, `download_done`, `done`, `failed`, `download_retrying`) in JSON and compact progress lines in human output. Repeat `--module-item-id` for multi-select downloads.

## Frontend and desktop app

Run the React/Vite prototype:

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8000` for browser-only development. Inside Tauri, API calls use the dynamic backend base URL exposed by the desktop shell, so Tauri-managed backend ports do not have to be 8000. The prototype currently shows a session-first login/status panel, current-term courses, explicit current/past term selection from `/api/terms`, lecture rows, API server supervision status, automatic download folder default, native Finder folder picker in Tauri, primary background single-download start/cancel with 1.5s auto-polling, advanced path preview/diagnostic actions, selected-lecture batch planning/download, and uncompleted-lecture batch planning.

Run the Tauri desktop shell for app QA:

```bash
cd frontend
npm run tauri:dev
```

Tauri now manages the Python API automatically for desktop QA: it reuses an existing healthy `127.0.0.1:8000` backend when present, otherwise starts the backend on a free random localhost port and cleans up that owned child process when the app exits. In development, the fallback command is still `uv run uvicorn ...`; for distributable builds, build the bundled sidecar first:

```bash
cd frontend
npm run build:backend-sidecar
npm run tauri:build
```

`build:backend-sidecar` runs PyInstaller (`--onedir`, so there is no unpack-to-temp step on every launch) from the repo root and writes `frontend/src-tauri/resources/backend-sidecar/ssu-api/ssu-api` plus its `_internal/` directory, which are included as a Tauri bundle resource. A leftover single-file `backend-sidecar/ssu-api` from older builds is removed by the script. The sidecar contains the Python runtime/backend and packaged media dependencies such as `imageio-ffmpeg`; `faster-whisper` is bundled when target-compatible wheels are available for the build machine/target.

## Verification

```bash
uv run pytest -q
cd frontend && npm run test:contract && npm run build
cd frontend/src-tauri && cargo test
```

### CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every pull request to `develop` and every push
to `develop`, on an Intel macOS runner (`macos-15-intel`, same architecture as the installed app):
`uv run pytest`, `uv run lint-imports`, `npm run typecheck`, `npm run test:contract`, the backend sidecar build,
`cargo test` and `npm run tauri build -- --bundles app` (no dmg, signing or upload).
`tests/test_macos_invariants.py` and `frontend/src-tauri/src/invariant_tests.rs` pin the macOS
behavior (app-data paths, LaunchAgent, Chrome argv, sidecar location, local-STT gate) that
platform-split changes must keep identical.
