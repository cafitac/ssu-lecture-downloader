# Roadmap

## Priority 1 — Tauri-managed Python backend (done)

Make the desktop app self-contained during development/runtime by supervising the FastAPI process from Tauri.

Implemented behavior:
- Reuse an already-running backend on `127.0.0.1:8000` only when `/api/health` returns the expected app response.
- If 8000 is empty or occupied by another process, choose a free random localhost port and spawn `uv run uvicorn ssu_lecture_downloader.api:app --host 127.0.0.1 --port <chosen-port>` from the repository root.
- Expose the dynamic backend base URL to React through a Tauri command; browser-only Vite development still uses the `/api -> 127.0.0.1:8000` proxy.
- Wait for `/api/health` before treating the app as ready.
- Kill only the child process that Tauri started; never kill a pre-existing user/server process.

## Priority 2 — LMS-like week content inventory and filtered downloads (done)

Documented in `.dev/10-lms-content-inventory.md` after live LMS inspection.

Implemented behavior:
- Replaced the video-only course detail surface with generalized week/module content rows via `CourseContentItem` and `content_items`.
- Preserved LMS-visible rows for `attendance_item`, `assignment`, `quiz`, `board`, `wiki_page`, `sub_header`, and unknown rows.
- Kept legacy `lectures` in the API as a video-only compatibility surface.
- Normalized downloadable media/material rows:
  - video/movie rows expose duration in raw seconds and display as formatted duration in the UI,
  - PDF/material rows expose file size metadata and can resolve through the same Commons path as videos.
- Added frontend filters by week/module, type, downloadability, and completion state.
- Bulk plan/download uses selected downloadable generalized content rows, starting with videos and PDFs.

Next priority:
- Finish Rust-native batch downloads with true bounded parallelism before claiming native speed improvements.
- Then run Tauri packaging/runtime QA against the real app flow: `npm run tauri:dev`, login, load courses, verify week/type/completion filters, download one video and one PDF/material, check progress and Finder open.

## Priority 3 — Rust native final-download engine (in progress)

Documented in `.dev/11-rust-native-download-engine.md`.

Implemented behavior:
- Python exposes `/api/download/native-plan` for Tauri to resolve raw Commons transfer data while still keeping LMS login, metadata, safe paths, and conflict preflight in Python.
- Tauri exposes native download job commands and streams resolved files with Rust `reqwest` into `.part` files before final rename.
- React routes Tauri runtime download jobs through the native Rust job registry; browser/Vite development remains on the existing FastAPI jobs.

Remaining:
- Convert native batch from sequential execution to bounded parallel Rust workers.
- Add an integration-style Rust test around a local HTTP fixture for `.part` resume/cancel behavior.
- Runtime QA with real SSU/LearningX downloads is still deferred.

## Priority 4 — Official viewer session bridge (implemented; live QA pending)

Documented in `.dev/official-viewer-session-bridge.md`.

Implemented behavior:
- Python exposes `/api/watch/session-bridge-plan`, which returns the resolved official Commons viewer URL, a Canvas module-item `launch_url`, and a local-only cookie bridge payload for Tauri.
- Diagnostics include only cookie names/domains/count and redacted HTTP trace data; cookie values are transported only to the local Tauri command for WebKit injection.
- Tauri command `open_official_viewer_with_session` creates the viewer at a bootstrap HTTPS URL, injects cookies with `WebviewWindow::set_cookie`, installs the watch-assist initialization script, then navigates to the validated Canvas module item URL so the flow is closer to clicking the lecture inside LMS while still attempting autoplay.
- React uses this bridge for manual `시청` opens in Tauri runtime while browser/Vite development keeps the old plain viewer-plan path.

Remaining:
- Live runtime QA: login, open a lecture with `시청`, confirm the bridged official viewer loads, then verify progress/resume in normal LMS.
- Sequential-watch mode still uses the older assisted viewer path; bridge it if live QA shows session context is missing there too.

## Priority 5 — Audio extraction and STT transcript pipeline (implemented; live QA pending)

Plan document: `.hermes/plans/2026-05-31_160210-audio-stt-pipeline.md`.

Implemented behavior:
- Adds a post-download pipeline that treats downloaded `.mp4`/video files as the input, instead of depending on official-player autoplay.
- Backend can extract `.wav`, `.m4a`, or `.mp3` audio with system `ffmpeg` through `/api/media/audio/jobs` and status/cancel endpoints.
- Backend exposes STT provider status at `/api/stt/providers/status`; when Whisper/faster-whisper is missing, the API returns a Korean install hint instead of failing silently.
- Backend can run STT jobs through `/api/stt/jobs`, using `faster_whisper` when installed and writing `.transcript.txt`, `.transcript.md`, and `.transcript.json` files. Unit tests use a fake provider so the suite does not download a model.
- React UI adds a `후처리` card with `음성 추출`, `STT 실행`, and `Finder에서 보기` actions. Downloaded single-video paths are auto-filled when available, and users can paste a local video path manually.
- Main download UI now has `저장할 파일 선택` multi-select for `MP4`, `MP3`, and `TXT`; artifact jobs can produce selected combinations and remove the source MP4 when it is not requested.

Remaining:
- Live smoke with a real downloaded lecture video: extract audio, run STT after installing faster-whisper/model if needed, open transcript files, and check Korean transcript quality.
- Optional: add a native Finder file picker for arbitrary video files and a transcript text-copy button.
- Optional: polish artifact-job progress copy for large batch STT and add a transcript text-copy button.

## Priority 6 — Tauri packaging/runtime QA

After Priority 2 UI is stable:
- Verify `npm run tauri:dev` flow end-to-end.
- Verify app window loads courses, shows generalized week content rows, filters by week/type/completion, starts a single video download, starts a PDF/material download, shows progress, saves files, and opens Finder.
- Audit macOS permissions/download folder behavior.
- Decide packaging constraints: whether Python/uv must be installed externally for now or whether a bundled sidecar is needed later.

## Priority 7 — Packaged app / distribution decision

After runtime QA:
- Decide whether this project remains a developer-run Tauri app that expects `uv`/Python on the machine, or graduates to a more bundled app shape.
- If bundling is needed, design the sidecar/runtime strategy before implementation.
- Record install/run commands that a non-developer user can follow without touching `.env`.
