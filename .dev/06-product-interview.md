# Product Interview Notes

Status: in-progress
Purpose: clarify product direction before implementation.

## Confirmed direction

- The final product is a local desktop app, not just a CLI.
- The app should let the user log in to Soongsil LMS, browse visible course/week/lecture information, select a weekly lecture, download video, and prepare for lecture text extraction/transcription.
- The implementation should investigate a fast `curl-cffi` path first, with Playwright as fallback only when necessary.
- Planning should happen in `.dev` before product code starts.
- Development should be agile: plan the full future path, but implement small slices and validate quickly.
- Old code may be used as reference, but the new project should be cleanly rewritten from scratch where possible.

## Interview flow

Ask one focused question at a time. After each answer, update this note with:

- raw answer,
- synthesized decision,
- assumptions,
- open follow-up questions.

## Answers

### Q1. What is the main first-success scenario for the app?

Raw answer:

- "A 만 빠르게 해봐도 될 것 같아"
- Meaning: for the first MVP, it is enough if login quickly shows currently enrolled courses, weeks/modules, and unwatched lecture information.

Synthesized decision:

- First MVP success is not downloading yet.
- First MVP success is a fast LMS course/week/lecture dashboard.
- The first technical slice should prioritize authenticated listing speed and data modeling over media resolution/download.
- `curl-cffi` should be evaluated early because the first success path is read/list heavy and likely API-friendly.

Assumptions:

- The UI can initially show read-only LMS information.
- Download and transcript extraction can remain visible as future states/placeholders, but not implemented in MVP-1.
- Playwright is acceptable only if needed as fallback, not as the default listing engine.

### Q2. What should the first validation shape be?

Raw answer:

- User does not mean future phases should be ignored; all future phases should be written down and concretized in advance.
- Development should proceed agile-style, validating each slice quickly.
- User chooses B: first validate via CLI/local API, then attach Electron UI.
- Since the LMS data structure is not known yet, seeing and understanding the data first is the highest priority.

Synthesized decision:

- Do not start with Electron UI.
- First build a data-discovery/read-model slice.
- The first live slice should output the raw/sanitized LMS structure and a normalized course/week/lecture read model.
- Electron UI comes after the data model is understood and stable enough.
- Future download/transcript phases must still be planned in `.dev`, even if not implemented first.

Assumptions:

- A CLI or local API proof is acceptable as the first visible result.
- The first output can include developer-oriented JSON/table output, as long as secrets are redacted.
- Raw LMS payloads should be stored only in ignored local artifacts and should have a sanitized export path for planning/testing.

### Q3. What login/session approach should Slice 1 try first?

Raw answer:

- "A 로 그냥 네트워크 다 까보면서 진행하고 싶어."
- User wants to try pure `curl-cffi`/HTTP login first while inspecting the full network flow.
- It is acceptable to reference and borrow ideas from the previous project.
- But the recommendation/preference is to rewrite cleanly from scratch as much as possible.

Synthesized decision:

- Slice 1 should start with pure `curl-cffi` login/network discovery, not Playwright bootstrap.
- Build a network trace/debug layer from the beginning, but redact secrets aggressively.
- Old `lms-scrapper` code is reference material only, not code to copy wholesale.
- Playwright remains fallback, but only after the HTTP-first path is understood and blocked by a concrete reason.

Relevant old-code evidence:

- Old `soongsil_scrapper.py` performed a multi-step HTTP SSO flow:
  1. GET `https://lms.ssu.ac.kr/`
  2. POST SmartID login process under `https://smartid.ssu.ac.kr/Symtra_sso/...`
  3. parse JavaScript redirect to LMS SSO callback,
  4. fetch iframe URL,
  5. parse Canvas login form and encrypted login token script,
  6. decrypt token using a page-provided key,
  7. POST final Canvas login form.
- Old `video_module_navigator.py` then used `xn_api_token` cookie as bearer token for the LearningX module API.

Implementation implication:

- The clean rewrite should model the login flow as explicit steps and save redacted request/response metadata for each step.
- Do not print raw private keys, decrypted tokens, cookies, passwords, bearer tokens, or signed URLs.
- The first useful output may simply be: login step trace, cookies present by name only, `xn_api_token` presence, and first successful authenticated dashboard/API request.

### Q4. How should credentials be provided during terminal-first development?

Raw answer:

- "일단 터미널 개발이니까 .env로 하자. ui 가 없으니까"
- Meaning: while development is terminal-only and no UI exists yet, use `.env` for credentials.

Synthesized decision:

- Use a local `.env` file for D1/D2 terminal development.
- Expected environment variables:
  - `SSU_USERNAME`
  - `SSU_PASSWORD`
- `.env` must be ignored by git.
- Provide `.env.example` with variable names only, no real values.
- CLI should load `.env` automatically for local development, but should never print the credential values.
- macOS Keychain or UI-managed secrets can be revisited when the Electron/local desktop app phase starts.

Assumptions:

- `.env` is acceptable only for local development, not as a packaged-app secret strategy.
- Redacted trace and logs must treat `SSU_USERNAME` as sensitive enough to mask in public/sanitized output, and `SSU_PASSWORD` as always fully secret.

### Q5. What should the first CLI output format be?

Raw answer:

- "C 로 가자"
- Meaning: default to compact human-readable terminal output, with a `--json` option for machine-readable output.

Synthesized decision:

- CLI commands should print compact table/status output by default.
- Add `--json` for machine-readable output.
- JSON output should use the same redacted/sanitized data model as the default output; it must not include raw cookies, tokens, passwords, private keys, or signed media URLs.
- This keeps terminal development ergonomic while preserving a path toward fixtures, tests, and later UI/local API integration.

Assumptions:

- Table/default output is optimized for quick human inspection.
- `--json` is stable enough for tests and tooling, but can still evolve during early discovery if versioned or documented.

## Current MVP-1 definition

MVP-1: after pure HTTP login/session discovery succeeds, quickly display LMS-visible information:

- enrolled/current courses,
  - Default course discovery must prefer currently active LMS courses, not old hard-coded term IDs.
  - LearningX exposes `term_id` and `ended`; current-term discovery should select the latest term with `ended=False` courses.
  - UI should surface term/semester filtering explicitly so old 1st-year courses do not appear as the default when the user is currently in a later year.
- course weeks/modules,
- lecture items,
- watched/unwatched or completed/uncompleted status when available,
- duration/title metadata when available.

Explicitly not the first implemented slice, but still part of the planned product:

- actual video download,
- media URL resolution,
- transcript extraction,
- desktop Electron UI,
- bulk archive/library management.

## Development strategy

- Plan the full product path.
- Implement in thin agile slices.
- Validate each slice with concrete output before broadening.
- Prefer data discovery and read models before UI design.
- Start with pure `curl-cffi` HTTP/network discovery.
- Keep Playwright as fallback only after a specific HTTP blocker is documented.
- Reference old code, but rewrite cleanly.

## Open questions

6. Before starting D1 implementation, should we create the minimal Python scaffold now under the new project, or continue one more planning pass for command/API names?
