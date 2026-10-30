# LAYOUT-PY-MIGRATION

- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-download-folder-layout/feature.md
- Sprint manifest: ../../sprints/sprint-1.md (sha256 f18c3c72...dab0)
- Target/base: develop (branch based on origin/develop d697c33, which contains LAYOUT-PY-PATHS #16)
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/layout-py-migration (feature/layout-py-migration)
- Design status: APPROVED (parent artifact-set sha256 63b53a26...c769; owner choices Q1-Q6 = recommended, parent D4)
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Acceptance subset: AC7, AC8, AC9, AC10, AC11 (backend)

## Outcome

The tidy-up of the old flat download layout exists as an internal API: preview (read only), apply (journaled,
exclusive moves, revalidated per file), undo (from the journal), run list, a scan-only legacy summary and
`legacy_file_count` in course detail. A tidy-up and a download never run together. Nothing runs without an
explicit request; nothing is deleted or overwritten; the only thing undo removes besides empty folders is app
metadata the run itself wrote (see decisions to confirm).

## API contract as implemented (design/09 § 내부 API 계약, additive only)

| Request | Result |
|---|---|
| `GET /api/library/legacy-summary?output_dir=` | `{ok, root, legacy_file_count, course_count, courses:[{folder, legacy_file_count}]}`; scan only, no login; courses without old files are left out |
| `GET /api/courses/{id}` | adds `legacy_file_count` (old-layout files in that course folder, 0 when none) |
| `POST /api/library/tidy/plan/jobs {output_dir?}` | job; result `{plan_id, expires_at, move_count, skip_count, moves:[{course, week_folder, lecture_stem, from, to, role}], skips:[{path, reason}], open_files_note: true}`; `from`/`to`/`path` are relative to the download folder; `lecture_stem` is null for materials; needs credentials (LMS read only); changes nothing on disk |
| `POST /api/library/tidy/apply/jobs {plan_id}` | job; progress `{stage:"tidy_moving", done, total}` (first, last, every 10 files or 0.5 s); result `{run_id, moved, skipped, skips:[{path, reason}], undo_available}` (`skips` = preview skips + skips found while applying; `run_id` is null for an empty plan) |
| `POST /api/library/tidy/undo/jobs {run_id, output_dir?}` | job; same progress stage; result `{run_id, restored, skipped, skips}` |
| `GET /api/library/tidy/runs?output_dir=` | `{ok, runs:[{run_id, at, moved, undo_available}]}`, newest first |
| job status / cancel | `GET /api/library/tidy/{plan\|apply\|undo}/jobs/{job_id}` and `POST .../cancel` (shared `add_job_routes`) |
| errors | 409 `library_busy`, 409 `library_tidy_running`, 409 `tidy_plan_expired`, 404 `tidy_run_not_found`, 400 `invalid_run_id`; messages in `service/api_errors.py` |

Skip reasons: `destination_exists`, `unknown_file`, `partial_download`, `course_not_matched`, `duplicate_transcript`,
`source_changed`, `source_missing`, `not_a_regular_file`; Windows: `in_use` (defined, never raised on macOS),
`path_too_long` (raised when the layout reports an item that cannot fit the path budget; inactive with the macOS
rules); added: `move_failed` (see decisions to confirm).

## What changed

- `core/library_migration.py` (new): `MigrationRunner` (`plan`, `apply`, `undo`, `runs`), `TidyPlan` / `TidyPlanStore` (30 minutes, single use), `exclusive_move`, `Journal`, `legacy_summary`, `count_legacy_files`. Planning reuses `CourseLayout` through `lms_service._course_layout`, so the destination of a tidied file is exactly where a download of that lecture would go (same week folder, stem, `(n)` numbering, existing-lecture reuse).
- `service/routers/library.py` (new): the endpoints above. The LMS is read through the service's `discover_courses` / `get_course_detail` (cached module lists).
- `service/app_context.py`: `ActivityRegistry` (a `JobRegistry` that can say whether a job has not finished), `LibraryLock`, `tidy_plan_jobs` / `tidy_jobs` registries, the plan store, `start_download_job`.
- `service/routers/download.py`, `lms_pages.py`: job starts go through `start_download_job` (atomic with the tidy check); synchronous writers (`/api/download`, `/api/download/batch` unless dry run, `native-plan`, `canvas-file`) run inside `library_lock.downloading()`.
- `service/routers/credentials.py`: `legacy_file_count`. `service/api.py`: router registration. `service/api_errors.py`: the new codes, added next to the two LAYOUT-PY-PATHS codes (kept).

## Behaviour worth knowing

- Attribution: only regular files directly inside a course folder that the legacy parser maps to a content id. Hidden names, `.tmp` leftovers and `.DS_Store` are ignored silently; `.part`, unparseable names, sub-folders that are not week folders of the new layout, and symlinks are listed with a reason; a folder whose name matches no current LMS course is one `course_not_matched` line for the whole folder.
- Mapping (design 07 § Name mapping): video, srt, audio (`<stem> 음성.<ext>`), `.transcript.txt` and the oldest `.txt` (`<stem> 자막.txt`; skipped as `duplicate_transcript` when the lecture already has a transcript text), `.transcript.md|json` into `.ssu/`; `(n)` copies of video, audio and srt as `<stem> (n).<ext>` (Q5); materials to `<week>/<title>.<ext>` (extension from the LMS `file_name` when the old one is generic); Canvas attachments by the week in their title, else `기타`; ids that vanished from the LMS list go to `기타/<title>/` (Q6).
- Apply order per lecture: folders (journaled `mkdir`), metadata (`item.json` / `materials.json`, journaled `meta_written`), then per file `intent` (fsync) -> exclusive move -> `done`. Revalidation before each file: regular file, size, `mtime_ns`, inode, destination free (or the same inode, i.e. the unfinished half of an earlier move). A lecture folder that appeared after the preview and is not ours is never written into.
- Crash recovery: a link without its unlink leaves two names of one inode and is finished by the next preview/apply (the move is planned again and only the old name is removed); a completed move without its `done` line is closed by the next apply/undo (`intent` whose source is gone and whose destination has the journaled inode and size).
- Undo: reverse order; a file is restored only when it still has the journaled size, `mtime_ns`, inode and its old path is free; then metadata the run wrote is taken back only while byte-identical to what the run wrote (materials.json: previous bytes restored; item.json: removed, but kept while the lecture folder holds anything else); then `rmdir` of folders the run created, never recursive. Journal paths must be relative, free of `..`, and cross no symlink.
- Accepted, documented gap: on volumes without hard links (`EPERM`, `EXDEV`, `ENOTSUP`, `EMLINK`...) the move is an existence check followed by `rename`; only another app creating exactly that name in that instant could be overwritten. The tidy lock keeps this app's own downloads out meanwhile. A cross-device rename is a skip (`move_failed`), never a copy.

## Deviations and decisions to confirm

- Added skip reason `move_failed` (an unexpected OS error on one file, or a cross-device rename): that file is skipped instead of the run failing half way. The UI copy of design/09 has no text for it ("옮기지 못했어요" suggested); LAYOUT-UI-TIDY should show unknown reasons generically.
- Metadata is written before the first move of a lecture, not after its files (design 05 draws it after): a crash between two files then leaves a folder the next plan recognises as the same lecture instead of a foreign one that would get a `(2)` sibling. A group whose moves are all skipped creates nothing.
- Undo removes app metadata the run wrote (byte-identical check). The unit brief says nothing is deleted except empty folders; design 07/AC9 say undo removes "metadata the run created". These are app-owned files, never user files.
- `POST .../undo/jobs` accepts an optional `output_dir`; without it the folder a run was created or listed for in this session is used, then the default folder. The frozen contract is only `{run_id}`; a UI that lists runs first (Settings) works unchanged.
- The apply result's `skips` includes the preview's skips, so `moved + skipped` is what the student saw in the preview.
- Transcript copies (`(n)` of `.txt`, `.md`, `.json`) stay in place as `duplicate_transcript`; Q5 moves copies of video, audio and srt.
- `(n)` copies of a material go to `<title> (n).<ext>` in the week folder (skipped as `destination_exists` when that name is taken).
- Admission also covers the synchronous writers and Canvas file downloads (a counter, not a registry), and the preview only checks that nothing runs (it does not block downloads, it is read only). Audio/STT/media jobs are not download jobs and are not gated.
- `lms_service._course_layout` (private name) is imported by `core/library_migration.py` so download and tidy cannot disagree on names; making it public needs an edit in LAYOUT-PY-PATHS' files.
- `ActivityRegistry` is a subclass in `app_context.py` because `core/jobs.py` is not an owned path.
- `tidy_moving` events are not coalesced (that list lives in `core/jobs.py`); throttling keeps them to about total/10 plus time-based ones, under the existing 400-event cap. Undo reports the same stage.
- `run_id` = `yyyymmddThhmmssZ-<3 hex ms><3 hex random>` so "newest first" is a plain sort.
- No entry in `/api/health` capabilities (`health.py` is not owned); the UI can probe `GET /api/library/tidy/runs` (404 on an old backend).
- The legacy summary counts attributable files of every course folder (no LMS), so it can exceed what a preview moves when some folders are `course_not_matched`.

## Review cycle 1 repairs

- Undo trusts the journal only by shape: a `done` line must be `<course>/<file>` -> `<course>/<week>/[<lecture>/]<file>` (or the lecture's `.ssu/transcript.md|json`), a `mkdir` line a week/lecture/`.ssu` folder, a `meta_written` line exactly `<course>/<week>/<lecture>/.ssu/item.json` or `<course>/<week>/.ssu/materials.json`; `previous_b64` is bounded to the metadata size cap. Anything else is skipped (and never unlinked or overwritten), also in crash recovery. The tampered-journal test now names in-root user files (pdf, docx) with their real size, mtime, inode and hash.
- `TidyRunNotFound` moved to `core/errors.py` (path added by coordinator decision, next to `PathTooLong` / `LibraryConflict`).
- `exclusive_move`: a destination that is the same entry as the source (case-only spelling on a case-insensitive volume, one name only) is `exists`, never an unlink; when the link worked but the old name cannot be removed, the new name is taken back and the error re-raised (the file is skipped as `move_failed`).
- Undo keeps a week's `materials.json` while a material this run moved into that week is still in its new place; undo cleans up (metadata, folders) unless the loop actually stopped on cancel.
- `legacy_file_count` / the summary no longer count files a preview always leaves for disk-only reasons (`duplicate_transcript`: a copy `(n)` of a transcript file, the oldest `.txt` next to a `.transcript.txt` or next to a transcript text already in the lecture folder; `.part`, unknown names and non-regular files were never counted), so the course notice does not return after a tidy-up.
- Duplicate audio copies are `<stem> 음성 (n).<ext>`.
- Contract amendment: `lecture_stem` in a move is null for materials.
- Not done (P4, noted): the metadata intent is not journaled before the metadata is written (a crash in between leaves app metadata that a later undo cannot take back; the next preview recognises it as the lecture); `runs()` is a plain read of the journals and does not run crash recovery, so a completed move whose `done` line was lost is counted only after the next apply/undo.
- Follow-ups: make `lms_service._course_layout` public; move `has_active` from `ActivityRegistry` onto `JobRegistry`.

## Review cycle 2 repairs

- Verification: `UV_LOCKED=1 uv run pytest -q`: 879 passed, 2 deprecation warnings (45.27 s); fake HTTP server emitted a BrokenPipeError during teardown, exit 0. `UV_LOCKED=1 uv run lint-imports`: 4 kept, 0 broken. Both used `UV_CACHE_DIR=/private/tmp/layout-py-migration-uv-cache` because the default cache was sandbox-inaccessible.

- Compare course names in journal move shapes after NFC normalization, so NFD course folders support both undo and recovery. Audited mkdir/meta shapes and `_meta_kind`: these check depth and fixed metadata names, with no raw course-name comparison.
- `TidyRunNotFound.error_code` supplies the router and failed background-job code.
- Disk-only legacy counts exclude existing new-layout lecture destinations, preserve material transcript-like copies, and ignore symlinked course folders.
- Known residual: a planted `done` line with exact stat values can still rename a week- or lecture-depth file, but can never delete or overwrite it. Binding `done` lines to this run's own `intent` and `mkdir` entries is a follow-up.

## Progress and evidence

- 2026-10-30: implemented from origin/develop d697c33. `UV_LOCKED=1 uv run pytest -q`: 855 -> 876 passed. `UV_LOCKED=1 uv run lint-imports`: 4 kept, 0 broken.
- Tests are on temp trees only; jobs are joined by thread, the progress throttle and plan TTL use injected clocks. Nothing sleeps or retries.
- Mutation spot checks (a mutant must fail a test): dropping the mtime, the size or the undo-precondition check; rename without the existence check; metadata removal without the byte check; item.json removal while the folder is in use; writing into a folder that appeared after the preview; finishing a half move; following a symlink in a journal path. Two mutants survived the first draft of the tests (metadata byte check, journal symlink guard) and each got its assertion.

## Test value

New tests: 16 functions, 21 cases (`tests/test_library_migration.py` 12 functions / 13 cases, `tests/test_api_library.py` 4 functions / 8 cases). One behaviour per test: preview + no change (AC7), legacy summary, apply + journal order + metadata + idempotent re-preview + new run (AC8), changed-since-preview skips, crash recovery (2 crash points are real equivalence classes), no-hard-link fallback, progress throttle, cancel, undo restore + second undo, undo refusals, tampered journal, end to end artifact job after the tidy-up (AC10); API contract, plan TTL, busy and tidy-running exclusion (AC11). The service layer repeats no unit scenario.

## Touched paths

Owned (sprint-1.md): `src/ssu_lecture_downloader/core/library_migration.py` (new), `service/routers/library.py` (new), `service/api.py`, `service/app_context.py`, `service/routers/download.py`, `service/routers/lms_pages.py`, `service/routers/credentials.py`, `service/api_errors.py`, `tests/test_library_migration.py` (new), `tests/test_api_library.py` (new). `service/routers/__init__.py` needed no edit. Outside the sprint-owned list, by coordinator decision at review cycle 1: `src/ssu_lecture_downloader/core/errors.py` (`TidyRunNotFound`). Plus this record.

## CI follow-up (c4)

- NFD course folders are offered only when the layout course path identifies the same directory; otherwise every listed item is skipped as `course_not_matched`, and disk-only legacy counts exclude the folder. APFS behavior is preserved.
- The NFD regression test now branches on volume capability instead of skipping; the macOS bundle job also runs it, while Ubuntu covers normalization-sensitive volumes.
- Verification: `UV_LOCKED=1 uv run pytest -q`: 879 passed, 2 deprecation warnings (44.40 s); fake HTTP server emitted a BrokenPipeError during teardown, exit 0. `UV_LOCKED=1 uv run lint-imports`: 4 kept, 0 broken. Both used `UV_CACHE_DIR=/private/tmp/layout-py-migration-uv-cache`.
