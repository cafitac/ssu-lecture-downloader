# Design index

- Feature record: ../feature.md
- Feature ID: 20261030-download-folder-layout
- Current slice: L1 (new layout + dedup + safe tidy-up of existing downloads)
- Current PR unit: NONE (proposed LAYOUT-PY-PATHS → LAYOUT-PY-MIGRATION → LAYOUT-UI-TIDY; first one only after PL-PY-PIPELINE MERGED)
- Design profile: STANDARD
- Design status: AWAITING_APPROVAL
- Target/base: develop (planning base e2f0714)

## Source evidence

Code survey 2026-10-30, develop @ e2f0714, read-only:

- `core/download.py`
  - `plan_download_path` (l.79–113): `output_dir / safe_filename(course_name) / safe_filename(f'{title} [{content_id}]').<ext>`; `rename` strategy adds ` (n)`; `skip`/`overwrite` first look for an earlier file of the same id via `_existing_with_content_id` (l.68–76: name ends with `[id].<ext>` in the course folder).
  - `find_downloaded_files` (l.116–135): course detail / page attachments mark an item downloaded when **any** non-`.part` file in the course folder contains `[id]` (so a lone `.srt` counts today).
  - `safe_filename` (l.62–65): replaces `\ / : * ? " < > |` and control chars with `_`, collapses whitespace, strips ` ._`; no NFC, no length cap.
  - `download_to_plan` creates `plan.course_dir` (l.219) — must become the lecture folder.
- `core/lms.py` `extract_course_content_items` (l.126–175): `week_position = module.get("week_position")` (module level only), `lesson_position` from `content_data`, `module_name` = module `name|title`, `content_id` = `item_content_data.content_id` (the same id that appears in file names). `extract_lecture_items` (`LectureItem`) has `module_name` but no week.
- `core/lms_service.py`
  - batch planning works on `CourseContentItem`s (`_select_batch_lectures` l.1682: every `downloadable` item — videos **and** materials); `_plan_batch_item` → `_planned_file(resolved, course_name, fallback_title=lecture.title)` uses the **resolved** title first (`result.title or fallback`).
  - `download_lecture` / `native_download_plan` (l.942–1061) only get `module_item_id`; the content item (week, title) must be looked up from the cached module list.
  - `download_canvas_file` (l.1183–1240): `plan_download_path(lecture_title=stem, content_id=f"canvas-{file_id}")`; request (`CanvasFileDownloadRequest`, api_models l.60) has no week.
- `core/stt.py` `transcript_output_paths` (l.116–123) → `<audio stem>.transcript.txt|md|json`; `transcript_srt_path` → `<audio stem>.srt`; `write_transcript_files` writes srt, md, json, then txt last (complete-set marker).
- `core/postprocess_media.py` `plan_audio_output_path` (l.100): `<video stem>.<fmt>` in the given dir.
- `service/artifacts.py` (develop): `_ItemPaths.for_video(video_path)` derives every output from the video path; PL-PY-PIPELINE keeps `_ItemPaths` inside its new `LecturePipeline` (worktree ~/Project/cafitac/tools/ssu-lecture-downloader-wt/pl-py-pipeline, uncommitted, l.39–48, 118, 443).
- `service/routers/credentials.py` l.112 and `service/routers/lms_pages.py` l.44: `find_downloaded_files` for course detail and page attachments.
- `frontend/src-tauri/src/native_download.rs` l.445–448: native transfer writes to the Python-planned `output_path` and creates parent dirs; native jobs are invisible to the Python `JobRegistry`.
- `frontend/src/lib/content.ts` `groupByWeek` (l.50–63): UI group title = `module_name` or `${week_position}주차` or `기타` — the folder rule mirrors it.
- `frontend/src/components/SettingsDialog.tsx` l.320–333: "저장 폴더" section (변경 disabled while busy: "다운로드가 끝나면 바꿀 수 있어요", 폴더 열기).
- Default root: `platforms/macos.py` l.144–150 `~/Downloads/SSU Lecture Downloader`.
- Real folder (read-only name listing, 2026-10-30): 4 course folders, 43 files: `선대_5_2 [68e66b6c1b816].mp4|.srt|.txt` (legacy `.txt`), `선대_6_1 [68feeb3f3b6dd].srt|.transcript.txt|.md|.json` (no video), `7주차 1강 감성지능 리더십 [65dbf62460d5a] (2).mp4` (rename duplicate), `… [660650405b034].file` (material, generic extension), `UI디자인_1주차_202602 [canvas-4732412].pdf` (Canvas attachment), 7 PDFs in one course. Titles often already contain the week ("5주차_02_웹접근성", "선대_5_2").
- PL-PY-PIPELINE design (approved, digest 8a74ddbd…): `ArtifactPipeline` → per-lecture `LecturePipeline`; output files keep CURRENT atomic rules; owned paths `service/artifacts.py`, `core/stt.py` (local gate hook), `core/lms_service.py` (callback).

## Artifact ledger

| Artifact | Status | Evidence / reason |
|---|---|---|
| Context map | N/A | No bounded-context change; the only external authority (LMS, read-only) is unchanged. Filesystem/UI/sidecar boundaries are drawn in the review-pack target overview. |
| Domain model | REQUIRED | `02-domain-model.puml`: LibraryLayout, LecturePaths, LibraryIndex, ItemMeta, MigrationRunner, Journal, LibraryLock, invariants. |
| Current schema | N/A | No database. The CURRENT on-disk naming is specified in `07-consistency-migration.md` § Name mapping (left column). |
| Target schema | REQUIRED | `04-target-schema.dbml`: `item.json`, `materials.json`, journal lines, in-memory plan, API payloads. |
| Transaction flows | REQUIRED | `05-transaction-flows.puml`: download into the layout (dedup incl. legacy), tidy-up plan, apply, undo. |
| State machines | N/A | No long-lived persisted lifecycle beyond one run; the run lifecycle (planned → applying → applied/partial → undone) is drawn in `09-design-review.md` and owned by `MigrationRunner` (07 § Migration run). |
| Consistency and migration | REQUIRED | `07-consistency-migration.md`: name mapping, attribution rules, exclusive move, journal, undo, locks, compatibility, failure/cleanup owner map, Windows. |
| Acceptance trace | REQUIRED | `08-acceptance-trace.md`. |
| Visual review pack | REQUIRED | `09-design-review.md` (Korean, target overview first). |

## Confirmed decisions

- D1 week → lecture folder layout with plain visible names; `.srt` same stem as the video (owner 2026-10-30).
- D2 hidden per-lecture `.ssu/` for id/source/versions and transcript `.md`/`.json`; existing files are tidied into the new layout, never re-downloaded; preview before moving (owner 2026-10-30).
- D3 sequenced after PL-PY-PIPELINE; reuse its `LecturePipeline` (brief 2026-10-30).

## Proposed decisions

- P1 Path builder `core/library_layout.py`: `week_folder(week_position, module_name)` → `NN주차` (int ≥ 0 zero-padded to 2; non-numeric → next rule) → number parsed from `module_name` by `(\d{1,2})\s*주\s*차` or `(?i)week\s*(\d{1,2})` → (Q1) sanitized `module_name` → `기타`. `lecture_stem(title)` = `safe_name(NFC(title))`, empty → `강의`, capped at 150 UTF-8 bytes on a character boundary. Visible names: `<stem>.<video ext>`, `<stem>.srt`, `<stem> 자막.txt`, `<stem> 음성.<audio ext>`; transcript md/json → `.ssu/transcript.md|json`.
- P2 `PathRules` per platform (`platform.path_rules()`): macOS `max_name_bytes=255`; Windows adds reserved device names (`CON PRN AUX NUL COM1–9 LPT1–9`, with or without extension → suffix `_`), no trailing dot/space, total path budget 240 chars (stem shortened first, then week folder never shortened). Windows rules ship with the code and tests now; activation follows the Windows platform split.
- P3 Collisions: lecture folder candidates `<stem>`, `<stem> (2)`, …; a candidate is usable when it does not exist, or exists with `.ssu/item.json` of the same `content_id`. Folder without metadata or with another id → next suffix. Processing order for one planning pass: (`lesson_position`, `content_id`) so suffixes are stable. Same content id found anywhere in the course (LibraryIndex) wins over a fresh path, so a lecture never moves on its own.
- P4 Metadata `core/library_meta.py`: `.ssu/item.json` (schema 1, see 04) written by tmp + `os.replace` before the first visible file is committed and updated when outputs change; parse bound 64 KiB, JSON object with required keys, `stem`/`files` values must be plain names (no `/`, no `..`), else ignored (folder treated as foreign, debug log).
- P5 `LibraryIndex(course_dir)`: scans `course_dir/*/` (weeks) and `course_dir/*/*/.ssu/item.json` (lectures) plus `course_dir/*/.ssu/materials.json`; no recursion deeper, symlinks not followed. Legacy fallback: one listing of `course_dir` files with the `[id]` marker parsed by the legacy name parser (07 § Legacy name parser). `locate(content_id)` returns `LecturePaths` with target paths in the new layout and, per role (video, audio, srt, text, md, json), the existing file — new layout first, then legacy. Cost: one directory walk of depth 2 per course per planning call (≈ tens of entries).
- P6 Planner integration: `plan_download_path` gains a `LibraryTarget` (course, week folder, stem, kind, content id) instead of `lecture_title`/`content_id`; the existing signature stays as a thin wrapper for the standalone/CLI paths. `skip`: existing video in either layout → skipped (legacy path returned as `output_path`). `overwrite`: writes the new-layout path; never writes into a legacy name. `rename`: new-layout path with ` (n)` on the **file** inside the lecture folder (folder stays the lecture's).
- P7 Lecture title source (Q3): stem = LMS item title (`CourseContentItem.title`, what the list shows), fallback resolved title, fallback `강의`. Stored both in `item.json` (`lms_title`, `resolved_title`).
- P8 `LecturePipeline` (PL-PY-PIPELINE) receives `LecturePaths` from the planner result (new internal field on the batch item result, not in the API payload) instead of deriving `_ItemPaths` from the video path. Existence checks use the role resolution, so a legacy `[id].transcript.txt` or legacy `[id].txt` satisfies TXT and a legacy video is the audio source. New outputs are always written to the new-layout targets; legacy files are never moved, renamed or deleted by a download job (source-video removal applies only to a video written by this job — unchanged rule).
- P9 Transcript writer: `write_transcript_files(..., paths: TranscriptPaths | None)`; `None` keeps today's `<audio stem>.transcript.*` naming for the standalone `/api/stt/jobs`. Order: srt → `.ssu/transcript.md` → `.ssu/transcript.json` → `<stem> 자막.txt` (last).
- P10 Materials (Q2): `<week>/<title>.<ext>` with `<week>/.ssu/materials.json` (`content_id` → file name, kind `material|canvas_file`). Generic extensions (`file`, empty) use the extension of the LMS `file_name` when it has one (O2). Canvas attachments use the optional `module_name`/`week_position` sent by the UI (from the page item), else a "n주차" number in the file title (e.g. `UI디자인_1주차_202602` → `01주차`), else `기타`.
- P11 Tidy-up `core/library_migration.py`: scope = direct children of course folders directly under the download root that the app attributes (07 § Attribution). Plan (needs LMS course list + course content per matched course, read-only) → in-memory `TidyPlan` (`plan_id`, TTL 30 min, single use) → preview payload. Apply = background job: for every planned move re-check (source exists, same size + mtime_ns, destination absent) → journal `intent` → exclusive move → journal `done`; metadata written after the lecture's files moved. Undo = background job reading the journal backwards with the inverse checks.
- P12 Exclusive move: `os.link(src, dst)` (fails atomically if `dst` exists) then `os.unlink(src)`; on `EXDEV`/`EPERM`/`ENOTSUP` (no hard links, e.g. exFAT) fall back to `dst` existence check + `os.rename` under the LibraryLock (documented TOCTOU vs external apps). A crash between link and unlink leaves two names of one inode; re-plan detects `same inode` and finishes by unlinking the source name (no data can be lost).
- P13 Journal `<root>/.ssu/migrations/<run_id>.jsonl`, append + `fsync` per line; `run_id` = UTC timestamp + 6 hex; one header line, then `mkdir` / `intent` / `done` / `skip` / `meta_written` lines (04). Kept forever (small); "마지막 정리 되돌리기" uses the newest run with `done` moves not yet undone.
- P14 LibraryLock (AppContext): `tidy_running` flag + registry check. Plan/apply/undo start → 409 `library_busy` when any Python download/artifact job is not finished. Download/artifact/canvas-file job start → 409 `library_tidy_running` while apply/undo runs. Native downloads are gated by the UI (`busy` already covers them).
- P15 UI (Q4): course-pane notice when `legacy_file_count > 0` for the opened course; Settings "저장 폴더" section adds "폴더 정리…" and "마지막 정리 되돌리기"; `TidyDialog` states: 불러오는 중 → 미리보기 → 정리 중 → 결과 (되돌리기) / 오류. "나중에" hides the notice until the app restarts (in-memory).
- P16 Internal API (not an external surface; the app's own sidecar): `GET /api/library/legacy-summary?output_dir=`, `POST /api/library/tidy/plan/jobs`, `POST /api/library/tidy/apply/jobs {plan_id}`, `POST /api/library/tidy/undo/jobs {run_id}`, `GET /api/library/tidy/runs`; job status/cancel via the shared `add_job_routes`. `plan_id` comes only from the plan job result (acquisition path in 05 Flow 2/3).

## Open questions

- Q1 module without a week number → module name folder (recommended) vs `기타`.
- Q2 materials directly in the week folder (recommended) vs own folder.
- Q3 lecture name from the list title (recommended) vs media-server title.
- Q4 notice in the course + Settings (recommended) vs Settings only.
- Q5 `(2)` duplicate copies moved next to the lecture (recommended) vs left in place.
- Q6 lectures no longer listed in a matched course → `기타/<title>/` (recommended) vs left in place.
- O1 real `week_position` coverage (verify in LAYOUT-PY-PATHS with the owner's 4 courses).
- O2 `.file` materials get the `file_name` extension (verify on the real `감성지능리더십` items).

## Review findings

Self-checklist review pass (STANDARD; no independent agent review was run for this draft — disclosed limitation):

- F1 (accepted, P5/P8): switching to the new layout without reading the old one would re-download every lecture after the update (owner forbids). The legacy fallback is read-only and covers every role, including legacy `.txt`.
- F2 (accepted, P12): `os.rename` silently replaces an existing destination on macOS/Linux; "never overwrite" needs an exclusive primitive. Hard link + unlink gives atomic no-replace on APFS; fallback documented.
- F3 (accepted, P11): a preview can go stale (new download, user edits). Apply revalidates every move against size + `mtime_ns` and destination absence; stale entries become `skip` lines, never errors that abort the run.
- F4 (accepted, P14): native (Tauri) downloads are invisible to the Python job registry; the backend lock alone cannot see them. The UI busy gate covers them; collision is further prevented because a native download targets the new layout only for lectures that dedup found absent in both layouts.
- F5 (accepted, R2/P1): Korean titles are 3 bytes per character in UTF-8; ` 자막.txt` adds 11 bytes, ` (99)` 5 bytes; a 150-byte stem cap keeps every name < 255 bytes. NFC avoids "same title, different bytes" duplicates from NFD input.
- F6 (accepted, P3): suffix order must not depend on thread completion order (PL-PY-PIPELINE plans in parallel); suffixes are assigned in the single-threaded plan step by (`lesson_position`, `content_id`) and the directory is created under a per-course lock.
- F7 (accepted, P11): `find_downloaded_files` today counts a lone `.srt` as "downloaded"; `downloaded_path` now prefers video → audio → `자막.txt` → material, preserving "something of this item exists".
- F8 (accepted): the rename strategy (`(n)` copies) still exists for single downloads; inside the new layout the copy is `<stem> (2).mp4` in the same lecture folder, so `.srt` auto-load applies only to the primary copy — acceptable, same as today.
- F9 (rejected): moving `.part` files so resumes continue. A tidy-up runs only with no download active, so a `.part` is an abandoned partial; leaving it is safer and it is listed as "받다 만 파일".
- F10 (rejected): a course-level index file. Scanning ≤ a few hundred `item.json` files is cheap; a second source of truth would need its own repair path.

## Validation evidence

- 2026-10-30 `python3 <agent-skills>/skills/feature-design/scripts/validate_design.py --record ../feature.md` → result and artifact-set digest recorded in `../feature.md` § Progress and evidence.
- PlantUML and DBML renderers are not installed on this Mac (sibling records' checks); `.puml`/`.dbml` are not rendered or parsed — named evidence limitation, not a semantic pass. The Mermaid review pack is the rendered view.
