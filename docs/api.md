# Local API

How the local FastAPI server (`127.0.0.1`) is started, how access is restricted, and which endpoints it exposes.

Run the local API manually for browser/Vite development:

```bash
uv run uvicorn ssu_lecture_downloader.api:app --host 127.0.0.1 --port 8000
```

`ssu_lecture_downloader.api:app` is the import string the desktop shell launches and detects; `ssu_lecture_downloader.service.api:app` (where the app actually lives) works too.

When running the Tauri desktop shell, the app first checks `/api/health` on `127.0.0.1:8000`. If a healthy backend is already running there, it reuses that external server. If 8000 is empty or occupied by a non-matching process, Tauri chooses a free random localhost port and starts a managed backend. Packaged (release) builds prefer the bundled `backend-sidecar/ssu-api/ssu-api` executable (a PyInstaller onedir bundle: the executable sits in a directory named `ssu-api` next to its `_internal/` folder); debug Tauri builds always use `uv run uvicorn ... --port <chosen-port>` from the repository root, even when a sidecar has been built. Tauri gives the React app that dynamic base URL through a Tauri command and terminates only its owned child process on app exit. Set `SSU_BACKEND_AUTOSTART=0` before `npm run tauri:dev` to disable autostart while debugging, or set `SSU_BACKEND_EXECUTABLE=/path/to/ssu-api` to smoke-test a custom sidecar.

API access control (all enforced by `ssu_lecture_downloader.service.security`):

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
- `GET /api/courses/{course_id}/pages/{page_url_or_id}/files?course_name=...&output_dir=...` (files linked in a wiki page: `file_id`, `display_name`, `size`, `download_url`, `downloaded_path`)
- `POST /api/download/canvas-file` (`course_id`, `file_id`, `course_name`, `output_dir`, `conflict_strategy`: saves one Canvas file into the course folder through a `.part` file, an existing file is kept with `skip`)
- `POST /api/lms/session-bridge` (LMS cookies for an in-app page window; protected like the watch session bridge)
- `POST /api/watch/chrome/start`, `GET /api/watch/chrome/status`, `POST /api/watch/chrome/next|stop|show` (auto playback queue in a dedicated Google Chrome window driven over CDP; error codes such as `chrome_not_found`, `chrome_launch_timeout`, `chrome_window_closed`)
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
- `GET /api/stt/remote/status`, `PUT /api/stt/remote/config`, `POST /api/stt/remote/check` (remote subtitle server, below)
- `POST /api/stt/jobs`
- `GET /api/stt/jobs/{job_id}`
- `POST /api/stt/jobs/{job_id}/cancel`

