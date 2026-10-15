# SSU Lecture Downloader UI Prototype

Lightweight React/Vite prototype for the local SSU lecture downloader API.

## Scope

Implemented:

- login-only credential flow backed by runtime-memory API status/save/delete endpoints (`GET /api/credentials/status`, `POST /api/credentials`, `DELETE /api/credentials`); the UI/API path ignores `.env` and does not use Keychain by default,
- current/past term list from `GET /api/terms`,
- current-term or explicit-term course list from `GET /api/courses`,
- generalized LMS content rows for the selected course from `GET /api/courses/{course_id}`,
- download planning via `POST /api/download/plan`,
- real download trigger via `POST /api/download`,
- cancellable single-download jobs via `POST /api/download/jobs`, `GET /api/download/jobs/{job_id}`, and `POST /api/download/jobs/{job_id}/cancel`,
- automatic 1.5s polling for active single/batch download jobs,
- compact job/progress summaries that avoid listing every chunk event,
- selected-content batch planning via `POST /api/download/batch/plan`,
- selected-content cancellable batch download jobs via `POST /api/download/batch/jobs`, `GET /api/download/jobs/{job_id}`, and `POST /api/download/jobs/{job_id}/cancel`,
- uncompleted-lecture batch planning via `POST /api/download/batch/plan`,
- API server supervision status in the credential panel,
- Rust-native final download jobs in Tauri runtime via Python `/api/download/native-plan` plus Rust streaming into `.part` files, Range-safe resume (a complete `.part` answered with 416 is finalized, a stale one restarts from byte 0, and an HTTP error never deletes the `.part`), connect/read-idle timeouts so cancel stays responsive, size verification before the final rename, stable `HTTP <status>` error codes, progress, cancellation, and final rename,
- one app-owned official Commons viewer window from each video row's `시청` button; Tauri manual opens bridge the Python-authenticated LMS cookies into WebKit before navigating to the validated Commons viewer URL,
- experimental `표시된 영상 순차 시청` mode in the Tauri desktop app: opens the official viewer for the currently filtered video list, injects a narrow helper that tries visible `video.play()`, listens for the real video `ended` event, closes that viewer, and advances to the next item,
- week/type/downloadability/completion filters over video, PDF/material, assignment, quiz, board, page, section, and unknown LMS rows,
- Korean-first copy that keeps older terms as an explicit future selection path.

This UI is now the React/Vite surface used by the Tauri desktop shell. Tauri remains a thin local shell: Python keeps the LMS/auth/discovery service boundary, while the shell owns native folder picking, backend supervision, downloads, and the official viewer window. The official viewer still leaves attendance/progress reporting to the 숭실/Commons player.

Important watch-mode split:

- Row-level `시청` is the default manual official viewer. In Tauri it must stay minimal: bridge the Python-authenticated LMS cookies into WebKit, then navigate to the validated Canvas module-item launch URL. Do not add autoplay scripts, debug custom-scheme navigation, `on_navigation` interception, or WebKit autoplay policy overrides to this path; that combination previously produced a blank white official-player window in WKWebView even though browser/Vite testing looked fine.
- `표시된 영상 순차 시청` is the experimental assisted path. It may inject the narrow helper that tries visible `video.play()`, listens for the real video `ended` event, closes that viewer, and advances to the next item.

Browser/Vite QA can validate React/API/session-planning behavior, but it cannot prove Tauri WKWebView cookie injection, iframe initialization, or autoplay behavior. Sequential watch mode does not call LearningX/Commons progress endpoints; it only attempts visible playback in the official player and advances when the embedded video emits `ended`. If autoplay is blocked by WebKit/browser policy, player markup changes, nested frames, or Commons-side restrictions, the mode falls back to manual user playback and will not advance until an `ended` signal is observed.

## Run

From the repository root, optionally start the backend API for browser-only development:

```bash
uv run uvicorn ssu_lecture_downloader.api:app --host 127.0.0.1 --port 8000
```

For `npm run tauri:dev`, the desktop shell starts the backend automatically (see "Backend supervision" below). It reuses a healthy external backend on `127.0.0.1:8000`; if 8000 is empty or occupied by something else, it starts its own backend on a free random localhost port and routes Tauri API calls there. Debug builds (`tauri dev`) always run `uv run uvicorn ssu_lecture_downloader.api:app` from the repository root so a stale packaged sidecar can never shadow the source; set `SSU_BACKEND_EXECUTABLE=/path/to/ssu-api` to smoke-test a sidecar from a debug build. Release builds start the bundled sidecar.

Then start the frontend:

```bash
cd frontend
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8000` for browser-only development. Tauri runtime API calls use the shell-provided dynamic backend URL instead.

## Packaged app build

Build the Python backend sidecar before creating a distributable Tauri bundle:

```bash
npm run build:backend-sidecar
npm run tauri:build
```

The sidecar build runs PyInstaller through `uv`. The sidecar is a PyInstaller `--onedir` bundle: `src-tauri/resources/backend-sidecar/ssu-api/` is a directory containing the `ssu-api` executable and `_internal/`. The whole `resources/backend-sidecar` directory is bundled as a Tauri resource and contains the Python API runtime plus packaged media dependencies such as `imageio-ffmpeg` and target-compatible `faster-whisper` wheels. A legacy single-file `backend-sidecar/ssu-api` is still accepted.

## Backend supervision

The Tauri shell owns the Python API process:

- `setup` only registers state and starts the backend on a background thread, so a slow or failing backend never blocks window creation or aborts the app. The React app polls `backend_supervision_status` (`state`: `starting` / `ready` / `failed`, plus `mode`, `error`, `log_path`) and shows a starting screen; on failure it shows the error, the backend log path, and a retry button that calls `backend_restart`.
- Startup waits up to 90 seconds while the child is alive and fails immediately when the child exits. Child stdout/stderr go to `backend.log` under the app log directory (`~/Library/Logs/com.local.ssu-lecture-downloader/` on macOS).
- The child runs in its own process group. On `RunEvent::ExitRequested`/`Exit` and on startup failures the whole group gets SIGTERM, then SIGKILL after a short grace period, so PyInstaller/uv grandchildren are not orphaned. A pidfile in the app data directory lets the next launch reap a backend left behind by a force-quit, but only when its command line matches this backend.
- A random per-launch token is passed to a managed backend as `SSU_API_TOKEN` and exposed to React through `backend_api_connection`; `src/lib/api.ts` sends it as `X-SSU-Token` on every request. External backends (port 8000) and Vite browser development use no token. Native Rust calls to the backend (health checks) send the same header.
- If the chosen random port is taken before the backend binds it, startup retries on another port (up to 3 attempts).

## Verify

```bash
npm run typecheck
npm run test:contract
npm run build
```

`test:contract` also runs Node-level behavior tests for the polling, post-process, token-header and progress helpers (`src/lib/*.ts`, loaded directly with Node's TypeScript type stripping, Node 22.18+).

Tauri-side checks run from `frontend/src-tauri`:

```bash
cargo fmt --check
cargo check
cargo test
```

The official-viewer regression stays runnable on its own:

```bash
cargo test manual_official_viewer_stays_minimal_without_assist_or_debug_hooks
```

For runtime QA, restart the Tauri app before retesting Rust/WebView changes. First confirm row-level `시청` opens the official player without a white screen. Test autoplay only through `표시된 영상 순차 시청`, then read the in-app `공식 플레이어 재생 진단 로그` if playback does not start.

Backend/API gates are run from the repository root:

```bash
uv run pytest -q
```

## Deferred

- Tauri runtime QA with real LMS credentials: login, load courses, verify filters, open bridged official viewer, confirm LMS progress/resume, download one video and one PDF/material, check progress, saved files, and Finder open.
- Apply the session bridge to sequential-watch mode if live QA shows the older assisted viewer path still lacks LMS session context.
- True bounded-parallel Rust batch downloads; current native batch jobs reuse the Rust job/progress shape but process prepared items sequentially.
- Packaged-app release QA: run `npm run build:backend-sidecar`, `npm run tauri:build`, install/open the produced `.app`, then verify it starts the bundled sidecar without user-installed `uv`/Python and can complete a small MP4/MP3/TXT job.
