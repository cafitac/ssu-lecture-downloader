# Agile Slice Roadmap

Status: draft
Purpose: keep the final product path concrete while developing in fast validated slices.

## Product target

Final target is a local desktop app for Soongsil LMS lecture management:

- login locally,
- display current LMS-visible courses/weeks/lecture items,
- show watched/unwatched or completion state,
- later resolve/download videos,
- later prepare/extract lecture text,
- eventually provide a usable Electron-like desktop UI.

## Development principle

Do not build the final UI first. First discover the LMS data structure and stabilize the read model.

Each slice must produce concrete output and answer one question.

Old code can be referenced, but the new project should be cleanly rewritten unless a small helper is clearly reusable.

## Slice 0: Planning and product interview

Question answered:

- What is the product and first success path?

Output:

- `.dev` planning docs.
- Interview notes.

Status:

- In progress.

## Slice 1: Pure HTTP login/network discovery

Primary question:

- Can pure `curl-cffi` reproduce enough of the Soongsil LMS/Canvas login flow to obtain an authenticated session and inspect LMS data?

Implementation:

- CLI first.
- Use `curl-cffi` from the beginning.
- Load local development credentials from `.env`.
- Add redacted network trace instrumentation from day one.
- Reference old `soongsil_scrapper.py` for flow shape, but rewrite cleanly.
- Do not use Playwright in this slice unless the slice ends by documenting a concrete blocker.

Credential mechanism:

- Use `.env` during terminal-first development.
- Required variables:
  - `SSU_USERNAME`
  - `SSU_PASSWORD`
- Commit `.env.example`, never `.env`.
- Ensure `.gitignore` excludes `.env`, `.env.*`, and `.local/` artifacts.
- Do not print credential values in CLI output or traces.

Output:

- Redacted login/network trace.
- Step-by-step login status in compact table/status format by default.
- `--json` output option using the same redacted fields.
- Cookie names present after each major step.
- `xn_api_token` presence/missing, never value.
- First successful authenticated LMS/Canvas request if possible.

Acceptance criteria:

- Either pure HTTP login succeeds, or failure is classified precisely.
- No raw credential/token/cookie/private-key material is printed or committed.
- The trace is useful enough to decide the next step.

## Slice 2: Data discovery CLI/local API proof

Primary question:

- Can we quickly retrieve and normalize LMS-visible course/week/lecture data?

Preferred implementation:

- CLI first, optionally a tiny local API only if it speeds later UI work.
- Use the authenticated `curl-cffi` session from Slice 1.
- Use Playwright only as later fallback if pure HTTP has a classified blocker.

Input:

- User's LMS credentials from local `.env` during terminal-first development.

Output:

- Redacted raw payload snapshots saved locally under ignored paths.
- Sanitized JSON summary of:
  - courses,
  - weeks/modules,
  - lecture items,
  - watched/unwatched/completion state,
  - durations/titles when available.
- First normalized read model.

Commands envisioned:

- `ssu-lecture-downloader login --trace .local/traces/login.json`
- `ssu-lecture-downloader discover --raw-out .local/raw --sanitized-out .local/sanitized`
- `ssu-lecture-downloader list-courses`
- `ssu-lecture-downloader list-lectures --course <course-id>`

Acceptance criteria:

- Course/week/lecture data is visible in compact terminal output by default.
- Raw sensitive data is not printed.
- `--json` output and sanitized samples can be used as fixtures later.
- Performance is measured roughly: total sync time and per-course/module fetch time.

Deferred:

- media URL resolve,
- video download,
- Electron UI.

## Slice 3: Read model stabilization

Primary question:

- What data shape should the UI depend on?

Output:

- Pydantic models for Course, Week/Module, LectureItem, CompletionState.
- Mapping from raw LearningX/Canvas payloads to normalized model.
- Fixture-based tests from sanitized real outputs.

Acceptance criteria:

- At least one real sanitized payload maps to the normalized read model.
- Unknown fields are preserved enough for future debugging but not leaked into UI by default.

## Slice 4: Local API for UI

Primary question:

- Can a UI consume the stabilized LMS read model without knowing scraper details?

Output:

- Localhost-only API or stdio bridge.
- Endpoints/events:
  - session state,
  - sync courses,
  - list courses,
  - list course detail,
  - list lectures.

Acceptance criteria:

- API returns normalized JSON.
- No raw cookies/tokens in responses.
- Works from a fresh local process.

## Slice 5: Electron UI MVP for read-only dashboard

Primary question:

- Does the desktop UX make sense for browsing LMS info?

Output:

- Electron + React/Vite app shell.
- Login screen or session bootstrap screen.
- Course list.
- Course detail with week/module/lecture tree.
- Watched/unwatched badges.

Acceptance criteria:

- User can open the app, authenticate/session-load, sync, and browse course/week/lecture data.
- No download yet.

## Slice 6: Media resolver research

Primary question:

- Can a selected lecture item resolve to real video media URL quickly?

Output:

- HTTP resolver using `curl-cffi`.
- Playwright fallback resolver.
- Candidate classifier for MP4/HLS/ignored asset.

Acceptance criteria:

- For one selected lecture, a media candidate is found or a clear failure reason is shown.
- Resolver lane and timing are recorded.

## Slice 7: Single lecture download

Primary question:

- Can the app download one selected accessible video reliably?

Output:

- Download job model.
- Streaming download with progress.
- Safe filename generation.
- Resume/retry if feasible.

Acceptance criteria:

- One lecture downloads successfully.
- File size/content checks pass.
- UI shows progress and completion.

## Slice 8: Library and local archive

Primary question:

- Can downloaded lectures be managed locally?

Output:

- Local metadata store.
- Downloaded lecture library screen.
- Open in Finder/open file.

Acceptance criteria:

- Downloaded files remain linked to course/week/lecture metadata.

## Slice 9: Transcript preparation

Primary question:

- Can downloaded lectures be prepared for lecture text extraction?

Output:

- Transcript job model.
- Audio extraction/prep pipeline if needed.
- Placeholder provider/model config.

Acceptance criteria:

- Downloaded lecture is marked “ready for transcript extraction”.

## Slice 10: Transcript extraction

Primary question:

- Can the app generate useful lecture text?

Possible implementations:

- local Whisper/faster-whisper,
- external API later if approved,
- chunking and timestamped transcript storage.

Acceptance criteria:

- Transcript text exists with lecture metadata and timestamps.

## Current next decision needed

Before starting D1 implementation, decide whether to create the minimal Python scaffold now or do one more planning pass for command/API names.
