# Desktop Product Flow

Status: draft

## Primary user journey

1. User opens the desktop app.
2. User enters LMS ID/password.
3. App authenticates locally.
4. App loads available LMS course information.
5. User selects a course.
6. App shows weeks/modules and lecture items.
7. User filters or sees watched/unwatched lectures.
8. User selects a lecture.
9. App resolves the media source.
10. User downloads the video.
11. App marks the video as downloaded and ready for transcript extraction.
12. Later, user starts transcript extraction to produce lecture text.

## Screen map

### 1. Login

Purpose: get authenticated session.

Visible elements:

- App title: `SSU Lecture Downloader`
- LMS ID input
- Password input
- Login button
- “Use existing local session” if a valid session exists
- Short local-only/security note
- Error box for login failure

Hidden/debug-only:

- raw cookies
- raw tokens
- redirect URLs with sensitive query params

### 2. Course dashboard

Purpose: overview of what the user can access.

Visible elements:

- session/account status, minimally displayed
- course cards/table
- default dashboard filter: current/latest active term only (`ended=False`), not legacy hard-coded term ids
- visible term/semester selector so the user can intentionally switch to older completed terms
- course name
- professor/term if available
- total lecture count
- unwatched count
- downloaded count
- sync/refresh button

### 3. Course detail / week learning view

Purpose: mirror the LMS mental model.

Visible elements:

- course title
- week/module tree
- lecture rows under each week
- status badges:
  - watched
  - unwatched
  - partially watched if available
  - download ready
  - downloaded
  - transcript ready
- duration
- last synced time

Actions:

- resolve media
- download
- open LMS page
- open downloaded file if present

### 4. Lecture detail

Purpose: focused action panel for one lecture.

Visible elements:

- lecture title
- course/week/module
- LMS completion status
- duration
- media resolution status
- download destination
- transcript prep status

Actions:

- resolve source
- download video
- cancel/retry download
- prepare transcript extraction
- open local video
- open local transcript when available later

Debug drawer, off by default:

- redacted request chain
- resolver lane used: `curl-cffi` or `Playwright fallback`
- media candidate classification
- failure reason if resolution failed

### 5. Download queue / library

Purpose: manage files.

Visible elements:

- active download jobs
- progress bars
- speed/ETA if cheap to compute
- completed lectures
- failed jobs with retry
- local file path

Actions:

- pause/cancel if supported
- retry
- reveal in Finder
- change download folder

### 6. Transcript preparation

Purpose: prepare for lecture-to-text extraction.

Initial MVP can be a readiness state, not full transcription.

Visible elements:

- downloaded video exists
- audio extraction required/not required
- estimated duration
- transcript job status
- future provider/model placeholder

Actions:

- prepare audio
- start transcript extraction later

## Data displayed from LMS

Target data:

- courses currently visible to the authenticated user,
  - default read model should include only the latest active term (`ended=False`),
  - retain `term_id`/`ended` metadata internally for UI filtering and older-term selection,
- course IDs/names,
- modules/weeks,
- lecture item IDs/titles,
- watched/unwatched/completed state,
- duration,
- content provider/type such as `everlec` or `mp4`,
- module item URL,
- possible availability/deadline if API exposes it.

## User settings

Minimum settings:

- download directory,
- prefer fast HTTP resolver,
- allow Playwright fallback,
- keep session after app closes: off by default unless user opts in,
- debug logging level.

Later settings:

- transcript provider/model,
- audio extraction format,
- filename template,
- concurrency limit,
- semester/course filters.

## UI principles

- Korean-first labels are appropriate because the target LMS/course data is Korean.
- Keep design restrained and practical.
- Do not expose raw technical objects in primary screens.
- Show resolver/download state in human terms.
- Make failures actionable:
  - “로그인이 만료됐어요. 다시 로그인해 주세요.”
  - “HTTP 고속 해석에 실패해서 브라우저 방식으로 다시 시도할 수 있어요.”
  - “다운로드 권한 확인에 실패했어요. LMS에서 강의가 열리는지 확인해 주세요.”

## Desktop implementation note

Even if the final shell is Electron, do not put LMS scraping logic in the renderer. The renderer should call a trusted local backend/bridge.

Electron split:

- Renderer: React UI.
- Main process: app lifecycle, file dialogs, local process management.
- Python core/local API: LMS logic, resolver, downloader, transcript prep.

## MVP UI boundary

The first visual MVP does not need full packaging.

Suggested staged UI:

1. Local API + simple web UI in browser.
2. Wrap the same UI in Electron.
3. Add native download folder/open-file features.
4. Package later.
