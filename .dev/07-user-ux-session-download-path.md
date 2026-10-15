# D10b User UX: Session-first login and download path

Status: completed

User feedback:

- macOS Keychain is not necessary for the app UX right now.
- The app should log in once when first opened and then reuse the in-process LMS cookies/session/token.
- The user does not need to see a developer-style "download plan" as a primary action.
- The user must be able to choose or enter where downloads are saved.

Product decisions:

1. Keep session/token persistence in memory only for now.
   - Reuse the existing D10a `SsuLectureService` session/cache for one app process.
   - Do not write cookies, `xn_api_token`, bearer tokens, or passwords to disk.
   - Keep `.env` as terminal/dev fallback until Tauri has a native first-run login flow.
   - De-emphasize Keychain copy in the browser prototype. Keychain storage is implementation detail/fallback, not the main UX promise.

2. Promote direct download over plan.
   - Primary CTA should be "다운로드 시작" via background job.
   - Dry-run/plan can remain as a secondary advanced diagnostic action while the prototype is still being verified, but it should not be framed as something normal users need.

3. Add download path control.
   - Browser prototype cannot open a native folder picker reliably for arbitrary local backend paths.
   - Provide a visible output directory text field with a safe default.
   - Pass `output_dir` to single direct download, single background job, selected batch, and uncompleted/batch plan where applicable.
   - Tauri D10 should replace this text field with a native directory picker.

Acceptance criteria:

- Default user copy says the app logs in once per app run and reuses the active LMS session.
- Default user copy does not present macOS Keychain as required.
- UI exposes a download folder field.
- API client sends `output_dir` for download/plan/job/batch requests.
- Primary single-download action is background `다운로드 시작`, not "download plan".
- Plan actions are marked advanced/diagnostic if retained.
- Full tests/build pass.

Verification completed:

- `uv run pytest -q` => 88 passed.
- `cd frontend && npm run test:contract && npm run build` => passed.

After D10b:

1. D10 Tauri shell with native folder picker.
2. Tauri process supervision for Python API.
3. Batch background jobs/cancel parity.
4. First-run login/session UX polish inside the desktop shell.
