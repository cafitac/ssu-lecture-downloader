# SSU Lecture Downloader Master Roadmap

Status: planning-only
Project path: `~/Project/ssu-lecture-downloader`
Reference project: `~/Project/school/lms-scrapper`

## Final product goal

Build a local desktop app, likely Electron or Tauri-style, for the user's own Soongsil LMS lectures.

The app should let the user:

1. enter Soongsil LMS ID/password locally,
2. authenticate to LMS/Canvas safely,
3. view LMS information that is already visible to the user:
   - enrolled/current courses,
     - default view must prefer the latest active term (`ended=False`) rather than old hard-coded term ids,
     - older completed terms should remain available through an explicit term/semester filter,
   - course weeks/modules,
   - watched/unwatched lecture items,
   - lecture title, week, duration, completion status,
4. select a specific week/lecture,
5. enter or open the lecture item flow,
6. resolve the real media source URL quickly,
7. download the lecture video,
8. prepare the downloaded media for later lecture-text extraction/transcription.

The UI should feel like a clean study utility, not a terminal-only scraper.

## Important product posture

This is a personal authenticated downloader for lectures the user can already access. It must not bypass authorization, DRM, encryption, or access controls.

The app should avoid exposing implementation details to the normal user screen. Raw API tokens, cookies, signed URLs, network logs, and debug payloads belong only in redacted debug views or local files that are ignored by git.

## Architecture decision

The product should be UI-first, but the resolver core should be independent of the UI.

Recommended high-level shape:

- `desktop/`: Electron or Tauri frontend shell.
- `backend/` or `src/ssu_lecture_downloader/`: local Python service/library that owns LMS auth, fast HTTP crawling, media resolution, download, and transcription prep.
- `data/`: ignored local runtime data such as config, session state, download metadata, and redacted logs.
- `.dev/`: planning and design docs.

Initial implementation should not start with Electron immediately. Start with a library/CLI or local API core that can be tested, then wrap it with a desktop UI.

## Fast-lane vs fallback resolver

### Fast lane: `curl-cffi`

Primary path for speed.

Target use:

- browser-like TLS/client fingerprinting,
- authenticated session cookies,
- `xn_api_token` extraction/use,
- LearningX/Canvas API calls,
- iframe/LTI form parsing,
- viewer/player HTML/JS/XML parsing,
- direct media URL discovery without a browser.

The old project suggests this is plausible because it already used a session token against:

`https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/modules?include_detail=true`

and video items are visible from module API payloads.

### Fallback: Playwright

Use only when necessary:

- SSO/login cannot be completed reliably through HTTP alone,
- LTI launch requires JS/browser state,
- player source is generated only after JS execution,
- fast-lane resolver fails for a particular lecture.

Playwright fallback should hand off cookies/network-discovered URLs to the same core resolver model. It should not become the main implementation if `curl-cffi` works.

## Product phases

### Phase 0: Planning and evidence audit

Docs only.

- Define the desktop app target.
- Define HTTP-first `curl-cffi` resolver approach.
- Define Playwright fallback boundary.
- Define UI information architecture.
- Decide Electron vs Tauri after the backend/core boundary is clear.

Exit criteria:

- `.dev/00-master-roadmap.md`
- `.dev/01-http-first-research.md`
- `.dev/02-architecture.md`
- `.dev/03-desktop-product-flow.md`
- `.dev/04-implementation-plan.md`

### Phase 1: Local parser and resolver core fixtures

No live login yet.

- Parse sanitized old network logs/HTML/XML.
- Extract candidate media URLs.
- Classify lecture video vs ignored assets such as `preloader.mp4`.
- Build domain models for Course, Week/Module, LectureItem, MediaCandidate.

Exit criteria:

- Core tests pass.
- CLI can scan local fixtures and show sanitized lecture media candidates.

### Phase 2: Fast authenticated LMS listing

Live LMS access, but no download.

- Implement `curl-cffi` session.
- Log in or import a browser/Playwright-authenticated session.
- Extract `xn_api_token`.
- List courses/modules/weeks/unwatched lectures.

Exit criteria:

- CLI or local API lists the user's courses and lecture items.
- Secrets are redacted from all logs.

### Phase 3: Fast media resolution

- Given one lecture item, follow module item/LTI/viewer flow with `curl-cffi`.
- Parse iframe/form/player/XML/JS to find original MP4/HLS candidates.
- Use Playwright fallback only for failures.

Exit criteria:

- One selected lecture resolves to a downloadable media candidate.

### Phase 4: Download manager

- Stream direct MP4 downloads.
- Support progress, resume, cancel, retry, checksum/size verification, safe filenames.
- Store metadata for later transcript extraction.

Exit criteria:

- One lecture can be downloaded from the UI or CLI.

### Phase 5: Desktop UI MVP

- Login screen.
- Course list.
- Course detail with week/module tree.
- Watched/unwatched badges.
- Lecture detail panel.
- Resolve/download button.
- Download queue/progress.
- Local library/downloaded videos view.
- Placeholder for transcript extraction status.

Exit criteria:

- Browser/desktop smoke test verifies the main flow.

### Phase 6: Lecture text extraction prep

First version should prepare for transcription rather than overbuild it.

- Store downloaded file path and metadata.
- Extract audio to a working path if needed.
- Define transcript job model.
- Later plug in Whisper/local model/API transcription based on user choice.

Exit criteria:

- Downloaded lecture appears as “ready for transcript extraction”.

## Approval scopes

Recommended approval sequence:

- `D0`: docs/planning only.
- `D1`: local fixture parser/core models only, no LMS login/network.
- `D2`: `curl-cffi` authenticated listing, no download.
- `D3`: media resolver with Playwright fallback, no bulk download.
- `D4`: single-video download manager.
- `D5`: desktop app MVP.
- `D6`: transcript prep/extraction.

Current recommended next scope: finish `D0`, then ask for `D1` approval.
