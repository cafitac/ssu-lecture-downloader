# Current Status

Status: D1 login discovery works; D2 current-term typed course/module summary works; D3 single-lecture download works; D4 service layer extracted for UI/local API; D5 local API and React/Vite prototype work; D6 selected/uncompleted batch plan/download works; D7 streaming byte progress, temp-file atomic writes, retry/resume, API/CLI/UI progress summaries work; D8 explicit term selector plus cancellable background download jobs work; D9 Tauri-first desktop direction, credential provider API, macOS Keychain-first storage with `.env` fallback, UI credential panel, and single-job auto-polling work; D10a in-memory authenticated session/list-result caching works; D10b user-facing session copy, download folder field, and advanced-only plan actions work; D11 Tauri shell + native Finder folder picker works for app QA; D12 user-facing progress bar, one-button start/cancel, advanced diagnostics collapse, and `~/Downloads` default path work

## What happened

The project began as planning-only. After product-direction interviews, the user approved starting D1 with a minimal Python scaffold and requested `uv` for virtualenv/package management.

Current project path:

`~/Project/ssu-lecture-downloader`

## User's clarified final target

The final product is not just a CLI downloader.

The target is a local desktop app, likely Electron-like, where the user can:

- enter LMS ID/password,
- see LMS-visible data such as courses, uncompleted/unwatched lectures, weeks/modules,
- select a weekly lecture item,
- resolve and download lecture video,
- prepare downloaded lectures for text extraction/transcription.

The implementation starts with pure `curl-cffi`/HTTP login and network discovery. The previous project is used only as endpoint/flow reference; the new project is a clean rewrite.

## Current implementation baseline

`uv` scaffold and package baseline:

- `pyproject.toml` with src-layout packaging and console script,
- `uv.lock`,
- `.env.example` using `SSU_USERNAME` and `SSU_PASSWORD`,
- `.gitignore` protecting `.env`, `.env.*`, `.local/`, virtualenv/cache files, media, and runtime artifacts,
- `README.md` with current commands.

Implemented modules:

- `src/ssu_lecture_downloader/redaction.py`
- `src/ssu_lecture_downloader/trace.py`
- `src/ssu_lecture_downloader/http_client.py`
- `src/ssu_lecture_downloader/errors.py`
- `src/ssu_lecture_downloader/auth.py`
- `src/ssu_lecture_downloader/login_flow.py`
- `src/ssu_lecture_downloader/lms.py`
- `src/ssu_lecture_downloader/media.py`
- `src/ssu_lecture_downloader/download.py`
- `src/ssu_lecture_downloader/service.py`
- `src/ssu_lecture_downloader/credentials.py`
- `src/ssu_lecture_downloader/api.py`
- `src/ssu_lecture_downloader/models.py`
- `src/ssu_lecture_downloader/cli.py`

Implemented CLI:

- `login`
- `discover-session` placeholder
- `discover-lms`
- `course-detail`
- `resolve-media`
- `download`
- `batch-download`

Credential rule:

- Terminal-first development can still load local credentials from the current working directory's `.env`.
- Desktop/runtime credential access now goes through `ssu_lecture_downloader.credentials`.
- On macOS, the default credential store is Keychain-first and falls back to `.env` for existing terminal workflows.
- The local API exposes credential status/save/delete without returning username/password values.
- Values must never be printed; CLI/API/test/UI output reports present/missing only.

CLI rule:

- Default output is compact human-readable status/table output.
- `--json` provides redacted machine-readable output.

Service/UI rule:

- `SsuLectureService` owns reusable login, current-course discovery, explicit term listing, course detail/lecture rows, media resolution, single download orchestration, selected/uncompleted batch download orchestration, and progress-event relaying.
- `SsuLectureService` now keeps in-memory-only authenticated session and TTL list-result caches per credential fingerprint. It does not persist Canvas cookies, `xn_api_token`, passwords, or bearer values to disk.
- API credential save/delete invalidates the service caches so new credentials cannot reuse stale authenticated sessions.
- Frontend initial load gets `/api/terms` first and then calls `/api/courses?term_id=<current>` to avoid the duplicate implicit current-term scan.
- `download_to_plan` owns byte streaming, `.part` temp-file writes, atomic rename, retry/resume, caller-driven cancellation, and byte-progress events.
- CLI commands should stay thin adapters over the service.
- The local FastAPI app in `ssu_lecture_downloader.api` exposes `/api/credentials/status`, `/api/credentials`, `/api/terms`, `/api/courses`, `/api/courses/{course_id}`, `/api/download/plan`, `/api/download`, `/api/download/jobs`, `/api/download/jobs/{job_id}`, `/api/download/jobs/{job_id}/cancel`, `/api/download/batch/plan`, and `/api/download/batch` for the UI.
- The React/Vite prototype in `frontend/` uses the local API via a Vite `/api` proxy and supports a session-first login/status panel, explicit term selection, lecture multi-select, editable download folder path with `~/Downloads` default, single background download job start/cancel through one primary button, user-facing progress bar/card with auto-polling, collapsed advanced diagnostics, plus batch plan/download actions.
- Browser-prototype folder selection still uses a text field because the native Finder directory picker is available only in the Tauri shell.
- D9 desktop shell decision: Tauri first, not Electron, because the app is local-first, already React/Vite-based, and benefits from a smaller native shell while Python remains the LMS/service runtime boundary.
- Future desktop UI should call the service/local API layer rather than duplicating CLI internals.

## Verified

Automated tests:

- `uv run pytest -q` => 88 passed.
- `cd frontend && npm run test:contract && npm run build` => passed.

Smoke/live discovery:

- `uv run ssu-lecture-downloader login --trace .local/traces/login.json --json`
  - login succeeded,
  - `xn_api_token_present: true`,
  - redacted trace written.

- `uv run ssu-lecture-downloader discover-lms --trace .local/traces/discover-lms-service.json --json`
  - succeeded after refactoring CLI orchestration through `SsuLectureService`,
  - defaults to the latest active/current term by scanning term candidates and selecting courses with `ended=False`,
  - current live result returned `course_count: 8`,
  - previous legacy `--term-id 43 --term-id 40` result returned old/ended courses (`course_count: 15`),
  - summary fields per course: `id`, `name`, `module_count`, `lecture_count`, `uncompleted_lecture_count`.

- `uv run ssu-lecture-downloader course-detail --course-id 44070 --course-name 'UX방법론' --trace .local/traces/course-detail.json --json`
  - succeeded,
  - returned `lecture_count: 15`,
  - lecture rows include `module_name`, `module_item_id`, `title`, `completed`, `duration`, and `content_type`,
  - live module shape uses `module_id`/`title`, so `LearningXLister.extract_lecture_items` now supports both old `id`/`name` and live `module_id`/`title` module keys.

- `uv run ssu-lecture-downloader download --course-id 44070 --module-item-id 3268578 --course-name 'UX방법론' --output-dir .local/downloads --dry-run --trace .local/traces/download-service-dry-run.json --json`
  - succeeded after refactoring through `SsuLectureService`,
  - planned `.local/downloads/UX방법론/26년1학기_UX방법론_1주차 [699c7d422c427].movie`,
  - `download_size: 330554102`,
  - did not write media bytes.

- `uv run ssu-lecture-downloader batch-download --course-id 44070 --course-name 'UX방법론' --module-item-id 3268578 --output-dir .local/downloads --dry-run --json`
  - succeeded,
  - returned `total_count: 1`, `success_count: 1`, `dry_run: true`,
  - emitted progress stages `resolving` and `planned`,
  - planned `.local/downloads/UX방법론/26년1학기_UX방법론_1주차 [699c7d422c427].movie`,
  - did not write media bytes.

- `uv run ssu-lecture-downloader download --course-id 42045 --module-item-id 3206622 --course-name '데이터사이언스' --output-dir .local/downloads --dry-run --json`
  - succeeded before service-layer refactor,
  - planned a safe path under `.local/downloads/데이터사이언스/`,
  - did not write media bytes.

- `uv run ssu-lecture-downloader download --course-id 44070 --module-item-id 3268578 --course-name 'UX방법론' --output-dir .local/downloads --json`
  - succeeded after enabling `curl-cffi` `stream=True` for real `iter_content`,
  - wrote a numbered safe-path copy under `.local/downloads/UX방법론/`,
  - actual file size: `132,501,810` bytes,
  - emitted `download_started`, thousands of `download_progress` events, and `download_done`,
  - left no `.part` file after atomic rename.

- `uv run ssu-lecture-downloader download --course-id 42045 --module-item-id 3206622 --course-name '데이터사이언스' --output-dir .local/downloads --trace .local/traces/download-live.json --json`
  - succeeded after increasing media GET timeout from curl-cffi default 30s to 300s,
  - wrote `.local/downloads/데이터사이언스/03-1.데이터의 종류와 구성 [66e3a7823e883].mp4`,
  - actual file size: 90,755,929 bytes.

Browser/API verification:

- Ran `uv run uvicorn ssu_lecture_downloader.api:app --host 127.0.0.1 --port 8000`.
- `GET /api/health` returned `ok: true`.
- `GET /api/credentials/status` returned only status booleans/source (`source: env` in live smoke, username/password present, `can_write: true` because macOS Keychain primary remains writable); no credential values were returned.
- `GET /api/terms` returned 5 available term rows and current term `46`.
- `GET /api/courses?term_id=46` returned `ok: true`, `term_ids: [46]`, and `course_count: 8`.
- `GET /api/courses` returned `course_count: 8`.
- `POST /api/download/batch/plan` with selected module item `3268578` returned `ok: true`, `dry_run: true`, `total_count: 1`, and progress stages `resolving`, `planned`.
- Ran `cd frontend && npm run dev -- --host 127.0.0.1` and opened `http://127.0.0.1:5173`.
- Browser rendered current-term course list, UX방법론 lecture rows, single download controls, batch selection checkboxes, and batch plan/download controls.
- Browser selected one lecture checkbox and clicked `선택 강의 배치 계획`; UI rendered `선택 강의 배치 계획 완료: 1개 중 1개 성공`, `batch dry_run true`, `total 1 · success 1 · failed 0`, and `progress resolving → planned`.
- After D7 UI update, the result panes include byte progress summaries for single and batch download responses.
- After D8 UI update, browser rendered the explicit `학기 선택` selector with current/past terms, `작업으로 다운로드 시작`, `작업 상태 새로고침`, and `다운로드 작업 취소` controls.
- D8 API smoke started a background download job, posted cancellation, and confirmed final job status `cancelled`; a UI smoke also started a job and rendered job status/progress without horizontal overflow.
- After D9 UI update, browser rendered `데스크톱 로그인 정보`, Tauri/macOS Keychain copy, credential status booleans, empty password input value, and auto-polling copy; horizontal overflow remained `0`.
- D10a tests prove repeated explicit-term course lookup reuses login/list caches, repeated term lookup avoids rescanning candidates, `clear_cache()` forces relogin/refetch, API reuses a singleton service instance across requests, and credential save/delete clears service caches.
- D10a frontend contract proves initial load uses current term id from `/api/terms` and avoids `loadCourses(null)` duplicate current-term scanning.
- D10b frontend contract proves user-facing session copy, editable download folder field, output_dir request wiring, and advanced-only plan actions.
- D12 frontend contract proves `~/Downloads` default path, one-button start/cancel download UX, progressbar/card copy, hidden default developer diagnostics, and advanced-only detailed diagnostics.
- D10b API now expands `~` in user-provided output directories for plan/download/job/batch requests.
- Browser console had no JS errors; document horizontal overflow check returned `0`.

Trace safety check:

- `.local/traces/login.json` and `.local/traces/discover-lms*.json` were checked for `.env` secret values.
- Service-refactor traces `.local/traces/discover-lms-service.json` and `.local/traces/download-service-dry-run.json` were also checked.
- Current result: no `.env` username/password values found in traces.
- Current result: no raw `Bearer ` strings or `xn_api_token=` cookie assignments found in traces.
- `resolve-media` trace was also checked after adding nested URL and `Referer` redaction; current result has no `.env` value leak.
- `download-live` trace was checked after the real download; current result has no `.env` value leak, no raw `Bearer `, and no `xn_api_token=` cookie assignment.

Media resolver finding:

- For one uncompleted lecture, the Canvas module item page contains an LTI launch form.
- Submitting the form returns LearningX root HTML, not a direct MP4.
- The JS bundle points to `GET /learningx/api/v1/courses/{course_id}/attendance_items/{item_id}`.
- That endpoint returned `item_content_data.content_id` and a `viewer_url` for Commons.
- `GET /learningx/api/v1/commons/contents?content_id={content_id}` returned Commons metadata including `download_url`, `total_file_size`, and `extension=mp4`.
- A HEAD request to the Commons `download_url` returned a 307 to a tokenized `index.php?...DownloadContent2...` URL; following HEAD returned `content-type: application/octet-stream` and `content-length` around 90 MB for the sample lecture.

Important fix discovered during live smoke:

- `sIdno` query parameters can contain the username/student id.
- Redaction now treats `idno`/`sIdno` as sensitive query keys.

## Current recommended next step

Continue D12 Tauri-managed backend process supervision and batch job parity now that D11 Tauri shell/native folder picker is available for app QA. Keep `.dev/06-d10a-performance-cache-plan.md`, `.dev/07-user-ux-session-download-path.md`, and `.dev/08-tauri-native-folder-picker.md` as completed rationale.

1. add a minimal Tauri shell around the existing React/Vite UI,
2. decide how Tauri will start/monitor the Python FastAPI service process in development and packaged mode,
3. add batch background jobs/cancel now that single-job auto-polling works,
4. tighten credential UX around first-run Keychain save/delete and `.env` fallback messaging,
5. only if Python streaming/cancel semantics become limiting, isolate direct byte-transfer into the Rust downloader boundary below,

Rust downloader boundary:

- Python keeps LMS login, course discovery, Commons metadata resolution, redaction, and credentials.
- Rust may own only direct file transfer after Python has resolved the already-authenticated download URL.
- Rust input should be direct URL, output path, optional `Referer`, timeout, and display metadata.
- Rust output should be final path, bytes written, elapsed time, and progress events.
- Rust responsibilities should be streaming write, temp-file + atomic rename, retry/resume/range support when Commons allows it, and cancellation safety.

Still deferred:

- no Playwright unless a concrete HTTP blocker is captured,
- no final Electron/Tauri packaging yet,
- no transcription/text-extraction pipeline yet.
