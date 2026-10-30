# LAYOUT-PY-PATHS

- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-download-folder-layout/feature.md
- Sprint manifest: ../../sprints/sprint-1.md (sha256 f18c3c72...dab0)
- Target/base: develop (branch based on origin/develop 491452e; develop later moved to 1104678 with frontend-only changes, disjoint from the owned paths)
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/layout-py-paths (feature/layout-py-paths)
- Design status: APPROVED (parent artifact-set sha256 63b53a26...c769; owner choices Q1-Q6 = recommended, parent D4)
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Acceptance subset: AC1, AC2, AC3, AC4, AC5, AC6, AC14

## Outcome

New downloads land in `<course>/<NN주차|module name|기타>/<lecture title>/` as `<title>.mp4`, `<title>.srt`,
`<title> 자막.txt`, `<title> 음성.mp3`; `.ssu/item.json` (schema 1) and `.ssu/transcript.md|json` are hidden;
materials sit directly under the week folder with `<week>/.ssu/materials.json`. Files in the old flat
layout (`<title> [content id].<ext>`) are recognised read-only and never downloaded or transcribed again.
Nothing is moved, renamed or deleted by this unit (the tidy-up is LAYOUT-PY-MIGRATION).

## What changed

- `core/library_meta.py` (new): `.ssu/item.json` / `<week>/.ssu/materials.json` atomic write + bounded parser (64 KiB / 256 KiB, schema check, plain names only, no symlink following), legacy `[id]` file-name parser.
- `core/library_layout.py` (new): `PathRules` (macOS, Windows), week folder / stem / visible names / `(n)` collisions, `LecturePaths`, `MaterialPaths`, `LibraryIndex` (new layout + legacy fallback), `CourseLayout` (names for the whole course assigned once, single-threaded, in (lesson_position, content_id) order, so a name never depends on selection or download completion order), per-course lock.
- `core/download.py`: `plan_library_download` (skip / overwrite / rename over `LecturePaths` or `MaterialPaths`), `plan_download_path` and `find_downloaded_files` kept as thin wrappers over the new modules; `download_to_plan` prepares the folder and its hidden metadata before the first byte.
- `core/lms_service.py`: course plan built before the parallel resolution; batch, single (`download_lecture`), native plan and Canvas downloads use it; internal `BatchDownloadResult.library_paths` (not in `batch_payload`); `native_download_plan` prepares the folder (the shell only writes bytes).
- `core/stt.py`, `core/stt_remote.py`: `TranscriptionRequest.paths` (internal, rejected from request bodies, hidden from OpenAPI); explicit-path writer order srt, `.ssu/transcript.md`, `.ssu/transcript.json`, `자막.txt` last.
- `core/postprocess_media.py`: `AudioExtractionRequest.output_path` only.
- `service/artifacts.py`: `_ItemPaths` replaced by `LecturePaths` (plan lookup via `ArtifactPipeline.library_paths`; the old derivation is kept as the private `_fallback_paths` for an item without a plan entry). Pipeline order, gates, events and failure handling are untouched.
- `service/api_models.py`, `service/routers/lms_pages.py`: `CanvasFileDownloadRequest.module_name` / `week_position` (optional, additive).
- `platforms/base.py`, `macos.py`, `windows.py`: `path_rules()` (Windows rules designed and tested, not activated).
- `service/routers/credentials.py`: no edit needed; `downloaded_path` comes from `find_downloaded_files`, which now reads both layouts.
- `core/errors.py`: `PathTooLong` and `LibraryConflict` live here (imported by `library_layout`); added to the owned list by the coordinator at review cycle 1.
- `.gitignore`: added `/AGENTS.md` and `/CLAUDE.md`. Owner-approved chore, not part of the feature (see Owner approvals).

## Open questions resolved

- O1 (how often `week_position` is filled): the parser reads only `module.week_position` (`core/lms.py` `extract_course_content_items`). The live-shaped fixture (`tests/test_lms.py`, module `title: "1주차: 강의 소개"`, `week_position: 1`) and the LMS inventory (`.dev/10-lms-content-inventory.md`, top-level module fields) put it at module level; the `tests/test_service.py` fixture puts a `week_position` inside each item's `content_data`, which the parser ignores. Real coverage on the owner's 4 courses could not be measured here (no real LMS access in this unit), so the rule is built not to depend on it: `week_position` -> number in `module_name` (`5주차`, `5 주차`, `Week 5`) -> sanitized `module_name` -> `기타`. To confirm on real data: open each course once and compare the folder names produced by a dry-run plan.
- O2 (`.file` extension): the extension comes from `item_content_data.content_type`, which is literally `file` for generic attachments. `material_extension()` takes the extension of the LMS `file_name` when the type is generic (`file`, `bin`, empty, ...); with no usable `file_name` the generic `.file` is kept. A module item typed `file` has kind `unknown` in `normalize_content_kind` (not downloadable in batch), so it is reached through the single download and native plan; the course plan places `unknown` items too so those get the week folder. Not verified on the real `감성지능리더십` items.

## Owner approvals

- `.gitignore` chore: on 2026-10-30 the owner chose "연결 (Recommended)" in answer to "숭실대 저장소의 CLAUDE.md와 AGENTS.md를 agent-skills의 이 프로젝트 전용 규칙 파일로 연결할까요?". The two lines ignore the local symlinks.

## Accepted deviations (design 07 / sprint-1)

- Re-planned `(n)` order for media-resolved items: the course plan numbers same-title lectures from the module list, before any item is resolved. When the media server reports a different content id for an item (`CourseLayout.bind_identity`), the item switches to the lecture folder of that id if one exists, else keeps its planned folder; the number it got can then differ from a plan made with the reported ids. Stable across runs once the folder exists (metadata decides).
- Lecture folder creation happens at prepare time (first write: download, audio, transcript, native plan), under the per-course lock, not during planning. Planning stays read-only (a dry run creates nothing), so two concurrent jobs can still race for a free name; `prepare()` then raises `LibraryConflict` instead of writing into the other job's folder.

## Deviations and decisions to confirm

- `artifacts.py` also gained `ArtifactPipeline.library_paths` / `lecture_paths()` (the plan lookup) and one `paths.prepare()` call in `_ensure_audio` (creates the lecture folder and `.ssu/item.json` before the first write into a lecture whose video is a legacy file). No change to order, gates, events or failure handling.
- With explicit lecture paths, `write_transcript_files` treats only `<stem> 자막.txt` as the "finished set" marker, so leftovers (`.srt`/`.md`/`.json` of an interrupted run) are rewritten instead of failing with `transcript_exists` (required by AC3). The standalone naming keeps its old check.
- `materials.json` gets the entry before the bytes arrive (so a crash keeps the name reserved); the index counts an entry only when its file exists.
- `native_download_plan` has a side effect now: it creates the lecture folder and `item.json` for a download it plans (native downloads would otherwise leave an unrecorded folder that the next plan treats as foreign).
- `plan_download_path` (standalone/CLI wrapper) places an item without week information in `기타/`.
- `AudioExtractionRequest.output_path` is a plain optional path like the existing `output_dir`; `/api/media/audio/jobs` accepts it from a request body just as it accepts `output_dir` today.
- Review cycle 1 repairs: legacy materials with a media or text extension (`notes [m1].txt`, `[m2].mp3`, Canvas `[canvas-5].mp4`) are found by their id for a planned material; `AudioExtractionRequest.output_path` is internal-only (rejected in a body, hidden from OpenAPI); `ArtifactPipeline.lecture_paths` trusts the written file over the dry-run plan (a file outside the planned folder, other than the planned old-layout video, gets the old derivation) and only the video that is `item.output_path` is ever removed; `MaterialPaths.prepare` refuses a name held by another id (case-folded) or an unrecorded file, and `skip` accepts an existing material only when materials.json maps it to its id; a failing module list fails a single download through the usual error mapping (warning logged) instead of filing it under `기타`; the Windows budget counts `(n)` twice and `.part`; `item_kind_for_extension()` is the single classifier.
- Out of scope, noted: `docs/stt.md` still describes the old `.transcript.*` names for the standalone endpoints (still correct for them).

## Progress and evidence

- 2026-10-30: worktree created from origin/develop 491452e; records copied from the parent checkout.
- 2026-10-30: implemented as above. Existing tests changed only where the design changes a path: `tests/test_download.py`, `tests/test_download_planner.py` (standalone planner now returns the week -> lecture layout), `tests/test_service.py` (names now come from the list title in the week folder), `tests/test_lms_pages.py` (Canvas files land in the week folder). New: `tests/test_library_layout.py`, `tests/test_library_index.py`; additions in `tests/test_stt.py`, `tests/test_api.py`, `tests/test_lms_pages.py`.
- Verification: `UV_LOCKED=1 uv run pytest -q` and `UV_LOCKED=1 uv run lint-imports` (see the PR for counts). Tests run on temp trees only and wait on job completion, never on sleeps.

## Test value (review cycle 1 cut)

The new tests were reduced from 172 to 70 (780 -> 850 total) by merging tables into literal-dict tests and dropping rig duplicates of unit scenarios (foreign folder, week moved, module-name week, same-title materials, generic `.file` without file_name, planning-writes-nothing). `test_value.py safety --module core/library_layout.py core/library_meta.py --tests test_library_layout.py test_library_index.py test_download_planner.py test_stt.py --base <before the cut>` (758 mutants, head = working tree): 7 mutants killed before and not after (NUL / LPT reserved names, week 0, extension `and`, `webm`, empty content id); each was restored as a row of an existing table test and no test was added back. The tool was not re-run after the rows were restored (about 13 minutes); `core/download.py` and `core/lms_service.py` were not part of that run.

## Touched paths

All inside the sprint-owned list: `src/ssu_lecture_downloader/core/{library_layout,library_meta,download,lms_service,stt,stt_remote,postprocess_media}.py`, `service/{artifacts,api_models}.py`, `service/routers/lms_pages.py`, `platforms/{base,macos,windows}.py`, tests listed above. `tests/test_lms_pages.py` is not in the sprint-owned test list; it changed because Canvas files now land in the week folder (path expectations, two new Canvas tests). `tests/test_download.py` was listed and changed for the planner expectation. Plus the `.gitignore` chore and the copied `.dev/features/20261030-download-folder-layout/` records.
