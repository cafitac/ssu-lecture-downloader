# HTTP-First Network Discovery Plan

Status: draft
Purpose: define how to investigate Soongsil LMS login and listing with pure `curl-cffi` first.

## Decision

Start with pure HTTP login/network discovery using `curl-cffi`.

Do not start with Playwright bootstrap. Playwright is reserved for a later fallback only if the HTTP-first path hits a concrete blocker.

The previous project can be used as reference, especially `soongsil_scrapper.py` and `video_module_navigator.py`, but the new implementation should be written cleanly from scratch.

## Why pure `curl-cffi` first

- The first MVP is data listing, not video playback.
- Old code already suggests the login flow was reproducible with requests-style HTTP.
- If successful, this gives the fastest path for course/week/lecture listing.
- Understanding the network flow first will make later Electron UI and fallback behavior cleaner.

## Old-code reference summary

Reference only; do not copy blindly.

Old `soongsil_scrapper.py` login flow:

1. GET LMS root:
   - `https://lms.ssu.ac.kr/`
2. POST SmartID login process:
   - base host: `https://smartid.ssu.ac.kr`
   - path family: `/Symtra_sso/...`
   - payload included login type fields, user id, password.
3. Parse JavaScript redirect:
   - old code searched for `parent.location.href = '...'`.
4. Build LMS SSO callback:
   - host: `https://lms.ssu.ac.kr`
   - path family: `/xn-sso/gw-cb.php?...`
5. Follow callback and iframe page.
6. Parse iframe `src` from returned HTML.
7. Fetch iframe content.
8. Parse Canvas `login_form` action and hidden inputs.
9. Parse `window.loginCryption(...)` data from JS.
10. Decrypt page-provided encrypted token with page-provided private key material.
11. POST final Canvas login form.
12. Confirm login from final URL/body.

Old `video_module_navigator.py` data-listing flow:

1. Read cookie named `xn_api_token`.
2. Use `Authorization: Bearer <xn_api_token>`.
3. Call:
   - `https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/modules?include_detail=true`
4. Identify video items:
   - outer `content_type == attendance_item`, or
   - nested `content_data.item_content_data.content_type` in `everlec`, `mp4`, `video`, `movie`, or
   - nested duration exists.

## New clean implementation shape

### `NetworkTrace`

Record a redacted trace entry for every request/response.

Fields:

- step name,
- method,
- URL with sensitive query values redacted,
- status code,
- final URL with sensitive query values redacted,
- elapsed time,
- request header names only, or redacted selected headers,
- response header names and safe values only,
- cookie names set by response, not values,
- body size,
- optional redacted body preview in debug mode.

Never store raw:

- password,
- cookies,
- bearer token,
- decrypted login token,
- private key material,
- signed media URL query values.

### `SsuHttpLoginClient`

Responsibilities:

- own a `curl_cffi.requests.Session`,
- set browser impersonation and headers,
- execute login steps as explicit methods,
- return a typed `SessionState`,
- emit redacted `NetworkTrace` entries.

Proposed methods:

- `fetch_lms_entry()`
- `submit_smartid_login(username, password)`
- `extract_sso_redirect(html)`
- `follow_lms_sso_callback(url)`
- `extract_iframe_src(html)`
- `fetch_iframe_login(url)`
- `extract_canvas_login_form(html)`
- `extract_login_cryption(html)`
- `decrypt_canvas_token(encrypted_data, private_key_material)`
- `submit_canvas_login(form, decrypted_token)`
- `verify_canvas_session()`
- `extract_xn_api_token_presence()`

### `LearningXLister`

Responsibilities:

- use the authenticated session,
- discover courses,
- fetch module details,
- write raw ignored payloads if enabled,
- write sanitized payloads for fixtures,
- map raw data to first read model.

Proposed methods:

- `list_courses()`
- `list_course_modules(course_id)`
- `extract_lecture_items(module_payload)`
- `normalize_course_detail(raw_payload)`

## First live spike command shape

Credential source for terminal-first development:

- Load credentials from local `.env`.
- Required variables:
  - `SSU_USERNAME`
  - `SSU_PASSWORD`
- Commit `.env.example` with empty placeholders.
- Never commit `.env` or trace/raw files under `.local/`.
- Never print the username/password values; output should say only whether required variables are present.

Tentative command:

`ssu-lecture-downloader discover-login --trace .local/traces/login.json`

Then:

`ssu-lecture-downloader discover-lms --raw-out .local/raw --sanitized-out .local/sanitized`

Output should default to compact human-readable terminal status/table output, with `--json` available for machine-readable redacted output.

Output should include:

- login step status summary,
- cookie names present,
- `xn_api_token`: present/missing only,
- course count,
- module/lecture count,
- elapsed time.

The `--json` shape should contain only redacted/safe fields and should be suitable for tests, fixture generation, and later UI/local API integration.

## Failure classification

If pure HTTP login fails, classify it before falling back:

- `smartid_login_rejected`
- `sso_redirect_missing`
- `lms_callback_failed`
- `iframe_src_missing`
- `canvas_form_missing`
- `login_cryption_missing`
- `token_decryption_failed`
- `canvas_login_failed`
- `xn_api_token_missing`
- `module_api_forbidden`
- `unknown_response_shape`

Only after a specific class is captured should Playwright fallback be considered.

## Security notes

The old code printed some sensitive intermediate details during debugging. The rewrite must not do that.

Rules:

- `.env` is for local terminal development only and must be gitignored.
- `.env.example` may document variable names but must not include real credentials.
- Log cookie names, never cookie values.
- Log token presence/length maybe, never token content.
- Log credential variable presence/missing, never values.
- Redact all query parameters by default in final user-facing output.
- Store raw payloads only under ignored `.local/` paths.
- Prefer sanitized fixtures for tests.

## Acceptance criteria for this discovery plan

The first implementation slice should be considered successful if it can produce one of these outcomes:

1. Full success:
   - pure `curl-cffi` login works,
   - `xn_api_token` is present,
   - at least one authenticated LMS/Canvas/learningx request succeeds.

2. Useful partial success:
   - login flow progresses to a specific step,
   - failure class is precise,
   - redacted trace shows what needs fallback/research.

3. Data success:
   - course/module/lecture structure is retrieved and sanitized,
   - normalized read model can be drafted from it.
