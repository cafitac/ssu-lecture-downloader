# ssu-lecture-downloader

Terminal-first groundwork for a local Soongsil LMS lecture discovery/downloader app.

Current implementation target: pure HTTP login/network discovery with `curl-cffi`, a reusable service layer, local FastAPI API, React/Vite UI prototype, Tauri desktop shell, native Finder download-folder picker, session-first UX, explicit term selection, cancellable auto-polled background download jobs, selected/uncompleted batch planning/download, streaming byte progress, temp-file/atomic-write downloads, retry/resume support, redacted traces, and compact CLI output plus `--json`.

## Local development

1. Copy `.env.example` to `.env` only for terminal CLI commands.
2. Fill in `SSU_USERNAME` and `SSU_PASSWORD` locally for CLI fallback. The API/UI login flow ignores `.env`, does not use Keychain by default, and keeps entered credentials only in the running backend process memory.
3. Do not commit `.env` or `.local/` artifacts.

## Commands

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

## Local API and UI prototype

Run the local API manually for browser/Vite development:

```bash
uv run uvicorn ssu_lecture_downloader.api:app --host 127.0.0.1 --port 8000
```

When running the Tauri desktop shell, the app first checks `/api/health` on `127.0.0.1:8000`. If a healthy backend is already running there, it reuses that external server. If 8000 is empty or occupied by a non-matching process, Tauri chooses a free random localhost port and starts a managed backend. Packaged (release) builds prefer the bundled `backend-sidecar/ssu-api/ssu-api` executable (a PyInstaller onedir bundle: the executable sits in a directory named `ssu-api` next to its `_internal/` folder); debug Tauri builds always use `uv run uvicorn ... --port <chosen-port>` from the repository root, even when a sidecar has been built. Tauri gives the React app that dynamic base URL through a Tauri command and terminates only its owned child process on app exit. Set `SSU_BACKEND_AUTOSTART=0` before `npm run tauri:dev` to disable autostart while debugging, or set `SSU_BACKEND_EXECUTABLE=/path/to/ssu-api` to smoke-test a custom sidecar.

API access control (all enforced by `ssu_lecture_downloader.security`):

- CORS only allows `tauri://localhost`, `http(s)://tauri.localhost`, `http://127.0.0.1:5173` and `http://localhost:5173`.
- Requests whose `Host` header is not `127.0.0.1[:port]` or `localhost[:port]` are rejected with `invalid_host` (DNS-rebinding guard).
- If the backend process is started with `SSU_API_TOKEN=<secret>`, every `/api/*` request except `GET /api/health` (and CORS preflights) must send `X-SSU-Token: <secret>`; otherwise it gets `401 {"ok": false, "error_code": "api_token_invalid"}`. Without the variable (plain `uvicorn`/Vite development) no token is enforced.
- `POST /api/media/video/ensure-playable` only accepts `.mp4` files under the default or user-chosen download folder (or files the app produced); anything else gets `400 media_path_not_allowed`.
- With no `output_dir` in a request, downloads go to `~/Downloads/SSU Lecture Downloader`.
- Unexpected failures are returned as `{"ok": false, "error_code": ...}` JSON: `network_error` (502), `network_timeout` (504), `internal_error` (500), or the LMS flow's own code. Network failures are never reported as rejected credentials.

Useful endpoints:

- `GET /api/health`
- `GET /api/credentials/status`
- `POST /api/credentials`
- `DELETE /api/credentials`
- `GET /api/terms`
- `GET /api/courses`
- `GET /api/courses?term_id=46`
- `GET /api/courses/{course_id}?course_name=...`
- `POST /api/download/plan`
- `POST /api/download`
- `POST /api/download/jobs`
- `GET /api/download/jobs/{job_id}`
- `POST /api/download/jobs/{job_id}/cancel`
- `POST /api/download/batch/plan`
- `POST /api/download/batch`
- `POST /api/media/audio/jobs`
- `GET /api/media/audio/jobs/{job_id}`
- `POST /api/media/audio/jobs/{job_id}/cancel`
- `GET /api/stt/providers/status`
- `GET /api/media/dependencies/status`
- `POST /api/stt/jobs`
- `GET /api/stt/jobs/{job_id}`
- `POST /api/stt/jobs/{job_id}/cancel`

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

### Audio extraction and STT transcripts

The desktop UI includes two related paths:

- In the main `다운로드` panel, `저장할 파일 선택` lets users multi-select `MP4`, `MP3`, and `TXT` before starting the selected lecture download. `MP4` keeps the source video, `MP3` extracts audio, and `TXT` runs STT after audio extraction. If `MP4` is unchecked, the temporary source video is removed after MP3/TXT artifacts are produced.
- The separate `후처리` card still supports a local already-downloaded video path for manual audio/STT work.

Audio extraction uses system `ffmpeg` when available, otherwise it uses the `imageio-ffmpeg` binary bundled in the Python runtime. For a distributed desktop app this must be included in the app package; end users should not have to install Homebrew or run terminal setup commands. The UI checks `/api/media/dependencies/status` and shows whether ffmpeg is available from the app bundle or the system.

STT uses packaged `faster-whisper` when available. The default is `large-v3` with Korean language forced, beam search, VAD, and a Korean lecture prompt because Korean lecture accuracy is prioritized over speed. This is the local/offline path closest to a Clova-class experience without sending lecture audio to an external API. It writes sibling transcript files:

- `.transcript.txt` for direct AI paste/upload
- `.transcript.md` with metadata and timestamps
- `.transcript.json` with structured segments

If Whisper/faster-whisper is missing, `/api/stt/providers/status`, `/api/media/dependencies/status`, and the UI return an explicit packaged-component reinstall/development-sync hint instead of silently falling back. In non-frozen development/runtime environments the UI also shows `필요한 구성요소 설치`, which calls `/api/media/dependencies/install/jobs` and installs only missing supported components through the backend (`imageio-ffmpeg` for ffmpeg fallback and `faster-whisper` for STT). Distributed frozen sidecars should still bundle target-compatible wheels up front; if the sidecar cannot safely self-modify, the app reports the reinstall path. The first model run can take longer because the Whisper model is downloaded/prepared locally.

### Official viewer QA boundary

Browser/Vite QA and Tauri app QA are intentionally different for lecture watching:

- Browser/Vite runs in the user's normal browser, so it can verify React rendering, FastAPI calls, course/detail loading, and official watch URL planning.
- Tauri opens a separate macOS WKWebView, so it must separately verify native `invoke` wiring, WebKit cookie injection, iframe/LTI initialization, and WebKit media/autoplay policy.

Do not treat Chrome browser success as proof that the Tauri official viewer or autoplay works. The row-level `시청` button is deliberately the minimal official flow: create one `official-viewer` window, set the Python-authenticated LMS cookies in WebKit, then navigate to the validated Canvas module-item launch URL. It must not inject autoplay helpers, debug custom-scheme navigation, `on_navigation` intercepts, or WebKit autoplay policy overrides; those previously caused a blank white official-player window. Autoplay and ended-event capture belong only to the experimental `표시된 영상 순차 시청` assisted path.

Tauri official-viewer regression checks:

```bash
cd frontend/src-tauri
cargo fmt --check
cargo check
cargo test manual_official_viewer_stays_minimal_without_assist_or_debug_hooks
cargo test
cd ..
npm run test:contract
npm run build
```

Manual runtime QA after those gates: restart the Tauri app, use a row-level `시청` button to confirm the official player opens without a white screen, then use `표시된 영상 순차 시청` if testing best-effort autoplay. If autoplay still fails there, inspect the in-app `공식 플레이어 재생 진단 로그`; common causes are no detected video/iframe yet, WebKit `NotAllowedError`, or Commons intro-video-to-main-video handoff.

## Architecture note

The CLI commands call `ssu_lecture_downloader.service.SsuLectureService`, which owns reusable login/course-discovery/course-detail/media-resolution/download orchestration. The local FastAPI app and React/Vite prototype sit on top of that service layer; future desktop UI should reuse the same boundary instead of duplicating CLI internals. API/UI credential access goes through a writable in-memory runtime store: login values are accepted only through `POST /api/credentials`, ignored from `.env`, not stored in Keychain by default, and cleared by `DELETE /api/credentials` or backend process exit. API/UI responses expose only presence/source booleans, never credential values. In the API process, the service keeps in-memory authenticated session and TTL list-result caches per credential fingerprint so repeated term/course/detail lookups avoid full login and duplicate term scans; these caches are never written to disk and are cleared on credential save/delete.

## Download engine boundary

The current byte writer is Python (`download_to_plan`) and uses the same authenticated `HttpClient` session that resolved the Commons URL. It now streams with `curl-cffi` `stream=True`, writes to `.part`, atomically renames on success, emits byte-progress events, retries interrupted streams, and uses `Range` to resume partial temp files when Commons returns `206 Partial Content`. Tauri uses Rust native transfer jobs for the desktop primary download path after Python returns a native plan. Python remains responsible for LMS login, course/media resolution, credentials, and trace redaction; Rust is responsible only for final byte streaming in the app path.

Recommended Rust module contract:

- input: already-resolved direct download URL, output path, optional `Referer`, timeout, and redacted display metadata;
- output: final file path, bytes written, elapsed time, and progress events;
- responsibilities: streaming write, temp-file + atomic rename, retry/resume/range support if supported by Commons, cancellation safety;
- non-responsibilities: LMS login, cookies, course discovery, Commons metadata resolution, credential handling, or trace redaction.

This keeps the risky authenticated LMS flow in the tested Python service and makes Rust an optional acceleration module for file transfer only.

## Verification

```bash
uv run pytest -q
cd frontend && npm run test:contract && npm run build
cd frontend/src-tauri && cargo test
```
