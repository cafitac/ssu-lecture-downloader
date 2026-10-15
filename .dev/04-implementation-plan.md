# Implementation Plan

Status: draft

> This plan intentionally starts before product code. The current project should remain planning-only until the user approves a development scope.

## Current approved scope

D0: planning/docs only.

No product scaffold, no LMS login, no credentials, no live network calls, and no downloads yet.

## Recommended first implementation scope after approval

D1: pure `curl-cffi` login/network discovery.

Why D1 first:

- The user wants to inspect the network flow directly rather than start from Playwright.
- The first MVP depends on understanding LMS data shape.
- Old code suggests the login flow may be reproducible with requests-style HTTP.
- A redacted network trace will make later fallback decisions precise instead of speculative.

## D1 task breakdown: pure HTTP login/network discovery

### Task 1: Create minimal project scaffold

Files:

- `pyproject.toml`
- `.gitignore`
- `.env.example`
- `README.md`
- `src/ssu_lecture_downloader/__init__.py`
- `tests/`

Dependencies:

- Python 3.10+
- `curl-cffi`
- `pydantic>=2`
- `pytest`
- `beautifulsoup4`
- `python-dotenv` or equivalent `.env` loading support.
- `cryptography` if the current login flow still requires token decryption.

Gitignore requirements:

- `.env`
- `.env.*`
- `.local/`
- downloaded media/output directories when they are introduced later.

Verification:

- `uv run pytest -q`

### Task 2: Add redaction utility first

Files:

- `src/ssu_lecture_downloader/redaction.py`
- `tests/test_redaction.py`

Required behavior:

- redact cookies,
- redact authorization headers,
- redact password-like fields,
- redact token-like query params,
- redact private-key/decrypted-token material,
- preserve useful host/path/status context.

Verification:

- tests prove no token/cookie/password/private-key literal survives.

### Task 3: Add network trace model

Files:

- `src/ssu_lecture_downloader/trace.py`
- `tests/test_trace.py`

Required behavior:

- typed request/response trace entries,
- elapsed time,
- method/status/final URL,
- cookie names only,
- redacted URLs/headers/body previews,
- JSON export.

Verification:

- trace JSON contains no secret literals.

### Task 4: Add `curl-cffi` session wrapper

Files:

- `src/ssu_lecture_downloader/http_client.py`
- `tests/test_http_client.py`

Required behavior:

- browser-like session creation,
- safe default headers,
- step-named GET/POST helpers,
- trace capture,
- redirect/final URL capture.

Verification:

- tests use a local fake HTTP server or mocked responses.

### Task 5: Implement login flow steps as explicit methods

Files:

- `src/ssu_lecture_downloader/auth.py`
- `tests/test_auth_parsing.py`

Start with parser tests before live calls:

- parse JavaScript redirect from SmartID response,
- parse iframe `src`,
- parse Canvas `login_form`,
- parse login-cryption call shape,
- classify missing pieces.

Then implement live sequence behind CLI command.

Proposed methods:

- `fetch_lms_entry()`
- `submit_smartid_login()`
- `extract_sso_redirect()`
- `follow_lms_callback()`
- `extract_iframe_src()`
- `fetch_iframe_login()`
- `extract_canvas_login_form()`
- `extract_login_cryption()`
- `decrypt_canvas_token()` if still required
- `submit_canvas_login()`
- `verify_session()`

Verification:

- parser tests pass using sanitized snippets.
- live command produces a redacted step summary when explicitly run by the user.

### Task 6: Add discovery CLI

Files:

- `src/ssu_lecture_downloader/cli.py`
- `tests/test_cli.py`

Commands:

- `ssu-lecture-downloader login --trace .local/traces/login.json`
- `ssu-lecture-downloader login --trace .local/traces/login.json --json`
- `ssu-lecture-downloader discover-session`
- `ssu-lecture-downloader discover-session --json`

Behavior:

- loads `SSU_USERNAME` and `SSU_PASSWORD` from local `.env` during terminal-first development,
- runs pure HTTP login,
- writes redacted trace,
- prints compact human-readable step status by default,
- supports `--json` for redacted machine-readable output,
- prints only step status, cookie names, `xn_api_token` presence, elapsed time.

Verification:

- module invocation works:
  - `uv run python -m ssu_lecture_downloader.cli --help`
- installed script works:
  - `uv run ssu-lecture-downloader --help`

### Task 7: Live failure classification

Files:

- `src/ssu_lecture_downloader/errors.py`
- tests as needed.

Failure classes:

- `smartid_login_rejected`
- `sso_redirect_missing`
- `lms_callback_failed`
- `iframe_src_missing`
- `canvas_form_missing`
- `login_cryption_missing`
- `token_decryption_failed`
- `canvas_login_failed`
- `xn_api_token_missing`
- `unknown_response_shape`

Verification:

- simulated missing-shape tests map to stable error codes.

## D2 task outline: LMS data discovery/listing

Only after D1 succeeds or yields a usable authenticated session.

Tasks:

1. Discover course IDs from LMS/Canvas/LearningX surfaces.
2. Add `LearningXLister`.
3. Call module API:
   - `https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/modules?include_detail=true`
4. Store raw ignored payloads under `.local/raw/`.
5. Store sanitized payloads under `.local/sanitized/`.
6. Print compact course/week/lecture summary.
7. Measure sync timing.

No download in D2.

## D3 task outline: read model stabilization

Tasks:

1. Add Pydantic models:
   - `Course`
   - `CourseModule` or `CourseWeek`
   - `LectureItem`
   - `CompletionState`
2. Map sanitized real payloads to normalized models.
3. Add fixture-based tests.
4. Preserve unknown fields for debugging without exposing them in normal UI output.

## D4 task outline: local API/bridge for UI

Tasks:

1. Decide local API vs stdio bridge.
2. Expose session state.
3. Expose course list.
4. Expose course detail/week/lecture list.
5. Ensure all responses are normalized/redacted.

## D5 task outline: Electron read-only dashboard

Tasks:

1. Electron + React/Vite shell.
2. Login/session screen.
3. Course list.
4. Course detail with week/module tree.
5. Watched/unwatched badges.
6. Sync status and error display.

No media resolve/download yet.

## D6 task outline: media resolver

Tasks:

1. Fetch selected module item page.
2. Parse LTI forms/iframe URLs.
3. Follow viewer resources with `curl-cffi`.
4. Parse XML/HTML/JS for media candidates.
5. Add candidate validation with HEAD/Range.
6. Add Playwright fallback only for concrete HTTP blockers.

No bulk download in D6.

## D7 task outline: single lecture downloader

Tasks:

1. Streaming MP4 download.
2. Safe filename template.
3. Resume/range support.
4. Download metadata store.
5. Progress events.
6. Single selected lecture download.

## D8 task outline: transcript preparation

Tasks:

1. Transcript job model.
2. Downloaded media metadata.
3. Optional audio extraction prep.
4. Placeholder for Whisper/local/API transcription.

## Verification gates

Before reporting any implementation slice complete:

- focused tests pass,
- full tests pass,
- no secrets in logs/output,
- no downloaded media committed,
- `.gitignore` protects sessions, cookies, logs, videos, local DBs,
- CLI smoke verifies both default compact output and `--json` output for the implemented surface,
- fallback to Playwright is not introduced unless a concrete HTTP failure class is documented.
