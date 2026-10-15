# HTTP-First Research Notes

Status: draft

## Decision

Start with pure `curl-cffi` login and network discovery first.

Playwright remains a fallback, but should not be the first path. The first implementation should expose enough redacted network trace data to understand exactly where pure HTTP succeeds or fails.

## Hypothesis

The downloader may not need Playwright for the listing/read-model MVP. The old project suggests the LMS exposes enough information through HTTP SSO, Canvas/LearningX APIs, iframe/LTI resources, and viewer resources that a `curl-cffi` HTTP client can be much faster than browser automation.

The likely best path is:

1. authenticate with pure `curl-cffi`,
2. reuse cookies and `xn_api_token`,
3. call LearningX module APIs,
4. parse course/module/lecture structure,
5. later parse module item / LTI / iframe / viewer resources for media URLs.

## Evidence from old project

Reference file:

`~/Project/school/lms-scrapper/soongsil_scrapper.py`

Old login flow, summarized:

- GET `https://lms.ssu.ac.kr/`
- POST SmartID login process under `https://smartid.ssu.ac.kr/Symtra_sso/...`
- parse JavaScript redirect such as `parent.location.href = '...'`
- follow LMS SSO callback under `https://lms.ssu.ac.kr/xn-sso/...`
- parse iframe `src`
- fetch iframe login page
- parse Canvas `login_form`
- parse `window.loginCryption(...)`
- decrypt a login token using page-provided key material
- POST final Canvas login form
- verify Canvas session.

Reference file:

`~/Project/school/lms-scrapper/video_module_navigator.py`

Key details:

- API token was read from cookie:
  - cookie name: `xn_api_token`
- API header used:
  - `Authorization: Bearer [REDACTED]`
  - `Accept: application/json`
  - `Referer: https://canvas.ssu.ac.kr/learningx/dashboard`
- Module API:
  - `https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/modules?include_detail=true`
- Video-like items:
  - outer `content_type`: `attendance_item`
  - nested `content_data.item_content_data.content_type`: examples include `everlec`, `mp4`, `video`, `movie`
  - nested duration also indicates video.
- Module item URL pattern:
  - `https://canvas.ssu.ac.kr/courses/{course_id}/modules/items/{module_item_id}`

Reference file:

`~/Project/school/lms-scrapper/playwright_video_navigator.py`

Key details for later fallback/media-resolver phase:

- Network logging watched:
  - `video`
  - `mp4`
  - `lti`
  - `iframe`
  - `commons.ssu`
  - `chapter`
  - `media_script`
  - `player`
- Resources observed:
  - `chapter.xml`
  - `media_script_list.xml`
  - `contents/chapter.xml`
  - `contents/media_script_list.xml`
- Page probing checked:
  - iframe `src`
  - `#viewer-root`
  - `video` tags and `video.src`
  - player-related elements.

## Research order

### Step 1: Pure HTTP login discovery

Use `curl_cffi.requests.Session` with Chrome impersonation.

Trace every request/response in redacted form.

Target outputs:

- login step status,
- final authenticated URL/body signal,
- cookie names present,
- `xn_api_token` present/missing,
- first authenticated Canvas/LMS request success.

### Step 2: Course and module listing

Call:

`GET https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/modules?include_detail=true`

Need to discover course IDs from either:

- dashboard API,
- old project course listing logic,
- Canvas course page/API,
- module URLs already visible in page HTML.

### Step 3: Data structure capture

Save two outputs:

- raw payloads under ignored `.local/raw/`,
- sanitized payloads under `.local/sanitized/` suitable for fixtures/planning.

Normal output should be a compact table/JSON summary, not raw LMS payload dumps.

### Step 4: Later lecture item/media resolution

For one item, after the listing model is understood:

1. Fetch module item URL.
2. Parse redirects/forms/iframe.
3. If LTI form exists, reproduce the form POST using `curl-cffi`.
4. Fetch iframe/viewer URL.
5. Parse viewer HTML/JS.
6. Fetch `chapter.xml` and `media_script_list.xml` when referenced.
7. Search every response body for:
   - `ssu-toast.commonscdn.com`
   - `commonscdn.com`
   - `contents/media_files/main_`
   - `.mp4`
   - `.m3u8`
   - `video.src`
   - encoded URLs.

## `curl-cffi` client requirements

Use `curl_cffi.requests.Session` with browser impersonation, likely Chrome.

Design concerns:

- preserve cookies across requests,
- stable user-agent and accept headers,
- referer/origin set from the previous page,
- redirect history captured for debugging,
- response bodies optionally saved with redaction in debug mode,
- all secrets redacted before logging.

## Failure classes to capture

- `smartid_login_rejected`
- `sso_redirect_missing`
- `lms_callback_failed`
- `iframe_src_missing`
- `canvas_form_missing`
- `login_cryption_missing`
- `token_decryption_failed`
- `canvas_login_failed`
- `xn_api_token_missing`
- `dashboard_api_unknown`
- `module_api_forbidden`
- `unknown_response_shape`

## Unknowns

- Whether pure HTTP login still works with the current SSO flow.
- Whether `curl-cffi` fingerprinting is necessary for all steps or only some.
- Whether the final LMS/Canvas session sets `xn_api_token` consistently.
- Which endpoint provides the current enrolled course list most cleanly.
- Whether media URL is embedded directly in XML/JS or generated by runtime JS.
- Whether media CDN URL is signed/short-lived.
- Whether direct MP4 supports Range/resume.
- Whether HLS appears for some lectures.
- Whether some courses use a different content provider.

## Research acceptance criteria

Before product code goes beyond data-discovery, prove at least one of these:

1. Pure `curl-cffi` login reaches Canvas/LMS authenticated state.
2. `xn_api_token` is present and can call a LearningX API.
3. A concrete failure class is captured with a redacted trace that explains why fallback is needed.

If pure HTTP fails, do not immediately jump into UI work. First document the blocker and then decide whether Playwright fallback should be added for that step only.
