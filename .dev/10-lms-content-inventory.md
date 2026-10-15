# LMS content inventory and next feature scope

Date: 2026-05-25
Scope: live LearningX/Canvas module inspection using the existing authenticated HTTP path. Secrets, cookies, bearer tokens, signed Commons URLs, content IDs, and student identifiers must not be stored here.

## Why this document exists

The downloader currently behaves like a video-only lecture downloader. The live LMS module API shows richer week content: videos, PDF lecture materials, boards, assignments, quizzes, wiki pages, and section headers. Before expanding the UI/downloader, we need a documented inventory of what the LMS exposes and a product decision for how much of that surface the app should support.

## Live source endpoints checked

1. Course list:
   - `GET https://canvas.ssu.ac.kr/learningx/api/v1/learn_activities/courses`
   - optional `term_ids[]` selects semester/term.
2. Course modules/weeks:
   - `GET https://canvas.ssu.ac.kr/learningx/api/v1/courses/{course_id}/modules?include_detail=true`
3. Attendance-item detail and Commons metadata for downloadable content:
   - Canvas module item page: `/courses/{course_id}/modules/items/{module_item_id}`
   - LTI launch form action: `/learningx/lti/lecture_attendance/items/view/{attendance_item_id}`
   - attendance detail: `/learningx/api/v1/courses/{course_id}/attendance_items/{attendance_item_id}`
   - Commons metadata: `/learningx/api/v1/commons/contents?content_id={content_id}`
   - Commons download HEAD/GET: signed redirect to `commons.ssu.ac.kr/index.php?...DownloadContent2...`

## Current-term sample findings

Current active term detection selected the latest term with `ended=False`.

Observed current-term course/module sample:

- Active courses: 8
- Sampled active courses for item inventory: 5
- Modules sampled: 68
- Module items sampled: 81

Top-level module fields observed:

- `module_id`
- `title`
- `position`
- `published`
- `unlock_at`
- `is_child_content`
- `week_position`
- `module_items`

Top-level module-item fields observed:

- `module_item_id`
- `title`
- `content_type`
- `content_id`
- `content_data`
- `position`
- `published`
- `indent`
- `url`
- `is_child_content`
- `completion_determinable`
- `completed` on many actionable items
- `attendance_status` on some attendance/video items

Observed top-level `content_type` counts in sampled current courses:

- `attendance_item`: 56
- `assignment`: 10
- `quiz`: 6
- `board`: 6
- `sub_header`: 2
- `wiki_page`: 1

Observed inner `content_data.item_content_data.content_type` counts:

- `movie`: 34
- `pdf`: 22
- missing/not applicable: 25

Important conclusion: lecture materials can appear as `content_type=attendance_item` with inner `item_content_data.content_type=pdf`. The current `extract_lecture_items` path is too video-oriented and should become a generalized week-content extractor.

## Duration and size metadata

Video/movie items expose duration directly:

- Path: `item.content_data.item_content_data.duration`
- Shape: numeric seconds, often float seconds, for example `2309.57`.
- UI format recommendation: keep raw seconds in API, display as `MM:SS` or `H:MM:SS` depending on length.

PDF/material items do not expose duration:

- `duration`: null/missing
- `total_file_size`: available in `item_content_data` and Commons metadata.
- `file_name` appears on PDF item_content_data in the module API sample.

Video/movie size note:

- Module API `total_file_size` and Commons metadata `total_file_size` may differ. The existing download hardening rule still stands: verify final bytes from disk after download.

## Downloadability check

A live PDF attendance item was resolved through the same existing Commons path used for videos:

- sample type: `attendance_item` / inner `pdf`
- resolve result: ok
- content_type: `pdf`
- duration: null
- download_size: present
- HEAD status: 307 signed Commons download redirect

A live movie attendance item was also resolved:

- sample type: `attendance_item` / inner `movie`
- resolve result: ok
- duration: present in seconds
- download_size: present
- HEAD status: 307 signed Commons download redirect

Conclusion: PDF 자료 download support should be a small extension of the existing resolver/downloader once item extraction and filename extension mapping are generalized.

## Proposed product surface

The app should show a course detail page that mirrors the LMS week/module structure, not just a video lecture list.

Recommended row model:

- course id/name
- module id/name
- week position
- lesson position, when present
- module item id
- title
- top-level item type: `attendance_item`, `assignment`, `quiz`, `board`, `wiki_page`, `sub_header`, etc.
- normalized kind:
  - `video` for inner `movie`, `mp4`, `video`, `everlec`, or duration-bearing media
  - `material` for inner `pdf` or future downloadable document-like content
  - `assignment`
  - `quiz`
  - `board`
  - `page`
  - `section`
  - `unknown`
- raw inner content type, when present
- completed, when present
- attendance_status, when present
- duration_seconds, when present
- size_bytes, when present
- opened/published/lock/unlock/due/late metadata, when present
- downloadable boolean
- resolvable boolean

## Filtering and selection UX

Minimum useful filters:

1. Week/module selection:
   - single week/module
   - multiple selected weeks/modules
   - all weeks
2. Type filter:
   - all
   - videos
   - materials/documents
   - assignments/quizzes
   - boards/pages
3. Completion filter:
   - all
   - incomplete/unwatched only
   - completed only
4. Downloadability filter:
   - downloadable only
   - display-only LMS items included

Bulk download behavior:

- For selected weeks, download all selected downloadable rows.
- Videos should save as `.mp4` when the actual container is MP4/video-like, even when Commons says `movie`.
- PDFs should save as `.pdf`.
- Future document extensions should be derived from filename/content type/HEAD where available, not blindly from top-level LMS item type.
- Non-downloadable rows such as board/assignment/quiz should remain visible but not selected for download by default.

## Support boundary decision

Recommended support tiers:

Tier 1, implement next:

- Display all module/week rows from the module API.
- Normalize and filter `video` and `material` rows.
- Show duration for video rows.
- Show file size for material/video rows when available.
- Resolve/download `attendance_item` media where inner content type is `movie` or `pdf`.
- Bulk download by selected week(s) and type filter.

Tier 2, display but do not download initially:

- `assignment`
- `quiz`
- `board`
- `wiki_page`
- `sub_header`

These are useful because they make the app match LMS week content, but they should open or show metadata rather than download in the first slice.

Tier 3, investigate only if observed/requested:

- PPT/PPTX/DOCX/HWP/ZIP/audio/HLS or other Commons-backed types.
- Board/assignment attachments that require separate Canvas/LearningX endpoints.
- Embedded external URLs or non-Commons files.

No bypass scope:

- Do not attempt to bypass authorization, DRM, encrypted streams, access controls, or unavailable/locked content.
- Only expose/download items the authenticated LMS session can already access.

## Implementation plan after this doc

1. Add generalized domain model, probably `CourseContentItem`, without removing the current `LectureItem` until API/UI migration is complete.
2. Replace or supplement `extract_lecture_items` with `extract_course_content_items`:
   - include every `module_item`
   - normalize type/kind
   - preserve enough raw IDs for resolver calls
   - keep stable API fields for frontend filters.
3. Add parser unit tests from redacted representative payloads:
   - attendance/movie with duration
   - attendance/pdf with file size and filename
   - assignment
   - quiz
   - board
   - wiki_page
   - sub_header
4. Extend resolver/download planner:
   - allow document-like `attendance_item` content types, starting with PDF
   - choose extension from normalized kind/content_type/file_name/HEAD
   - keep movie/video mapping to `.mp4`.
5. Extend API and frontend:
   - course detail returns week content rows, not only lectures
   - filters by week/type/completion/downloadability
   - selected rows can batch-plan/download.
6. Verify with:
   - `uv run pytest -q`
   - `cd frontend && npm run test:contract && npm run build`
   - live dry-run for one selected week containing both movie and pdf rows
   - one PDF live download smoke to confirm final extension and file size.

## Current code gap

Current `LearningXLister.extract_lecture_items` only returns items accepted by `is_video_item`. This hides PDF attendance materials even though they are exposed in the same module API and can be resolved through Commons.

Current `ResolvedMediaResult` already includes `duration`, `content_type`, and `download_size`, so the downloader core is close. The larger change is product/API/UI modeling: the app needs to stop treating every row as a lecture video.
