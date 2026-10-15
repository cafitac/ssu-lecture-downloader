# Official viewer session bridge

## Current flow

1. React calls `planOfficialViewer(course_id, module_item_id, course_name)` for browser/dev mode.
2. Backend `/api/watch/viewer-plan` logs into SmartID/Canvas with Python `curl_cffi`, resolves the Commons viewer URL, and returns only viewer metadata plus `viewer_url`.
3. Tauri manual `시청` calls `/api/watch/session-bridge-plan`, which returns the resolved Commons `viewer_url`, the LMS-authenticated Canvas module `launch_url`, and a local-only cookie bridge payload.
4. Tauri creates a new `official-viewer` WebView at a safe bootstrap URL, injects the Python LMS cookies, installs a manual-watch autoplay-only helper, then navigates to the Canvas module item `launch_url` instead of directly opening the Commons viewer URL. Manual row-level `시청` must not install the sequential-watch auto-close assist.
5. Navigating through the Canvas module item is intentionally closer to the normal LMS click path; if live QA still shows `00:00` resume, the remaining gap is likely deeper browser/LTI state rather than missing direct viewer cookies alone.

## Missing session state

The missing state is the authenticated LMS cookie jar from the Python login session, especially Canvas/LearningX cookies like `xn_api_token`. These cookies must be injected into the Tauri WebView before navigating to the official Commons viewer URL.

## Tauri API boundary

Tauri 2.11 exposes `WebviewWindow::set_cookie` and `WebviewWindow::navigate`. The bridge creates the viewer at a safe bootstrap URL, injects cookies with domain/path/security attributes, installs only the manual-watch autoplay helper, then navigates to the validated Canvas module item launch URL. The resolved Commons viewer URL is retained as a fallback and diagnostic surface, but direct Commons navigation is no longer the primary Tauri session-bridge path. Autoplay/end-detection belongs only to the explicit sequential-watch flow because it can close the viewer when any early/hidden/prior-position video emits `ended`; row-level `시청` may try `video.play()`/play-button clicks but must not emit an ended signal.

## Safe diagnostics

The bridge endpoint returns cookie names/domains/count and redacted HTTP trace diagnostics. It intentionally does not log cookie values in diagnostics. Cookie values are returned only to the local Tauri command payload so WebKit can receive the session.

## Implemented slice

- Backend endpoint: `POST /api/watch/session-bridge-plan`
- Backend model: `OfficialViewerSessionBridgeResult` with `launch_url`, `cookies`, and `diagnostics`
- Frontend Tauri path: manual official-viewer opens use `planOfficialViewerSessionBridge`
- Tauri command: `open_official_viewer_with_session`
- Validation: fallback viewer URLs are restricted to HTTPS `commons.ssu.ac.kr`/subdomains or `ssu-toast.commonscdn.com`; Tauri bridged launch URLs may also use HTTPS `canvas.ssu.ac.kr` module item pages.

## Verification

- `uv run pytest -q`
- `npm run test:contract`
- `npm run build`
- `cargo check`
