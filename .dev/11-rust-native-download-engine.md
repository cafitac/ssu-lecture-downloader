# Rust native download engine plan

## Goal

Move the final byte-transfer step for the Tauri desktop app from Python to Rust while keeping the LMS/Canvas/LearningX/Commons discovery logic in Python.

This is scoped for GitHub Release style distribution: the app should feel native for long-running downloads, cancellation, progress, and file writes, without rewriting the fragile LMS authentication/resolution flow.

## Boundary

Keep in Python/FastAPI:
- SSU SmartID login and session cache.
- Course, module, week content discovery.
- LearningX/Commons media resolution.
- Safe filename/output-path planning.
- Conflict preflight semantics: overwrite vs skip.

Move to Rust/Tauri:
- HTTP GET byte streaming for resolved Commons URLs.
- `.part` temporary file writes and atomic rename.
- Range resume only when the server returns `206 Partial Content`.
- Cancel checks between chunks.
- Progress snapshots for React polling.
- Parallel native batch download after Python has prepared each item.

## User-visible behavior

- Browser/Vite-only development can keep using the existing Python download jobs.
- Tauri runtime should use Rust-native jobs after Python returns a native download plan.
- UI progress card should keep the same shape; users should not see raw signed URLs, headers, job internals, or developer terms.
- Existing duplicate-file prompt remains: overwrite downloads through Rust, cancel means skip existing files without creating `(2)` duplicates.

## Implementation slices

1. Add a Python native-plan endpoint.
   - Request shape mirrors `/api/download/plan`.
   - Response includes the existing safe `DownloadResult` fields plus a nested native transfer plan containing only what Rust needs: raw `download_url`, optional `referer`, `output_path`, and expected `total_bytes`.
   - If conflict strategy is `skip` and the file exists, mark the result as skipped and omit/disable transfer.

2. Add Rust native job registry.
   - `native_start_download_job` for one item.
   - `native_start_batch_download_job` for prepared items.
   - `native_download_job_status` polling.
   - `native_cancel_download_job` cancellation.

3. Add Rust streaming downloader.
   - Use `reqwest` blocking client inside background threads.
   - Stream chunks to `<target>.part`.
   - Use `Range` only for non-empty partial files.
   - If resume requested but response is not `206`, restart from byte 0.
   - Rename `.part` to final target only after success.

4. Wire React/Tauri bridge.
   - In Tauri runtime, `startDownloadJob` / `startBatchDownloadJob` prepare via Python then start Rust jobs.
   - `fetchDownloadJob` and `cancelDownloadJob` route native job ids to Tauri commands.
   - Browser runtime continues to use existing FastAPI job endpoints.

## Verification

Automated gates:
- `uv run pytest -q`
- `cd frontend && npm run test:contract && npm run build`
- `cd frontend/src-tauri && cargo fmt && cargo test`

Runtime QA remains deferred until the final packaging pass unless explicitly requested:
- `npm run tauri:dev`
- login
- load courses
- download one video and one PDF/material
- verify progress, saved files, cancel, and Finder open
