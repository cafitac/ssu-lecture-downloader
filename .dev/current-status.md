# Current status

## 2026-06-07 artifact existing-file reuse

User-visible request:
- If a requested file already exists, the app should skip recreating it instead of downloading/converting duplicates.
- Example: when a lecture already has an `.mp3` and the user requests `TXT`, generate the transcript from that existing `.mp3` without re-downloading the video or re-extracting audio.

Fix:
- Artifact jobs now first run a dry-run LMS/Commons path plan to compute expected MP4/MP3/TXT paths.
- The job checks existing MP4, audio, and transcript sidecar files before deciding what work is still needed.
- Default artifact conflict behavior is now `skip`/reuse, not rename/overwrite.
- Existing `.mp3` is reported as a skipped audio artifact and then reused as the STT input for TXT creation.
- Existing transcript sidecars are reported as skipped TXT artifacts instead of failing the job.
- If a temporary source video is downloaded only to create MP3/TXT, it is removed after successful post-processing; pre-existing videos are never deleted.
- React now sends `overwrite_artifacts: false` and uses `skip` as the primary artifact conflict strategy.

Verification:
- Added regression coverage for TXT-only requests reusing an existing MP3 without calling video download or audio extraction.
- `uv run pytest tests/test_api.py::test_artifact_download_job_supports_txt_only_selection tests/test_api.py::test_artifact_txt_reuses_existing_mp3_without_downloading_video -q`: 2 passed.
- `uv run pytest tests/test_api.py tests/test_media.py tests/test_stt.py -q`: 50 passed.
- `uv run pytest -q`: 138 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

## 2026-06-07 parallel MP3/TXT artifact progress

User-visible problem:
- In an MP4+MP3+TXT job, the UI reached `TXT 생성 중` with the main progress at 100% and appeared stuck.
- Live job inspection showed the job was still `running`; the last event was `transcribing` for item 1/2 after item 1 MP3 completed. STT had no detailed progress events, and artifact post-processing was sequential.

Fix:
- Cancelled the old live stuck job and restarted the backend after changes.
- Artifact post-processing now runs selected items in parallel up to `max_parallel_downloads` after the MP4 download phase.
- Each item runs its own MP3 -> TXT pipeline, so multiple MP3 conversions and multiple TXT generations can proceed concurrently.
- `transcribe_audio()` now accepts an optional progress callback.
- `faster_whisper` transcription emits:
  - `transcript_model_loading`
  - `transcript_started`
  - `transcript_progress` with segment count, elapsed seconds, duration, and percent when available
  - final 100% progress before `transcript_done`
- Fake/test STT also emits transcript progress.
- React now shows a separate `TXT 생성` phase progress bar and file-row states such as `TXT 모델 준비 중`, `TXT 생성 중`, and `TXT 완료`.
- MP3 ffmpeg progress event volume was throttled to percent changes to avoid thousands of duplicated progress events during long conversions.

Verification:
- `uv run pytest tests/test_stt.py tests/test_media.py tests/test_api.py -q`: 49 passed.
- `uv run pytest -q`: 137 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- Restarted live backend on `127.0.0.1:8000`; `/api/health` returned the expected app/capabilities payload.

## 2026-06-07 MP3 conversion progress bar

User-visible request:
- MP4 byte downloads finish with visible progress, but MP3 conversion can still take a while and looked like the job was stuck at the end.

Fix:
- `extract_audio()` now supports an optional progress callback. When provided, it probes media duration with `ffprobe` when available, runs ffmpeg with `-progress pipe:1 -nostats`, parses `out_time_ms`/`out_time`, and emits `audio_probe_done` plus `audio_progress` events with `elapsed_ms`, `duration_ms`, and `progress_percent`.
- Artifact downloads now forward those audio progress events with the selected lecture `module_item_id`, title, and batch index/total.
- The React progress card now recognizes MP3 stages, shows `MP3 변환 중`, resets the file row from download-complete to conversion progress, and adds a visible `MP3 변환` phase progress bar for both single and multi-selection artifact jobs.
- Existing test doubles remain compatible with the older one-argument `extract_audio(request)` signature.

Verification:
- Added regression coverage for ffmpeg progress parsing.
- `uv run pytest tests/test_media.py tests/test_api.py -q`: 43 passed.
- `uv run pytest -q`: 136 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- Restarted live backend on `127.0.0.1:8000`; `/api/health` returned the expected app/capabilities payload.

## 2026-06-07 empty course list/session revalidation fix

User-visible problem:
- After restart, the app briefly showed the login screen, then automatically entered the course page with `현재 학기 과목 0개를 불러왔습니다`.
- This still exposed the logged-in UI (`로그아웃`) even though the LMS list was not actually trustworthy.

Root cause:
- The frontend fix introduced `isSessionVerified`, but it treated any successful `/api/courses` response as verified, including `course_count: 0`.
- The live backend returned `/api/terms` as `200 OK` with an empty `terms: []`, and `/api/courses` as `200 OK` with `course_count: 0`.
- In the service layer, cached successful login state can outlive the actual LearningX `xn_api_token` cookie. With a stale cached session, LearningX term/course discovery can degrade into empty results instead of a clear 401, so empty discovery was being mistaken for a valid empty semester.

Fix:
- `/api/terms` now treats empty term discovery as an auth/session verification failure (`lms_session_unverified`, HTTP 401) instead of `200 OK`.
- `lms_session_unverified` is included in backend/frontend authentication error codes, so the UI routes back to login instead of showing the course screen.
- `SsuLectureService.list_terms()` now invalidates the cached credential session and clears cookies, then retries one fresh SmartID/LMS login when term discovery is empty. This recovers automatically from expired cached `xn_api_token` when stored credentials are still valid.
- If the fresh login still cannot produce any terms, the API surfaces `lms_session_unverified` and the UI stays on the login-first flow.

Verification:
- Regression tests added:
  - cached empty term discovery retries login once and recovers terms;
  - empty API term discovery returns 401 `lms_session_unverified`.
- `uv run pytest -q` passed: 135 passed.
- `cd frontend && npm run test:contract && npm run build` passed.
- Live backend restarted on `127.0.0.1:8000`; after credential reinjection, live `/api/terms` now returns 5 terms and `/api/courses` returns 8 current courses.

## 2026-06-07 startup auth verification UX fix

User-visible problem:
- After app restart, the UI could enter the logged-in course screen because stored credentials existed, but the actual LMS session/list load was not verified.
- The user then had to press logout and log in again before courses loaded.

Root cause:
- The UI used credential storage booleans (`username_present && password_present`) as the condition for showing the logged-in screen.
- During explicit login, `handleCredentialSubmit()` set `state='loading'` before calling `loadCourses()`, while `loadCourses()` immediately returned when `state === 'loading'`. That could skip the real course-list verification/load and leave the app in a stored-credentials-but-not-verified state.

Fix:
- Added an explicit `isSessionVerified` UI state.
- The logged-in course UI is shown only when stored credentials exist AND the app has successfully loaded LMS courses for the current session.
- On startup with stored credentials, the app stays on the login shell with the message `저장된 로그인 정보로 LMS 세션을 확인하는 중입니다...` until `/api/terms` + `/api/courses` succeed.
- Authentication failures (`session_expired`, SmartID rejection, missing credentials, etc.) already route through `returnToLoginAfterAuthenticationFailure()`, which clears UI state and deletes stale stored credentials.
- Removed the `loadCourses()` early return that skipped real list loading when another caller had already set `state='loading'`.

Verification:
- `cd frontend && npm run test:contract && npm run build` passed.
- Contract test now asserts the app does not show the logged-in course screen until stored credentials are verified by a real LMS list load, and forbids the old `loadCourses()` loading-state early return.

## 2026-06-06 readystream package audio extraction fix

User-provided evidence:
- The user selected audio-only output for two 14주차 readystream lectures.
- The app downloaded two `.mp4` paths successfully, then failed during MP3 creation with `ffmpeg_failed` / `Invalid data found when processing input`.

Root cause:
- The files saved under `.mp4` were not normal MP4 containers.
- `file` identified both as `Microsoft Silverlight Application`; their bytes start with ZIP magic `PK` and contain ReadyStream/Silverlight package files.
- Inside the package, the real media lives under `contents/media_files/...`, e.g. `contents/media_files/media/(...).mp4`.
- ffmpeg was pointed at the outer ReadyStream package, not the inner real media file, so it reported invalid input data.

Completed:
- Updated `postprocess_media.extract_audio()` to detect ReadyStream/Silverlight ZIP packages and extract the largest inner media member under `contents/media_files/` to a temporary file before running ffmpeg.
- This keeps audio-only artifact flow working even when the downloaded source path is a ReadyStream package named `.mp4`.
- Added regression coverage for a readystream package with inner media candidates.

Live recovery/verification:
- Re-ran audio extraction on the two already-downloaded files in `~/Downloads/취업에필요한재무제표이해 (사전녹화) (2150153501)`.
- Created verified MP3 outputs:
  - 1차시: `31757100` bytes, duration `2646.355011`, codec `mp3`
  - 2차시: `24273646` bytes, duration `2022.736689`, codec `mp3`

Verification:
- `uv run pytest tests/test_media.py -q`: 5 passed.
- `uv run pytest -q`: 133 passed.

Runtime note:
- The previous failed run left the outer ReadyStream packages at `.mp4` paths even though audio-only was selected. After this fix, audio-only success should remove the source path in the existing artifact cleanup path. Existing leftover `.mp4` package files can be deleted manually after confirming the `.mp3` outputs play.

---

## 2026-06-06 readystream module-metadata resolver fallback

User-provided evidence:
- After removing the hidden default selection, the MP3 artifact job correctly started with `selected_count:2` for course `44080`.
- Both intended 14주차 videos failed during the `파일 위치 확인` / resolve phase with per-file `HTTPError`:
  - module item `3265047` 14주차 1차시
  - module item `3265048` 14주차 2차시

Root cause:
- These rows are real video materials. The course module list already exposes their Commons metadata:
  - `item_content_data.content_id`
  - `content_type=readystream`
  - `view_url`
  - `duration`
  - `total_file_size`
- The old resolver ignored that available module metadata and re-entered Canvas module item → LTI form → `GET /learningx/api/v1/courses/{course}/attendance_items/{attendance_id}`.
- For these readystream rows, that attendance detail API returned 404, even though the module-list metadata was sufficient to resolve the Commons download.

Completed:
- Added a module-list metadata resolver fallback in `SsuLectureService._resolve_media_authenticated()`.
- If a selected module item already has Commons `content_id` in `content_data.item_content_data`, the service now resolves Commons download metadata directly from that module-list data.
- Existing LTI/attendance-detail path remains as fallback when module-list metadata lacks Commons `content_id`.
- Added regression coverage for a readystream-like row whose attendance detail endpoint is unavailable.

Live verification:
- Direct service dry-run for module items `[3265047, 3265048]` now returns `ok=True` with two planned `.mp4` outputs.
- Restarted the dev backend on `127.0.0.1:8000`, reloaded credentials into runtime memory without printing values, and verified API `POST /api/download/batch/plan` returns 200 OK for the same two items:
  - `3265047` => content `6773aeec2250e`, size `464279393`
  - `3265048` => content `6773b956663cb`, size `440193604`

Verification:
- `uv run pytest -q`: 132 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

Runtime note:
- The latest dev backend is running on `127.0.0.1:8000` with credentials in runtime memory. Reopen/reload the app if it still talks to an older sidecar/process.
- Full MP3 generation will still need to download roughly 900MB of video before audio extraction; the previously failing resolve phase is now fixed.

---

## 2026-06-06 remove default lecture auto-selection

User observation:
- The user filtered to 14주차 and selected the intended videos, but the download count included one extra item.
- Cause was consistent with a hidden/default selection from course load being carried into the later manual selection.

Root cause:
- `selectCourse()` auto-checked the first downloadable video by calling `setSelectedLectureIds([firstDownloadableVideo.module_item_id])` when course detail finished loading.
- Because course detail loading is asynchronous, that default selection could remain or be reintroduced independently of the user's later filter/selection intent.

Completed:
- Removed default checkbox selection on course detail load.
- Course detail may still visually focus the first downloadable item for context, but `selectedLectureIds` is always reset to `[]`.
- The message now says `다운로드할 영상을 직접 선택하세요`.
- Contract test now rejects reintroducing `[firstDownloadableVideo.module_item_id]` or the old `첫 번째 영상 항목을 선택했습니다` copy.
- Existing filter handlers already clear selection on week/type/completion filter changes; this now pairs with no default auto-check.

Verification:
- `cd frontend && npm run test:contract && npm run build`: passed.
- `uv run pytest -q`: 131 passed.

Runtime note:
- Reopen/reload the Tauri app/WebView so it uses the new frontend bundle. After selecting a course, the primary button should say `영상을 선택하세요`/`강의를 선택하세요` until the user explicitly checks rows.

---

## 2026-06-06 non-downloadable Zoom 안내 row and native-plan message fix

User-provided evidence:
- Single MP4 click on course `44080` still failed at 0% with generic `lms_request_failed` after reaching `job_start.start {mode: single_mp4}`.

Live reproduction:
- Restarted the dev backend on `127.0.0.1:8000` with current source and reloaded credentials from `.env` into runtime memory without printing values.
- `/api/courses/44080` showed the first row:
  - title `Zoom 수업 안내...`
  - `kind=video`, `duration=60`, but no `size_bytes`/`file_name`/download metadata.
- Calling native-plan on that row raised `RuntimeError: attendance item does not expose Commons content_id`.

Root cause:
- The UI/backend list treated duration-only attendance items as downloadable videos.
- Some rows are LMS/Zoom 안내/attendance metadata, not Commons-backed media downloads.
- The Tauri native-plan wrapper also replaced backend-specific messages with generic fallback text.

Completed:
- Course content extraction now marks video/material rows downloadable only when they have download metadata (`content_id`, file size, or file name) plus a module item id.
- Duration-only 안내 rows remain visible as video-ish content but are not selectable for MP4/MP3/TXT downloads.
- Native-plan RuntimeError for missing Commons `content_id` now returns an actionable API error:
  - `media_content_id_missing`
  - message tells the user to refresh and select a video item for MP4/MP3/TXT.
- Tauri native-plan error wrapping now preserves backend-provided `ApiError.message` instead of replacing it with generic `lms_request_failed` text.
- `zoom` media type now normalizes to `.mp4` output paths.

Live verification:
- After backend restart, `/api/courses/44080` shows:
  - `Zoom 수업 안내...` => `downloadable=false`
  - first downloadable video => `Zoom 수업 녹화...`, `inner_content_type=zoom`, `size_bytes=200051403`
- `/api/download/native-plan` for that first downloadable video returned 200 OK and planned:
  - `... [69ac0a4e74e28].mp4`

Automated verification:
- `uv run pytest -q`: 131 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

Runtime note:
- The fixed dev backend is currently running on `127.0.0.1:8000` with credentials loaded into memory.
- Existing Tauri/WebView windows may still hold old JS or may use the packaged sidecar on a random port; restart/reopen the app if the button still reports the old generic message.

---

## 2026-06-06 detailed LMS/native-plan HTTP error reporting

User-provided evidence:
- The single-item path now reaches the real native-plan stage but still fails:
  - `conflict_check.skipped_default_overwrite`
  - `job_start.start {mode: single_mp4}`
  - `job_start.failed {error: 선택한 영상의 다운로드 주소를 준비하지 못했습니다. (lms_request_failed) ...}`

Root cause:
- The app is now on the intended single success path, but the backend still maps every `curl_cffi.HTTPError` to generic `lms_request_failed`.
- That hides the important evidence: which LMS/Commons step failed and what HTTP status/host was returned.

Completed:
- Backend `_service_exception_error()` now turns LMS HTTP failures into detailed codes:
  - `lms_request_failed:<step>:<status>`
  - e.g. `lms_request_failed:get_commons_content:502` or `lms_request_failed:head_commons_download:403`.
- Backend error payload now includes a human `message` and safe `detail` object with:
  - `step`
  - `status_code`
  - `host`
  - `elapsed_ms`
  - `body_size`
- Frontend API client now prefers backend-provided `payload.message` over generic `errorMessage(code)`, so the progress card can show the exact LMS/Commons failed stage.
- Added API regression test for native-plan HTTP error detail propagation.
- Contract coverage requires frontend use of backend-provided error messages.

Verification:
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 40 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- `uv run pytest -q`: 128 passed.

Expected next behavior:
- After app/backend restart, the same failure should no longer say only `lms_request_failed`.
- It should show a detailed stage/status, for example `LMS/Commons 요청 실패: get_commons_content 단계에서 HTTP 502 응답을 받았습니다.` and the native-plan wrapper should include `native_plan_failed:lms_request_failed:<step>:<status>`.

Runtime note:
- Process inspection showed the Tauri sidecar on port 51997 had no in-memory credentials, while the fixed dev backend on port 8000 did. If behavior differs after restart, first check which backend the Tauri window is using and whether login state exists in that backend process.

---

## 2026-06-06 remove duplicate conflict preflight LMS calls

User-provided evidence:
- Single selected MP4 still failed before job creation:
  - `click.accepted {outputs: mp4, selected_count: 1, course_id: 44080, tauri: true}`
  - `conflict_check.start {selected_count: 1}`
  - `job_start.failed {error: 처리 중 오류가 발생했습니다: lms_request_failed}`

Root cause:
- Even for one selected item, the UI performed a duplicate dry-run LMS request just to check file conflicts before starting the real native-plan/job path.
- This duplicated the same fragile LMS/Commons resolve step and could fail before the single clear success path started.

Completed:
- Removed duplicate conflict preflight calls from the primary download start path.
- `resolveConflictStrategy()` no longer calls `planDownload()` or `planBatchDownload()`.
- Conflict policy is now explicit and simple for the primary consumer path: log `conflict_check.skipped_default_overwrite` and use `overwrite`.
- This keeps the success path clear: click → dependency gate → explicit overwrite policy → native-plan → native job.
- Contract coverage now rejects duplicate `planDownload()`/`planBatchDownload()` conflict preflight calls and requires the explicit overwrite diagnostic.

Verification:
- `cd frontend && npm run test:contract && npm run build`: passed.
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.

Expected next behavior:
- The next log should no longer contain `conflict_check.start`.
- It should contain `conflict_check.skipped_default_overwrite`, then `job_start.start`.
- If LMS still fails, it will now be at the real native-plan stage and should surface as `native_plan_failed:<code>` / `native_batch_plan_failed:<code>` with the selected item position when applicable.

---

## 2026-06-06 remove native-plan fallback and make failures explicit

User direction:
- Avoid fallback-heavy logic. Prefer clear errors and a single good success path.

Evidence that drove the change:
- `job_start.start {mode: batch_mp4}` followed by `job_start.failed {error: 처리 중 오류가 발생했습니다: lms_request_failed}` showed that Tauri native-plan preparation failed before native job creation.

Root cause:
- The previous patch added backend-job fallback when native-plan failed. That made the UI continue, but it mixed two download engines and made root cause harder to understand.
- Better product behavior is: native/Tauri download succeeds only when native planning succeeds clearly; otherwise show an explicit native-plan error with the failed selected item position.

Completed:
- Removed backend fallback helpers from `frontend/src/lib/api.ts`.
- Tauri single MP4 path now remains: `/api/download/native-plan` succeeds → `native_start_download_job`; otherwise throw explicit `native_plan_failed:<code>`.
- Tauri batch MP4 path now plans selected items sequentially so it can report the exact selected item position that failed.
- Batch native-plan failure now throws `native_batch_plan_failed:<code>` with Korean user text such as `선택한 2번째 영상의 다운로드 주소를 준비하지 못했습니다. (...)`.
- Contract coverage now rejects fallback helper/log markers and requires explicit native-plan error messages.

Verification:
- `cd frontend && npm run test:contract && npm run build`: passed.
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.

Expected next behavior:
- If the selected 2-item batch still has an LMS/native-plan issue, the progress card remains failed with a clear message naming the failing selected item position and low-level code.
- If all selected items resolve cleanly, the native Tauri job starts normally and uses the native progress path.

---

## 2026-06-06 conflict preflight partial_failure fallback

User-provided evidence:
- Progress card showed failure before job creation:
  - `click.accepted {outputs: mp4, selected_count: 2, course_id: 44080, tauri: true}`
  - `conflict_check.start {selected_count: 2}`
  - `job_start.failed {error: 처리 중 오류가 발생했습니다: partial_failure}`

Root cause:
- The app runs a preflight conflict check before starting the real download job.
- For 2 selected items, that preflight calls `/api/download/batch/plan`.
- The service can return `partial_failure` when at least one selected item cannot be planned/resolved.
- That preflight exists only to ask about overwriting existing files; it should not abort the whole download before a real progress job exists.

Completed:
- `resolveConflictStrategy()` now catches `ApiError('partial_failure')` during conflict preflight.
- On that specific preflight-only failure, it logs `conflict_check.partial_failure_fallback` and falls back to `overwrite`, allowing the real job-start path to proceed so progress/error can be attached to an actual job.
- Contract coverage now guards that `partial_failure` during conflict preflight cannot recreate the disappearing progress-card bug.

Verification:
- `cd frontend && npm run test:contract && npm run build`: passed.
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.

Expected next behavior:
- For the same 2-item click, the log should include `conflict_check.partial_failure_fallback`, then `conflict_check.done`, then `job_start.start`.
- If native job planning still fails, the progress card should remain visible with `job_start.failed` showing the next actual blocker instead of disappearing.

---

## 2026-06-01 download-start diagnostics for disappearing progress card

Goal: make the `0% progress appears then disappears` case diagnosable instead of invisible.

Root-cause hypothesis:
- If a failure happens before a real download job id is created (dependency gate, conflict/path planning, native plan, or job-start call), `isStartingDownload` becomes false while `downloadJob` is still null. Without a persisted start error, the progress card disappears.

Completed:
- Added `downloadStartError` state so pre-job failures keep the progress card visible in a failed state.
- Added `appendDownloadDiagnostic()` instrumentation for the download-start chain:
  - `click.accepted`
  - `dependency.blocked`
  - `conflict_check.start`
  - `conflict_check.done`
  - `job_start.start`
  - `job_start.done`
  - `job_start.failed`
  - `job_start.auth_error`
- The progress card now shows `다운로드 시작 실패: ...` plus an expanded `다운로드 진단 로그` section when the failure happens before job creation.
- Diagnostics are also persisted to browser/app localStorage under `ssu-download-diagnostics` and mirrored to console as `[ssu-download] ...` without tokens/cookies/passwords/URLs.

Verification:
- `cd frontend && npm run test:contract && npm run build`: passed.
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.
- Vite dev screen loaded after the change and exposes the corrected `영상을 선택하세요` default when no video row is selected.

How to debug the next failed click:
- Reopen/restart the Tauri app, click download once, then copy the visible `다운로드 진단 로그` lines from the progress card.
- If the card still disappears completely, inspect localStorage key `ssu-download-diagnostics` or the WebView console for `[ssu-download]` lines; that would indicate the running app did not load the latest bundle.

---

## 2026-06-01 immediate progress card and MP4 video-target fix

Goal: fix the user-visible regression where pressing the download button could appear to do nothing before progress was shown, and make MP4 target video rows instead of accidentally starting from a PDF/material row.

Root cause:
- The UI only rendered the progress card after a real download job id existed.
- Before that, the click handler waited on path/conflict/native-plan preparation. If that preparation was slow or failed, no progress card appeared even though the click handler had started.
- The default selected downloadable row could be a PDF material, while the selected output label was `MP4`, so `MP4 다운로드 시작` could actually target a PDF/material row.

Completed:
- Progress card now appears immediately while `isStartingDownload` is true, before the real job id exists.
- The prep state shows `준비 중` and `저장 위치와 파일 정보를 확인하는 중입니다...` at 0%, then switches to the real job progress once the job starts.
- Course detail now auto-selects the first downloadable video row for the default MP4 path.
- MP4/MP3/TXT selections now require video rows; PDF/material rows show `영상만` and their checkboxes are disabled for these output choices.
- Updated frontend contract coverage for immediate pre-job progress and MP4/MP3/TXT video-only targeting.

Verification:
- `cd frontend && npm run test:contract && npm run build`: passed.
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.
- Browser dev smoke before the video-target refinement confirmed the latest progress card renders and a real download job reaches completion. After the refinement, the fixed Vite screen shows the primary label as `영상을 선택하세요` when no video is selected, rather than allowing a PDF row under `MP4`.

Remaining:
- Restart/reopen the Tauri app so the WebView loads this latest frontend bundle. Existing app windows/processes can keep the older JS in memory.

---

## 2026-06-01 MP3 retry overwrite and visible artifact errors

Goal: eliminate another “button returns with no visible error” path for MP3/TXT generation.

Completed:
- Verified in the live browser that the latest source moves MP3 selection from a PDF row to the first video row and starts a real download/progress card.
- Changed the main MP3/TXT artifact request to `overwrite_artifacts: true`, so retrying a lecture does not fail with `audio_exists` just because a previous `.mp3` or transcript output already exists.
- Added progress-card error detail for artifact failures (`파일 생성 실패: ...`) so ffmpeg/audio/STT errors are visible instead of looking like no action happened.
- Kept frontend contract coverage for overwrite-on-main-artifacts and visible artifact error text.

Verification:
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- Browser smoke on `http://127.0.0.1:5173`: selecting MP3 moved the selected row from PDF material to the first video row; pressing the primary button showed a progress card and active cancel button.

Remaining:
- Rebuild/reopen the Tauri app from this latest source. If it still appears to do nothing, the progress card should now show a concrete `파일 생성 실패` or `downloadJob.error_code` value to report.

---

## 2026-06-01 MP3/TXT video-only selection fix

Goal: prevent MP3/TXT downloads from trying to process downloadable PDF/material rows as if they were videos.

Completed:
- Confirmed the live UI was auto-selecting the first downloadable row, which could be a PDF `자료` item, not a video.
- When `음성/MP3` or `자막/TXT` is selected, the UI now targets video rows only: existing video selections are kept, otherwise the first visible downloadable video is selected automatically.
- The primary download button now uses the effective video selection count for MP3/TXT labels and disables with `영상을 선택하세요` when no video is available.
- `전체 선택` now selects visible videos only while MP3/TXT is selected, avoiding accidental PDF/material processing.
- Starting a download now shows explicit messages instead of silently returning when course/lecture/video selection is invalid.
- Added frontend contract coverage for video-only MP3/TXT selection.

Verification:
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

Remaining:
- Reopen/rebuild the app UI and retry `음성/MP3` after selecting a course. If the first selected row was a PDF, selecting MP3 should now switch the active selection to the first video row before starting.

---

## 2026-06-01 MP3/TXT artifact polling and postprocess removal

Goal: make MP3/TXT creation happen only through the main file-selection download flow, and fix the case where MP3-only downloads appeared to start then disappeared.

Completed:
- Removed the separate manual `후처리` panel from the default UI; users now choose `영상`, `음성`, and/or `자막` in the main download panel and run one download action.
- Fixed artifact job polling/cancel state in `frontend/src/lib/api.ts`: MP3/TXT jobs now preserve the `artifact-` job id prefix after every status response, so the UI keeps polling `/api/download/artifacts/jobs/...` instead of accidentally switching to the normal MP4 job endpoint.
- Added frontend contract coverage that forbids the manual postprocess panel and guards the artifact job id prefix behavior.
- Stabilized the backend artifact job test by polling the background job like the UI does instead of assuming the worker thread completes before the first status read.

Verification:
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

Remaining:
- Refresh/reopen the running app UI, then retry `음성/MP3` only. The progress card should stay visible and continue polling until the MP3 job succeeds or shows a real error.

---

## 2026-06-01 first-downloadable selection UX fix

Goal: avoid a confusing disabled `TXT 다운로드 시작` button when media dependencies are ready but no lecture has actually been selected for download.

Completed:
- Course detail load now automatically selects the first downloadable item, so the primary download button is immediately usable after a course opens.
- If the user clears all lecture selections, the primary button now says `강의를 선택하세요` instead of misleadingly showing `TXT 다운로드 시작` or similar start text.
- Added frontend contract checks to keep this no-selection/button-label behavior from regressing.

Verification:
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

Remaining:
- Refresh/reopen the running app UI to pick up this change, then select TXT again and confirm the first downloadable row is checked and the primary button is enabled.

---

## 2026-06-01 app-like download readiness UX polish

Goal: keep the download panel feeling like a consumer app rather than a developer diagnostics screen.

Completed:
- Replaced the raw header text `API 서버 ...` with a user-facing app connection label.
- Changed MP4/MP3/TXT checkboxes into card-style choices labelled `영상`, `음성`, and `자막`, with MP4/MP3/TXT as secondary labels.
- Replaced the always-visible ffmpeg/faster-whisper status table with a single readiness card: `영상·음성·자막 저장 준비 완료` or `음성/자막 저장 준비가 필요합니다`.
- Moved technical readiness details behind a collapsed `자세한 준비 상태` disclosure that appears only when something is missing.
- Renamed install/recheck controls to app-like actions (`자동 준비`, `다시 확인`) and kept raw component names out of the primary path.
- Added frontend contract coverage so the primary surface stays non-developer-oriented.

Verification:
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

Remaining:
- Browser/Tauri visual QA is still deferred; manually confirm the card/toggle spacing in the real app window.

---

## 2026-06-01 MP3/TXT readiness button-state clarification

Goal: make the download panel unambiguous after ffmpeg/faster-whisper are already available, and prevent MP3/TXT downloads from starting when required media components are missing.

Completed:
- Added an explicit `MP3/TXT 준비 완료` message when both ffmpeg and faster-whisper are available, so an enabled download button means the app is ready rather than still installing.
- Added a selected-output guard: MP4-only downloads can still start without MP3/TXT components, but MP3/TXT selections disable the primary action and show `구성요소 확인 필요` until the missing component is installed/rechecked.
- Applied the same dependency gates to the manual 후처리 buttons: 음성 추출 requires ffmpeg, STT 실행 requires faster-whisper.
- Updated stale UI help text from `small/ko` to the current `large-v3/ko` default.
- Added frontend contract coverage for the dependency-gated primary button and the ready-state copy.

Verification:
- `uv run pytest tests/test_api.py tests/test_stt.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

Remaining:
- Browser/Tauri manual visual QA is still deferred; verify that the ready message appears and the primary button disables only when MP3/TXT is selected while a required component is unavailable.

---

## 2026-06-01 Korean high-accuracy STT default slice

Goal: make the TXT/STT path prioritize Korean lecture accuracy, targeting a Clova-like user expectation while keeping downloaded lecture audio local/offline by default.

Completed:
- Switched the default faster-whisper model from `small` to `large-v3` for both direct STT jobs and MP4/MP3/TXT artifact jobs.
- Forced Korean language by default and added quality-oriented faster-whisper options: beam search size 5, VAD enabled, deterministic temperature, and a Korean lecture initial prompt for course terminology/numbers/proper nouns.
- Extended STT provider status with `recommended_model` and a Korean quality note so UI/API clients can explain that `large-v3` is intentionally slower but higher quality.
- Updated the React client/manual STT button and API client defaults to use `large-v3`.
- Updated README and contract coverage so future changes do not silently downgrade the default STT model.

Verification:
- `uv run pytest tests/test_stt.py tests/test_api.py -q`: 39 passed.
- `uv run pytest -q`: 127 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

Remaining:
- Real transcript-quality QA is still deferred until faster-whisper/model runtime is installed and a short real Korean lecture sample is run. The first `large-v3` run may take significantly longer because the model must be downloaded/prepared locally.
- NAVER CLOVA Speech itself remains a separate cloud-provider integration requiring user-provided credentials/consent; it was not added as the default because the current product path avoids auto-uploading lecture audio to external APIs.

---

## 2026-06-01 one-click MP3/TXT component install slice

Goal: make the missing ffmpeg/faster-whisper state actionable from the app UI instead of asking users to run terminal commands such as `uv add`, `uv pip install`, or `brew install`.

Completed:
- Added `src/ssu_lecture_downloader/dependency_installer.py` to detect missing MP3/TXT components and install only the missing supported packages (`imageio-ffmpeg` for ffmpeg fallback, `faster-whisper` for TXT/STT) via `uv pip install --python <current-python>` or `python -m pip install` in non-frozen development/runtime environments.
- Added FastAPI install job endpoints:
  - `POST /api/media/dependencies/install/jobs`
  - `GET /api/media/dependencies/install/jobs/{job_id}`
- Extended `/api/media/dependencies/status` with `auto_install_available` and `auto_install_unavailable_reason` so packaged/frozen builds can explicitly say when self-install is unavailable.
- Updated the React download panel with a `필요한 구성요소 설치` button that appears only when a component is missing, disables during active downloads/install, starts the backend install job, polls progress, and refreshes readiness after success.
- Replaced user-facing terminal-command guidance in the primary UI with app-level install/reinstall messaging.

Verification:
- `uv run pytest tests/test_api.py tests/test_media.py tests/test_stt.py -q`: 42 passed.
- `uv run pytest -q`: 126 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.

Remaining:
- Real runtime QA is still deferred: click `필요한 구성요소 설치` in the app, verify the installer result on this machine/target, then run a small MP3/TXT lecture job.
- Frozen/PyInstaller packaged sidecars still cannot safely mutate their internal Python environment at runtime; those builds should bundle compatible wheels up front or show the reinstall path.

---

## 2026-06-01 packaged backend sidecar build slice

Goal: remove the last developer-only `uv run uvicorn ...` dependency from distributable Tauri builds while keeping that fallback for local development.

Completed:
- Added `scripts/ssu_api_sidecar.py`, a small packaged API entrypoint that runs `ssu_lecture_downloader.api:app` through uvicorn with `--host`/`--port` arguments.
- Added `scripts/build_backend_sidecar.py` and `frontend` script `npm run build:backend-sidecar`; it runs PyInstaller via `uv run --with pyinstaller`, then writes the standalone executable to `frontend/src-tauri/resources/backend-sidecar/ssu-api`.
- Added the Tauri bundle resource directory `resources/backend-sidecar` and changed backend supervision so packaged/dev builds prefer a built `ssu-api` sidecar, while falling back to `uv run uvicorn ...` when no sidecar exists.
- Added `SSU_BACKEND_EXECUTABLE=/path/to/ssu-api` support for explicit sidecar smoke testing without rebuilding the bundle.
- Updated README packaging instructions and backend/download architecture notes.

Verification:
- `uv run pytest tests/test_stt.py tests/test_api.py -q`: 35 passed.
- `uv run pytest -q`: 123 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- `cd frontend/src-tauri && cargo fmt --check && cargo test -q`: 11 passed.
- `cd frontend && npm run build:backend-sidecar`: built `frontend/src-tauri/resources/backend-sidecar/ssu-api`.
- Sidecar smoke: `./frontend/src-tauri/resources/backend-sidecar/ssu-api --host 127.0.0.1 --port 48123` served `/api/health` and `/api/media/dependencies/status`.
- `cd frontend && npm run tauri:build`: built `.app` and `.dmg` successfully.

Remaining:
- Packaged app manual QA with a real LMS login and a small lecture download/transcript job is still deferred.
- This local build's executable and sidecar are `x86_64`; build on the intended release machine/target if a native arm64 release is required.
- The local sidecar reports packaged `ffmpeg`/system ffmpeg readiness but `faster-whisper` is unavailable in this Python 3.13 build environment, so TXT/STT remains gated until a target-compatible faster-whisper runtime is included.

---

## 2026-06-01 packaged MP3/TXT runtime dependencies slice

Goal: treat MP3/TXT generation as an end-user app feature, not a developer setup step that asks users to run `brew install` or `uv pip install` manually.

Completed:
- Added `imageio-ffmpeg` as a runtime dependency and changed audio extraction to use a bundled `imageio_ffmpeg` ffmpeg binary when system `ffmpeg` is absent.
- Kept `faster-whisper` as a packaged runtime dependency for supported build targets; on macOS Intel/dev environments where current `onnxruntime` wheels are unavailable, the app reports an explicit packaged-dependency reinstall/development-sync hint instead of suggesting user-level brew/pip commands.
- Added `GET /api/media/dependencies/status` and a `packaged_media_dependencies` health capability so the UI can show ffmpeg/STT readiness before a TXT/MP3 download.
- Updated the download and postprocess UI to show ffmpeg/faster-whisper readiness as app-packaged components and to block MP3/TXT start with a clear reinstall/sync message when a required packaged component is missing.
- Updated tests and frontend contract coverage for the packaged dependency status surface.

Verification:
- `uv run pytest tests/test_media.py tests/test_stt.py tests/test_api.py -q`: 39 passed.
- `uv run pytest -q`: 123 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- `cd frontend/src-tauri && cargo test -q`: 10 passed.

Remaining:
- Packaging/distribution still needs a concrete sidecar/runtime build step: today Tauri dev still launches `uv run uvicorn ...` from the repo root. For a real `.app`, bundle a Python runtime/backend sidecar and its wheel environment, including `imageio-ffmpeg` and target-supported `faster-whisper`, then point `backend_launch_command` at that sidecar instead of requiring end-user `uv`.
- Live runtime QA with a real lecture is still deferred.

---

## 2026-05-31 selectable MP4/MP3/TXT download artifacts slice

Goal: let users choose desired output artifacts before downloading selected lectures, instead of requiring a separate manual post-download flow.

Completed:
- Added MP3 audio extraction support to `postprocess_media.py` while keeping WAV/M4A compatibility.
- Added FastAPI artifact job endpoints:
  - `POST /api/download/artifacts/jobs`
  - `GET /api/download/artifacts/jobs/{job_id}`
  - `POST /api/download/artifacts/jobs/{job_id}/cancel`
- Artifact jobs download the selected lecture(s), keep MP4 when selected, extract MP3 when selected, run STT/TXT when selected, and remove the source video after conversion when MP4 is not selected.
- Added React API/types and a main download-panel `저장할 파일 선택` multi-select for `MP4`, `MP3`, and `TXT`. The primary button label now reflects the selected artifact set.
- Kept the existing manual `후처리` card for already-downloaded/local video paths.

Verification:
- `uv run pytest tests/test_media.py tests/test_stt.py tests/test_api.py -q`: 36 passed.
- `uv run pytest -q`: 121 passed.
- `cd frontend && npm run test:contract`: passed.
- `cd frontend && npm run build`: passed.

Remaining:
- Deferred live runtime QA with a real lecture: select MP4/MP3/TXT combinations, verify created files, verify Whisper install hint when needed, and inspect Korean transcript quality.

---

## 2026-05-31 audio extraction + STT transcript slice

Goal: lower official-player/autoplay priority and add a post-download pipeline that turns downloaded lecture videos into AI-summary-ready transcript files.

Plan document:
- `.hermes/plans/2026-05-31_160210-audio-stt-pipeline.md`

Completed:
- Added backend audio extraction support via `src/ssu_lecture_downloader/postprocess_media.py`, re-exported from `media.py` for compatibility. It validates local video files, checks for system or bundled `imageio-ffmpeg` ffmpeg, returns a Korean packaged-component hint when missing, and writes `.wav` audio into a sibling `transcripts/` folder by default.
- Added backend STT support via `src/ssu_lecture_downloader/stt.py`. It exposes provider status/install hints, a fake provider for tests, `faster_whisper` execution when installed, and transcript writers for `.txt`, `.md`, and `.json` files.
- Added FastAPI job endpoints:
  - `POST /api/media/audio/jobs`
  - `GET /api/media/audio/jobs/{job_id}`
  - `POST /api/media/audio/jobs/{job_id}/cancel`
  - `GET /api/stt/providers/status`
  - `POST /api/stt/jobs`
  - `GET /api/stt/jobs/{job_id}`
  - `POST /api/stt/jobs/{job_id}/cancel`
- Added React API/types and a `후처리` UI card with video path input, `음성 추출`, `STT 실행`, and `Finder에서 보기`. Single downloaded video paths are auto-filled when available.
- Whisper/faster-whisper missing case is explicit: `/api/stt/providers/status` and failed STT jobs surface packaged-component reinstall/development-sync guidance instead of silent fallback.

Verification:
- `uv run pytest -q`: 120 passed.
- `cd frontend && npm run test:contract`: passed.
- `cd frontend && npm run build`: passed.

Remaining:
- Live runtime smoke with a real downloaded lecture file: extract audio, install faster-whisper if prompted, run STT, and verify transcript quality/files.
- Optional polish: Finder file picker for arbitrary video input, transcript text-copy button, and later batch STT.

---

## 2026-05-26 official-viewer session bridge slice

Goal: make the Tauri official viewer inherit the Python-authenticated LMS browser session so official Commons playback has the required Canvas/LearningX cookies instead of opening a bare resolved `viewer_url`.

Plan/status document:
- `.dev/official-viewer-session-bridge.md`

Completed:
- Mapped the existing official-viewer flow: React requested `/api/watch/viewer-plan`, Python resolved the Commons URL with an authenticated `curl_cffi` session, but the Tauri/WebKit viewer opened with a separate empty cookie store.
- Identified the missing browser state as Python's Canvas/LearningX cookie jar, especially `xn_api_token` and related LMS cookies.
- Added `/api/watch/session-bridge-plan`, returning the existing viewer plan plus a Tauri-only cookie bridge payload, a Canvas module-item `launch_url`, and safe diagnostics containing only cookie count/names/domains and redacted trace data.
- Added React/Tauri wiring so manual `시청` opens in Tauri use the bridge endpoint while browser/Vite development keeps the original plain viewer-plan flow.
- Added Rust command `open_official_viewer_with_session`: create the official-viewer WebView at a safe bootstrap HTTPS URL, set cookies via Tauri 2.11 `WebviewWindow::set_cookie`, then navigate to the validated Canvas module item URL rather than directly to the Commons viewer URL. Manual row-level `시청` intentionally does not install the sequential-watch auto-close assist.
- Added tests for the API bridge payload and updated the Soongsil LMS automation skill/reference with the bridge pattern.

Verification:
- `uv run pytest -q`: 113 passed.
- `cd frontend && npm run test:contract`: passed.
- `cd frontend && npm run build`: passed.
- `cd frontend/src-tauri && cargo fmt --check`: passed.
- `cd frontend/src-tauri && cargo check`: passed on retry. First run hit an Apple clang linker segfault while compiling a dependency build script, then `cargo check -q` completed successfully without code changes.
- Note: `~/Project/ssu-lecture-downloader` is not a git repository, so git status/diff is unavailable here.

Follow-up fix:
- Removed the sequential-watch `ended`/auto-close navigation hook from the manual session-bridge viewer after live Tauri QA showed a row-level `시청` window closing by itself after a few seconds. Root cause: ordinary `시청` was sharing the sequential-watch assist path, so any early/hidden/prior-position `video ended` event could close the viewer.
- Added a separate manual-watch autoplay helper for row-level `시청`: it polls for the official player video/play controls and attempts `video.play()`/play-button clicks, but never emits `ssu-watch://ended` and never closes the viewer.
- Verification after fix: `cargo fmt --check`, `cargo check -q`, `cargo test -q` (9 passed), `npm run test:contract`, and `npm run build` passed.

Remaining before calling the app release-ready:
- Live Tauri runtime QA with a real LMS login: `npm run tauri:dev`, open a lecture with `시청`, verify the official player loads with the bridged session, then confirm progress/resume in the normal LMS page.
- Sequential-watch mode still uses the older assisted viewer path and may need the same session bridge before it can be considered reliable.
- Native batch downloads are still sequential inside the Rust job registry; true bounded parallel Rust workers remain an optimization follow-up.
- Packaging/distribution decision is still open: developer-run app requiring `uv`/Python vs bundled sidecar/runtime.

---

## 2026-05-25 Rust native download engine slice

Goal: document and start the GitHub Release packaging-friendly path where Python keeps LMS/Commons resolution and Tauri/Rust owns final byte streaming.

Plan document:
- `.dev/11-rust-native-download-engine.md`

Completed:
- Added `/api/download/native-plan` so the desktop app can ask Python for a resolved raw Commons transfer plan without making Python write the file.
- Added `NativeDownloadPlanResult` / `NativeDownloadTransferPlan` service models and API serialization.
- Added Tauri native download commands:
  - `native_start_download_job`
  - `native_start_batch_download_job`
  - `native_download_job_status`
  - `native_cancel_download_job`
- Added Rust streaming download implementation using `reqwest` blocking client, `.part` files, best-effort Range resume, cancellation checks, progress events, and final rename.
- Wired React API client so Tauri runtime uses Python native-plan + Rust native jobs, while browser/Vite runtime keeps existing FastAPI jobs.
- Updated frontend contract expectations from stale sequential-batch copy to the current simultaneous-download copy.

Important limitation:
- Native batch command currently executes prepared items sequentially inside the Rust job registry while reporting the same job/progress shape. True bounded parallel Rust workers remain a follow-up before marketing the app as faster native parallel downloading.

Verification:
- `uv run pytest -q`: 106 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- `cd frontend/src-tauri && cargo test`: 6 passed.
- Note: `~/Project/ssu-lecture-downloader` is not a git repository, so git status/diff is unavailable here.

---

## 2026-05-25 QA-deferred development sweep

User asked to defer runtime QA until the end and continue any remaining development work first.

Findings:
- Roadmap Priority 1 and Priority 2 implementation are complete by current docs and automated gates.
- `frontend/README.md` still listed `Batch background jobs/cancel` as deferred, but the API/client/UI/tests already include cancellable batch jobs through `/api/download/batch/jobs` plus shared job polling/cancel endpoints.

Completed:
- Updated `frontend/README.md` so implemented scope reflects generalized LMS content rows, selected-content batch jobs, active single/batch polling, and week/type/downloadability/completion filters.
- Removed stale `Batch background jobs/cancel` from the deferred list.

Remaining before packaging/distribution decisions:
- Tauri runtime QA only: `npm run tauri:dev`, login, load courses, verify week/type/completion filters, download one video and one PDF/material, check progress, saved files, and Finder open.

---

## 2026-05-25 completion-filter follow-up

Goal: finish the remaining Priority 2 UI slice by adding completion-state filtering to the generalized LMS content list.

Completed:
- Added a low-emphasis completion-state select beside the existing week/type filters: all, completed, uncompleted, and unknown.
- Wired completion filtering into `filteredContentItems` without changing selected-download semantics.
- Updated the lecture panel completed count to use generalized `contentItems` instead of the legacy video-only `lectures` count.
- Added frontend contract coverage for the completion filter markers.

Verification:
- `cd frontend && npm run test:contract && npm run build`: passed.
- `uv run pytest -q`: 100 passed.

---

## 2026-05-25 follow-up

Goal: implement the documented LMS week-content inventory in the service/API/UI and allow PDF materials to use the same Commons planning path as videos.

Completed:
- Added generalized `CourseContentItem` model and LearningX module extraction for video, PDF/material, assignment, quiz, board, page, section, and unknown rows.
- Extended `/api/courses/{course_id}` to return `content_count` and `content_items` while keeping legacy `lectures` for video-only compatibility.
- Updated React course detail to keep the existing mounted layout while showing week/type filters, downloadable-only filtering, and read-only non-downloadable LMS rows.
- Changed batch download selection to use generalized downloadable content rows, so explicitly selected PDF/material `attendance_item` rows can be planned/downloaded just like videos.
- Added PDF planning tests for single and batch flows; PDF paths now use `.pdf` when the resolved LearningX content type is `pdf`.

Verification:
- `uv run pytest -q`: 100 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- Note: `~/Project/ssu-lecture-downloader` is not a git repository, so git status/diff is unavailable here.

---

## 2026-05-25

Goal: before extending downloads beyond videos, inspect the live LMS module data and document what course/week content the app should support.

- Created `.dev/10-lms-content-inventory.md` from a live authenticated LearningX/Canvas inspection.
- Confirmed current course modules expose more than video rows: `attendance_item`, `assignment`, `quiz`, `board`, `wiki_page`, and `sub_header` appeared in current-term samples.
- Confirmed `attendance_item` can wrap both `movie` and `pdf` content via `content_data.item_content_data.content_type`.
- Confirmed video duration is available as numeric seconds at `content_data.item_content_data.duration`.
- Confirmed PDF lecture material has file size metadata and resolves through the same Commons download path as videos; live PDF HEAD returned a signed Commons redirect.
- Main gap: `LearningXLister.extract_lecture_items` is still video-oriented and hides PDF/material rows. Next work should introduce a generalized week-content row model and filters before expanding batch download.

Recommended next priority:
1. Add generalized `CourseContentItem` extraction and tests.
2. Extend API/frontend course detail to show LMS-like week content rows with type filters.
3. Add selected-week/type-filtered batch planning/download for videos and PDFs first.

Verification:
- Live read-only LMS inspection succeeded with `.env` credentials; no files were downloaded.
- PDF and movie samples were resolved to redacted Commons metadata/HEAD responses only.

---

## 2026-05-24

Follow-up: avoid failing when port 8000 is already occupied by another local process.

- Changed Tauri backend supervision to reuse `127.0.0.1:8000` only when its `/api/health` returns the expected healthy API response.
- If 8000 is empty or occupied by a non-matching process, Tauri now chooses a free random localhost port and launches `uvicorn` on that port.
- Added `backend_api_base_url` Tauri command and made the React API client use it in Tauri runtime; browser-only Vite development still uses the `/api -> 127.0.0.1:8000` proxy.
- Added FastAPI CORS for localhost/Tauri origins so Tauri dev can call the random backend port directly.
- Live collision smoke: held `127.0.0.1:8000` with `python3 -m http.server`, ran `npm run tauri:dev`, confirmed Tauri launched uvicorn on random port `54168`, `GET http://127.0.0.1:54168/api/health` returned `{"ok":true}`, while port 8000 remained the dummy server.

Verification after dynamic-port change:
- `.venv/bin/python -m pytest tests/ -q`: 89 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- `cd frontend/src-tauri && cargo fmt && cargo test`: 4 passed.

---

## 2026-05-24 earlier

Goal: make the Tauri desktop app start the Python/FastAPI backend automatically so the user does not need a separate terminal command.

Current baseline before this slice:
- Backend can be started manually with `uv run uvicorn ssu_lecture_downloader.api:app --host 127.0.0.1 --port 8000`.
- Vite proxies `/api` to `http://127.0.0.1:8000`.
- Tauri shell exists and can open Finder/download folder, but README says Python process supervision is still next.
- Single-download progress and mp4 extension issues were fixed and verified (`89 passed`, frontend contract/build, cargo test).

Priority order:
1. Tauri-managed Python backend process supervision.
2. Tauri packaging/runtime QA after backend autostart works.
3. Batch background jobs/cancel support.

## Priority 1 acceptance criteria

- `npm run tauri:dev` can open the app without manually starting `uvicorn` first.
- On app setup, Tauri checks `GET /api/health` on `127.0.0.1:8000`.
- If an API is already running, Tauri reuses it and does not kill it on app exit.
- If no API is running, Tauri starts `uv run uvicorn ssu_lecture_downloader.api:app --host 127.0.0.1 --port 8000` from the repository root.
- If Tauri started the backend, it terminates that child process when the app exits.
- There is a small status command/contract so we can verify whether the backend is `external` or `managed`.
- Tests cover command construction/project-root resolution and the frontend contract covers the new Tauri supervision markers.

## Progress log

- Created this status file before code changes so implementation decisions and follow-up work are recorded.
- Implemented `BackendManager` in `frontend/src-tauri/src/lib.rs`:
  - checks `/api/health` on `127.0.0.1:8000`,
  - marks mode as `external` when an API is already running,
  - starts `uv run uvicorn ssu_lecture_downloader.api:app --host 127.0.0.1 --port 8000` from the repository root when no API is healthy,
  - exposes `backend_supervision_status` for UI/status checks,
  - kills only the owned child process on app exit,
  - supports `SSU_BACKEND_AUTOSTART=0` as a debugging escape hatch.
- Added UI visibility: credential panel now displays `API 서버 {browser|external|managed|unknown}`.
- Added Rust tests for repository-root resolution and uvicorn command construction.
- Added frontend contract checks for backend supervision markers and stale-progress regression.
- Updated README/frontend README to record the new Tauri-managed backend behavior and next priorities.
- Live smoke: killed the manually running backend, started `npm run tauri:dev`, confirmed `curl http://127.0.0.1:8000/api/health` returned `{"ok":true}`, confirmed `lsof` showed a new Python listener owned by the Tauri run, then killed the Tauri process and confirmed port 8000 was released.

## Verification

- `.venv/bin/python -m pytest tests/ -q`: 89 passed.
- `cd frontend && npm run test:contract && npm run build`: passed.
- `cd frontend/src-tauri && cargo test`: 4 passed.
