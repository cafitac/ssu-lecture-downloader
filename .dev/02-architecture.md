# Architecture Draft

Status: draft

## Product shape

The final product should be a local desktop study utility with a clean UI and a fast resolver core.

Recommended architecture:

```text
Desktop UI
  Electron/Tauri renderer
  course/week/lecture/download/transcript screens
        |
        v
Local app backend / bridge
  starts and talks to Python core, or calls a local HTTP API
        |
        v
Python core library
  auth/session, LMS listing, resolver, downloader, transcript prep
        |
        v
LMS/Canvas/Commons resources
  curl-cffi fast lane + Playwright fallback
```

## Why keep Python core separate

- Easier to test LMS parsing/resolution without UI.
- Easier to use `curl-cffi`, Playwright Python, requests-style downloads, ffmpeg/Whisper later.
- UI can be replaced later without rewriting scraping/downloading logic.
- CLI/local API smoke tests can verify behavior before desktop packaging.

## Desktop shell choice

### Electron

Pros:

- Mature desktop packaging.
- Easy React/Vite UI.
- Good Node bridge for starting Python subprocess/local API.
- Familiar pattern for download queues and desktop file dialogs.

Cons:

- Heavier runtime.
- Need careful secret handling between renderer/main process.

### Tauri

Pros:

- Lighter app.
- Rust shell can be safer for filesystem/IPC.

Cons:

- More setup complexity.
- Python sidecar flow needs more planning.

Recommendation for first UI MVP: Electron + React/Vite, unless packaging size becomes a priority. Keep the Python core independent so switching to Tauri later is possible.

## Backend/core modules

Potential Python package: `ssu_lecture_downloader`

Suggested modules:

- `models.py`
  - `CredentialInput`
  - `SessionState`
  - `Course`
  - `CourseWeek` / `Module`
  - `LectureItem`
  - `MediaCandidate`
  - `DownloadJob`
  - `TranscriptJob`
- `auth.py`
  - pure `curl-cffi` login/session handling first
  - Playwright fallback/session export only after a concrete HTTP blocker is documented
- `learningx.py`
  - course/module/week/lecture listing
- `resolver.py`
  - common resolver interface
- `resolver_http.py`
  - fast `curl-cffi` resolver
- `resolver_playwright.py`
  - fallback network/iframe resolver
- `media_url.py`
  - candidate extraction/classification/validation
- `download.py`
  - streaming download, resume, progress
- `library.py`
  - local metadata DB or JSON store for downloaded lectures
- `transcript.py`
  - transcript prep/job model; actual model integration later
- `redaction.py`
  - log/event redaction
- `api.py` or `server.py`
  - optional local HTTP/WebSocket bridge for UI

## UI screens

### Login screen

- LMS ID/password fields.
- Optional “use saved local session” state.
- Clear copy that credentials stay local.
- No raw cookie/token display.

### Dashboard

- Authenticated user/session summary.
- Current semester/course list.
- Sync button.
- Error panel with redacted diagnostics.

### Course detail

- Week/module tree.
- Lecture rows:
  - title,
  - week/module,
  - duration,
  - watched/unwatched/completion status,
  - media resolved/not resolved,
  - downloaded/not downloaded,
  - transcript ready/not ready.

### Lecture detail

- Metadata.
- Resolve source button.
- Download button.
- Open local file button after download.
- Transcript prep/extract button placeholder.
- Redacted debug drawer.

### Download queue

- Current downloads.
- Progress/resume/cancel/retry.
- Destination folder.
- Completed library.

### Transcript prep

- Downloaded video list.
- Audio extraction status.
- Transcript job state.
- Later model/provider selection.

## Data storage

Local-only storage options:

- SQLite for metadata:
  - courses,
  - lecture items,
  - media candidates,
  - download jobs,
  - transcript jobs.
- Filesystem for media:
  - `~/Movies/SSU Lectures` or user-configured directory.
- Local app config:
  - no plaintext password by default.
  - session cookies stored only if user opts in, preferably OS keychain or encrypted app storage later.

First MVP can start with JSON/SQLite, but metadata should be structured from the beginning.

## IPC/API boundary

Recommended first bridge:

- Python local API process exposing localhost-only endpoints, or
- Electron main process invokes Python CLI/core commands.

For UI responsiveness, a local API/WebSocket or stdio event stream is better for download progress.

Candidate local API endpoints:

- `POST /auth/login`
- `GET /session`
- `POST /sync/courses`
- `GET /courses`
- `GET /courses/{course_id}`
- `POST /lectures/{lecture_id}/resolve`
- `POST /lectures/{lecture_id}/download`
- `GET /downloads`
- `POST /downloads/{job_id}/cancel`
- `POST /lectures/{lecture_id}/prepare-transcript`

## Security/redaction rules

Never send or show raw secrets in the renderer unless absolutely necessary.

Sensitive values:

- password,
- cookies,
- `xn_api_token`,
- bearer tokens,
- signed URLs/query params,
- raw request/response headers,
- account identifiers when not needed.

Debug logs should use stable redaction:

- host/path can remain when useful,
- query strings with tokens should be removed or replaced,
- auth headers must become `[REDACTED]`,
- cookies must become `[REDACTED]`.

## Testing strategy

Start with core tests before UI:

- parser tests using sanitized fixtures,
- URL classifier tests,
- filename/path safety tests,
- redaction tests,
- session/request builder tests without real credentials,
- downloader tests using a local test HTTP server.

Then UI tests:

- component/source tests for screen states,
- Electron/Playwright smoke only after local API exists.

## Key architecture risk

If LMS login requires browser execution, do not hide that assumption. First capture the precise pure-HTTP failure class and redacted trace. Only then add a Playwright fallback for the blocked step while keeping listing/resolution/download in `curl-cffi` wherever possible.
