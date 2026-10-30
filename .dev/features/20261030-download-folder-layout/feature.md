# 20261030-download-folder-layout: Week → lecture folder layout, hidden metadata, safe tidy-up of existing downloads

- Record schema: 4
- Status: SPRINT_PLANNED
- Planning mode: STANDARD
- Execution mode: AUTONOMOUS_SPRINT
- Record kind: SPRINT_CONTROL
- Owner: cafitac (main orchestration task)
- Repository: ~/Project/cafitac/tools/ssu-lecture-downloader (github.com/cafitac/ssu-lecture-downloader, public)
- Created: 2026-10-30
- Updated: 2026-10-30
- Current slice: L1 (new layout + tidy-up of existing files) — sprint-1 planned; prerequisites PL-PY-PIPELINE (#10) and PL-UI-STATES (#11) MERGED
- Current PR unit: CONTROL
- Delivery strategy: STANDALONE
- Current delivery bundle: N/A
- Bundle PR topology: N/A
- Standalone slice reason: N/A (one slice; three sequential PR units, each independently revertible)
- Architecture authoritative lifecycles: on-disk library layout of the download folder (path builder + hidden per-lecture `.ssu/` metadata = dedup authority); one migration run (plan → apply → optional undo) journaled on disk
- Architecture durable effect families: user files in the download folder (new downloads written into the new layout; existing files moved by the tidy-up, never deleted or overwritten); app metadata files (`.ssu/item.json`, `<week>/.ssu/materials.json`, `.ssu/transcript.md|json`); migration journal (`<root>/.ssu/migrations/<run_id>.jsonl`)
- Architecture external authorities/gateways: Soongsil LMS (read only, unchanged: module list gives `week_position` / `module_name` / item title for folder names); the local filesystem (APFS today, Windows later); other apps holding files open (Finder, players) — not controlled
- Architecture public idempotency namespaces: none public; internal `plan_id` (in-memory plan handle, TTL) and `run_id` (journal identity) for apply/undo idempotency
- Architecture runtime/deploy artifacts: macOS app/sidecar (packaging unchanged); Tauri native download writes to the Python-planned path (unchanged contract, parent dirs already created by `native_download.rs`)
- Architecture failure/recovery owners: LibraryLayout (path decisions, collision suffixes); LibraryIndex (dedup scan; corrupt metadata → ignored entry, never a crash); MigrationRunner (per-move revalidation, write-ahead journal, crash-resume by re-plan, undo); LibraryLock (mutual exclusion migration ↔ Python download jobs); UI busy gate (native downloads invisible to Python)
- Architecture cleanup owners: MigrationRunner undo (moves journaled files back, removes only empty directories it created and metadata it wrote); none for user files (never deleted); expired in-memory plans dropped by TTL
- Architecture repositories/ledgers: migration journal JSONL (append-only, per run, in the download folder); `.ssu/item.json` per lecture (dedup record)
- Architecture security/resource parsers: `.ssu/item.json` / `materials.json` / journal JSONL read from a user-writable folder (untrusted: size cap, schema check, paths must stay under the download root, no symlink following); legacy filename parser (`<title> [<id>]( (n))?<suffix>`)
- Independent boundary categories: failure/recovery-owner, rollbackable-effect-family
- Architecture split/replan decision: STANDALONE_REEVALUATED (layout/dedup and migration have separate rollback: layout = revert PR, legacy files stay readable by the fallback; migration = per-run undo journal. They stay sequential STANDALONE units because the migration reuses the layout builder and must not ship before the dedup reads both layouts)
- Failure/recovery owner map: see design/07 § Failure and cleanup owner map
- Cleanup inventory map: see design/07 § Failure and cleanup owner map
- Parent control record: N/A
- Sprint ID: download-folder-layout-sprint-1
- Sprint manifest: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-download-folder-layout/sprints/sprint-1.md
- Sprint manifest SHA-256: f18c3c7241c41d3aaf8c39e9d46fafd75c4d71b8f02925a919a55ee3bf75dab0 (validate_review_topology.py ok, 2026-10-30; CONFIRMED by owner 2026-10-30 "이 순서로 진행 (Recommended)")
- Authority grant: user 2026-10-29 standing grant for this repository (autonomous within an approved scope; Claude merges reviewed + CI-green PRs to develop). Running the tidy-up on the owner's real download folder stays owner-only (AC13).
- Approved delivery scope: sprint-1 manifest (sha256 f18c3c72…dab0): LAYOUT-PY-PATHS → LAYOUT-PY-MIGRATION ∥ LAYOUT-UI-TIDY (merged after MIGRATION) → owner checkpoint CP-TIDY-DOGFOOD (AC13)
- Delivery finalization: AUTO_AFTER_GATES
- Delivery finalization scope: PRs to cafitac/ssu-lecture-downloader develop only
- Delivery finalization evidence: standing grant (sibling records 20261029-windows-platform-split, 20261029-grades-view, 20261030-per-lecture-pipeline)
- Eligible unit IDs: LAYOUT-PY-PATHS, LAYOUT-PY-MIGRATION, LAYOUT-UI-TIDY
- Review topology: CODE_POLISH_QUORUM
- Review quorum: 2
- Review scheduling: PARALLEL
- Review cycle budget: 3
- Unit review topology map: all INHERIT
- Unit review cycle budget map: all INHERIT
- Single-reviewer exception scope: N/A
- Single-reviewer exception rationale: N/A
- Single-reviewer exception approval: N/A
- Human tracker / workspace / team: GitHub PRs on cafitac/ssu-lecture-downloader
- Tracker project / assignee / language: cafitac/ssu-lecture-downloader / cafitac / Korean user-facing, English code
- Tracker status mapping / synchronization authority: PR state is the projection; main task only
- Sprint-eligible tracker scope: this record's units only
- Maximum active execution lanes: 2 (backend lane + frontend lane) + review
- Explicit role/model routes: every role on Claude (personal scope)
- Account-window warning / critical thresholds: GitHub Actions free minutes (stop and ask before exceeding)
- Usage measurement checkpoints / ordered response: per unit terminal; shorten handoffs first, then lanes
- Orchestrator handoff record / rotation condition: ~/Project/cafitac/.agent-squad/ssu-lecture-downloader/
- Target/base: develop
- Base ref: origin/develop
- Base decision: repository default integration branch (develop); planning base contains PL-PY-PIPELINE (#10, 4ab55da) and PL-UI-STATES (#11, d0591ca)
- Initialization base SHA: pending per unit (planning base origin/develop@d0591cab533f4358c8c354855c0d8ec537f3ccc3, fetched read-only 2026-10-30)
- Worktree/branch: pending
- Verification topology: LOCAL_LIGHT + CI_EXACT_SHA (GitHub Actions macOS + Windows from sibling S0-CI)
- Execution plane: LOCAL_EXECUTION_EXCEPTION (MacBook; personal desktop project)
- Remote execution profile / host: N/A
- Remote worktree / branch / base / head / patch: N/A
- Remote policy release / manifest SHA-256: N/A
- Native agent health receipt: N/A
- Remote handoff / sync-back receipt: N/A
- Local exception fingerprint / permitted stage / remote-resume condition: personal desktop project; all stages local + GitHub Actions; the Mac Studio is not touched
- Remote test runner profile: N/A
- Remote test fallback: USER_OPT_IN
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Worktree cleanup status: NOT_CREATED
- Worktree cleanup evidence:
- Canonical record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-download-folder-layout/feature.md
- Record provenance: planning copy on the develop checkout; no worktree handoff yet
- External links: https://github.com/cafitac/ssu-lecture-downloader
- Design profile: STANDARD
- Design status: APPROVED
- Design root: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-download-folder-layout/design
- Design approval: user 2026-10-30 "추천안 그대로 승인 (Recommended)" (Design Gate, via coordinator) for artifact-set sha256:63b53a2600fab58b0f6efd7a66cd51b5ad517ec522a417d69120b2746685c769; Q1–Q6 = recommended options (re-validated unchanged)
- Design evidence identity: artifact-set sha256:63b53a2600fab58b0f6efd7a66cd51b5ad517ec522a417d69120b2746685c769 (APPROVED 2026-10-30; re-validated unchanged)

## Test environment evidence (optional)

- UI design profile: existing app screens (SettingsDialog "저장 폴더" section, Dialog component, LecturePane header)
- UI design system: frontend/src/styles/tokens.css (no DESIGN.md in the repo)
- UI IA document: design/09-design-review.md § 화면과 문구
- UI reference screens: SettingsDialog.tsx (저장 폴더 · 변경 · 폴더 열기), Dialog.tsx, WatchAllDialog.tsx (confirm dialog pattern), Toasts.tsx
- UI design review: pending (LAYOUT-UI-TIDY render + screenshot check, light/dark)
- UI design waiver: N/A

## Workflow cursor

- Workflow stage: AGENT_SQUAD
- Last completed stage: FEATURE_PLAN (final sprint-1 manifest, sha256 f18c3c72…dab0)
- Next eligible action: AGENT_SQUAD dispatch LAYOUT-PY-PATHS (READY; base origin/develop@d0591cab; acceptance AC1–AC6, AC14)
- Next action class: LOCAL_CONTINUE
- Gate state: NONE
- Gate reason:
- Active run:
- Evidence identity: artifact-set sha256:63b53a2600fab58b0f6efd7a66cd51b5ad517ec522a417d69120b2746685c769; sprint manifest sha256:f18c3c7241c41d3aaf8c39e9d46fafd75c4d71b8f02925a919a55ee3bf75dab0

## Outcome

Downloads are stored the way a student browses them: one folder per course, inside it one folder
per week (`05주차`), inside it one folder per lecture holding exactly the files the student wants to
open — the video, subtitles with the same name as the video (players load them automatically), the
transcript text and the audio — with plain names and no internal ids. Everything the app needs to
recognise a lecture later (content id, source, versions, the transcript `.md`/`.json`) lives in a
hidden `.ssu/` folder inside the lecture folder.

```
<과목>/
  05주차/
    선대_5_2/
      선대_5_2.mp4          ← 영상
      선대_5_2.srt          ← 자막
      선대_5_2 자막.txt     ← 자막 글
      선대_5_2 음성.mp3     ← 음성
      .ssu/                 ← 숨김: item.json, transcript.md, transcript.json
```

Files downloaded before this change are never downloaded again: the app recognises them in the
old flat layout, and a one-time "정리하기" moves them into the new layout after the student has
seen the full list of moves. Nothing is deleted or overwritten; every move is journaled and can be
undone ("되돌리기").

## Requirements

- R1 [MUST]: Path builder. `<download root>/<safe course>/<week folder>/<lecture stem>/`; week folder = `NN주차` (zero-padded, from `week_position`), else the number parsed from `module_name` (`5주차`, `5 주차`, `Week 5`), else (Q1) the sanitized `module_name`, else `기타`. Lecture stem = sanitized LMS item title (Q3). Visible names: `<stem>.<video ext>`, `<stem>.srt`, `<stem> 자막.txt`, `<stem> 음성.<audio ext>`. No content id in any visible name.
- R2 [MUST]: Sanitization and limits: existing unsafe-character rules, Unicode NFC, trimmed dots/spaces, empty → `강의`, each name ≤ 255 UTF-8 bytes with the longest suffix (stem capped at 150 bytes, cut on a character boundary); Windows rules (reserved device names, 240-char path budget, trailing dot/space) designed and unit-tested now, activated with the Windows platform.
- R3 [MUST]: Collisions. Two different lectures with the same stem in one week get `<stem>`, `<stem> (2)`, … (deterministic: lesson order, then content id). A folder without app metadata is foreign and never reused. A lecture already on disk is reused where it is, even if the LMS later moved it to another week.
- R4 [MUST]: Hidden metadata `.ssu/item.json` per lecture (schema version, content id, module item id, course id, kind, LMS title, resolved title, stem, week folder, created by download|migration, app version, layout version, timestamps), written atomically; transcript `.md`/`.json` in `.ssu/`; `<stem> 자막.txt` stays the last file written (complete-set marker).
- R5 [MUST]: Dedup without filename markers. Already-present outputs are found through `.ssu/item.json` (new layout) and, until tidied, through the `[content_id]` marker in the course folder (legacy fallback, read-only). No lecture or output that exists in either layout is downloaded or transcribed again with the default `skip` strategy. `downloaded_path` in course detail and page attachments stays correct for both layouts.
- R6 [MUST]: MP4-optional flows. MP3-only / TXT-only / MP3+TXT produce the lecture folder without a video (source video removal rule of PL-PY-PIPELINE unchanged); a legacy video is used as a source in place and is never moved or deleted by a download job.
- R7 [MUST]: Lecture materials (PDF, `.file`, other non-video module items) go to `<week>/<title>.<ext>` (Q2) with `<week>/.ssu/materials.json` for dedup; Canvas page attachments go to the page's week (request gains optional `module_name`/`week_position`), else `기타/`.
- R8 [MUST]: Safe tidy-up (migration) of existing files: only files directly inside course folders of the app's download folder that the app can attribute (`[id]` marker + known suffix); unknown files, `.part` files, folders and courses the app cannot match stay untouched and are listed; never delete, never overwrite (exclusive move), skip on destination collision or when the source changed since the preview; write-ahead journal per run; re-running is idempotent; legacy `<stem>.txt` and `.transcript.*` both handled.
- R9 [MUST]: Preview before moving: the UI shows the complete list of moves and skips with reasons, and moves nothing until the student presses "정리하기"; after a run it offers "되돌리기"; Settings keeps "마지막 정리 되돌리기" for the latest run. Files open in other apps are moved (macOS allows it) and the result says so.
- R10 [MUST]: Mutual exclusion: tidy-up refused while any download/artifact job runs (Python jobs checked in the backend, native downloads by the UI busy gate); new download jobs refused while a tidy-up runs.
- R11 [MUST]: Sequenced after PL-PY-PIPELINE: reuse its `LecturePipeline` (path resolution replaces its `_ItemPaths`), no second pipeline; standalone `/api/stt/jobs` and `/api/media/audio/jobs` keep today's naming (no lecture context).
- R12 [OUT_OF_SCOPE]: renaming folders when the LMS renames a lecture later; moving files of other terms' courses that the LMS no longer lists; Windows hidden-attribute and long-path activation (designed only, ships with the Windows platform work); a configurable layout.

## Acceptance contract

- [ ] AC1 (R1, R2): Path builder table tests: week_position 5 → `05주차`, 12 → `12주차`, "7" → `07주차`; None + `5주차: 행렬` → `05주차`; None + `Week 3` → `03주차`; None + `오리엔테이션` → per Q1; None + "" → `기타`; titles with `/ : * ? " < > |`, control chars, NFD input, leading/trailing dots/spaces, 300-byte Korean title (cut on a character boundary, every file name ≤ 255 bytes incl. ` 자막.txt` and ` (99)`); Windows rules: `CON`, `aux.txt`, trailing dot, 240-char budget; visible names never contain the content id.
  - Evidence: pytest `test_library_layout.py` (parametrized table).
- [ ] AC2 (R3): Two lectures with the same title in one week → `선대_5_2/`, `선대_5_2 (2)/`, stable across runs and orderings; a foreign folder `선대_5_2/` without `.ssu/` → new lecture gets `(2)`; an existing lecture in `04주차` stays there when the LMS now says week 5.
  - Evidence: pytest on a temp tree.
- [ ] AC3 (R4): After an artifact job the lecture folder has the visible files of the requested outputs, `.ssu/item.json` with all schema fields, `.ssu/transcript.md|json`; `자막.txt` written last (inject a failure before it → next run re-transcribes); corrupt, oversized (> 64 KiB) or path-escaping `item.json` is ignored without crashing and the lecture is treated as foreign.
  - Evidence: pytest (fake media/STT).
- [ ] AC4 (R5): Second artifact job (skip) on the same lectures emits no `downloading` / `extracting_audio` / `transcribing` for lectures present in the new layout AND for lectures present only in the legacy layout (`[id].mp4`, `[id].transcript.txt`, legacy `[id].txt`); `native-plan` returns skipped for both; course detail `downloaded_path` and page attachment `downloaded_path` set for both layouts.
  - Evidence: pytest (service + API).
- [ ] AC5 (R6): MP3-only, TXT-only, MP3+TXT on a fresh lecture → lecture folder without video; TXT-only with a legacy `[id].mp4` → `자막.txt`/`.srt` in the new lecture folder, the legacy video untouched (same inode, same path).
  - Evidence: pytest.
- [ ] AC6 (R7): Material item (pdf, `.file`) → `05주차/<title>.pdf` + `05주차/.ssu/materials.json`; second download skipped; Canvas attachment with week → that week, without → `기타/`; same-title materials → `(2)`.
  - Evidence: pytest.
- [ ] AC7 (R8 plan): On a temp tree copied from the owner's real names (4 courses, 43 files incl. `(2).mp4`, legacy `.txt`, `.transcript.*`, `.file`, `canvas-` pdf) plus `.part`, `notes.docx`, a user subfolder and an unmatched course folder: the preview lists exactly the expected moves (mapping table in design/07) and skips with reasons; planning changes no byte, no name, no mtime in the tree.
  - Evidence: pytest `test_library_migration.py` (tree snapshot before/after plan).
- [ ] AC8 (R8 apply): Apply performs exactly the previewed moves; destination created meanwhile → skipped `destination_exists`; source size/mtime changed → skipped `source_changed`; the multiset of (inode, size) before == after (nothing deleted, nothing overwritten); every move has a journal `intent` line before and a `done` line after; injected crash after k moves → re-plan + apply finishes the rest with no duplicate move; a second plan after a complete run is empty.
  - Evidence: pytest with fault injection.
- [ ] AC9 (R8, R9 undo): Undo of a run moves every `done` file back; skips (and reports) a file whose original path is occupied or whose moved file changed; removes only empty directories and metadata the run created; second undo is a no-op; undo after new downloads into a migrated lecture folder keeps the new files and the folder.
  - Evidence: pytest.
- [ ] AC10 (R5 after R8): After apply, an artifact job (skip) on the migrated lectures performs zero downloads/transcriptions; course detail marks them downloaded; players' subtitle rule holds (`<stem>.mp4` + `<stem>.srt` in the same folder).
  - Evidence: pytest end-to-end on the temp tree.
- [ ] AC11 (R10): Plan/apply/undo return 409 `library_busy` while a Python download/artifact job is active; download/artifact job start returns 409 `library_tidy_running` while apply/undo runs; UI disables "정리하기" while any download (incl. native) is busy.
  - Evidence: pytest API + frontend contract test.
- [ ] AC12 (R9): UI: one-line notice in the course pane when the opened course has old-layout files ("이 과목에 예전 방식으로 저장된 파일이 n개 있어요 · 정리하기 / 나중에"); Settings "저장 폴더" gets "폴더 정리…" and "마지막 정리 되돌리기"; preview dialog shows counts, per-course/week grouped moves (from → to), skipped items with reasons, the open-files note and an explicit "정리하기" button; result shows moved/skipped counts with "되돌리기". Copy as design/09.
  - Evidence: frontend contract tests; render + screenshots of each state (light/dark).
- [ ] AC13 (R1–R10, runtime): Owner check on an installed develop build with the real `~/Downloads/SSU Lecture Downloader`: preview matches expectations, apply, open a migrated mp4 in a player (subtitles load), re-open the courses (all marked downloaded, no re-download on "받기"), undo once, apply again.
  - Evidence: owner report + screenshots (manual checkpoint; owner-only action).
- [ ] AC14 (R11): PL-PY-PIPELINE suites stay green unchanged except path expectations; standalone STT/audio endpoints keep `<stem>.transcript.*` naming.
  - Evidence: existing pytest suites.

## Design contract

- Profile decision: STANDARD — new on-disk metadata, a multi-step file migration with journal/undo, dedup authority change and UI flow; no money/authorization; moves are reversible and nothing is deleted, so not DEEP.
- Required artifacts: design/00, 02, 04, 05, 07, 08, 09. Context map, current schema, state machines N/A with reasons in the index (migration run lifecycle drawn in the review pack and specified in 07).
- Current-system evidence: design/00 § Source evidence.
- Aggregate / transaction boundary: one lecture folder = one consistency unit (its `.ssu/item.json` + files); one migration run = one journal; each move is one exclusive filesystem operation; no database.
- Physical schema boundary: new files `.ssu/item.json` (schema 1), `<week>/.ssu/materials.json` (schema 1), `.ssu/transcript.md|json`, `<root>/.ssu/migrations/<run_id>.jsonl`; shapes in design/04.
- Concurrency / idempotency boundary: `LibraryLock` in AppContext (tidy-up vs Python download jobs); exclusive move (hard link + unlink on APFS/HFS+, check + rename fallback); `plan_id` single-use; `run_id` journal makes undo idempotent.
- Migration / rollback boundary: legacy layout stays readable forever (fallback), so reverting LAYOUT-PY-PATHS only changes where new files go; a tidy-up run is undone by its journal; reverting LAYOUT-PY-MIGRATION leaves moved files valid (new layout is read by LAYOUT-PY-PATHS).
- Design-to-acceptance trace: design/08-acceptance-trace.md
- Visual review pack: design/09-design-review.md
- Review and validation: validate_design.py valid; self-checklist review pass (findings in design/00); owner approves the exact digest.

## Delivery map

### L1: New layout, dedup, tidy-up

- Status: PLANNED
- Outcome: every new download lands in the week → lecture layout with hidden metadata; old files are recognised (no re-download) and can be tidied with preview, journal and undo.
- Included acceptance: AC1–AC14
- Non-goals: R12
- Estimate: 2–2.5 days after PL-PY-PIPELINE MERGED, medium confidence.
- PR map:
  - LAYOUT-PY-PATHS:
    - Status: PLANNED
    - Outcome: `core/library_layout.py` (week folder, stem, visible names, collisions, PathRules macOS/Windows); `core/library_meta.py` (`item.json` / `materials.json` atomic read/write, bounded parser); `LibraryIndex` (new-layout scan + legacy `[id]` fallback, role resolution video/audio/srt/text/md/json); `plan_download_path` and `find_downloaded_files` delegate to it; `lms_service` passes the content item (week, title) to the planner for batch, single, native and Canvas downloads; `LecturePipeline` (from PL-PY-PIPELINE) takes `LecturePaths` instead of `_ItemPaths`; `stt.write_transcript_files` takes explicit paths (standalone endpoints keep old naming); `CanvasFileDownloadRequest` optional `module_name`/`week_position`.
    - Included acceptance: AC1–AC6, AC14
    - Actual production-code changed lines: informational; fill after a snapshot exists
    - Affected boundaries: download path planning (all download entrypoints), dedup, artifact pipeline paths, transcript writer, course-detail `downloaded_path`
    - Deployment compatibility: legacy layout read by the fallback, so an update never re-downloads; an older build after a downgrade does not see new-layout files (re-download risk only after downgrade — documented in design/07)
    - Rollback/containment: revert PR; files already written in the new layout stay valid but an older build would not recognise them
    - Observability: progress events carry the new `output_path` values; debug log line per LibraryIndex scan (counts, ignored metadata)
    - Review map: no id in visible names, byte-length truncation, collision determinism, legacy fallback covers every role, `자막.txt` last, metadata parser bounds and root containment, PL-PY-PIPELINE behaviour unchanged
    - Split decision: STANDALONE — one owner (LibraryLayout/Index), one rollback
    - Architecture-surface ownership: dedup authority, metadata files
    - Failure/recovery owner coverage: LibraryLayout, LibraryIndex
    - Cleanup inventory coverage: none new (source-video/intermediate-audio removal stays with LecturePipeline)
    - Refactor boundary: new `core/library_layout.py`, `core/library_meta.py`; edits in `core/download.py`, `core/lms_service.py`, `core/stt.py`, `service/artifacts.py`, `service/routers/credentials.py`, `service/routers/lms_pages.py`, `service/api_models.py`
    - PR role: STANDALONE
    - PR merge target/ref: develop
    - PR lifecycle state: NOT_CREATED
    - Dependencies: Design approval (done); PL-PY-PIPELINE MERGED (#10 4ab55da, done)
    - Owned paths/component: as sprints/sprint-1.md § LAYOUT-PY-PATHS (re-checked on d0591ca): `core/library_layout.py` (new), `core/library_meta.py` (new), `core/download.py`, `core/lms_service.py` (planner call sites, internal `BatchDownloadResult.library_paths`, per-course lock), `core/stt.py` (`TranscriptionRequest.paths`), `core/stt_remote.py` (writer call site only), `core/postprocess_media.py` (`AudioExtractionRequest.output_path` only), `service/artifacts.py` (`_ItemPaths` → `LecturePaths` only), `service/routers/credentials.py`, `service/routers/lms_pages.py`, `service/api_models.py`, `platforms/{base,macos,windows}.py` (`path_rules()`), tests incl. `tests/test_library_layout.py`, `tests/test_library_index.py` (new), `tests/test_artifact_pipeline.py` (path expectations only)
    - Integration point (d0591ca): `_ItemPaths` still exists in `service/artifacts.py` (l.39–58), used at `LecturePipeline._steps` l.118 (→ `_ensure_mp4/_ensure_audio/_ensure_transcript`) and `ArtifactPipeline._needs_video` l.450; transcript writers at `core/stt.py` l.324 and `core/stt_remote.py` l.674; audio path at `core/postprocess_media.py` l.820. Paths flow from the dry-run plan via the internal `BatchDownloadResult.library_paths` (not serialised by `batch_payload`, so the API stays unchanged per design P8)
    - Shared contracts/resources: design/04 `item_json`, `materials_json`; design/07 § Name mapping
    - Verification lanes: pytest full + new layout suite
    - Next-unit condition: MERGED unlocks LAYOUT-PY-MIGRATION
    - Rollback: revert
  - LAYOUT-PY-MIGRATION:
    - Status: PLANNED
    - Outcome: `core/library_migration.py` (scan + attribute + plan, exclusive move, write-ahead journal, apply with revalidation, undo, run list); `service/routers/library.py` (`GET /api/library/legacy-summary`, `POST /api/library/tidy/plan/jobs`, `POST /api/library/tidy/apply/jobs`, `POST /api/library/tidy/undo/jobs`, `GET /api/library/tidy/runs`); `LibraryLock` in AppContext; download/artifact job start checks the lock; course detail gains additive `legacy_file_count`.
    - Included acceptance: AC7, AC8, AC9, AC10, AC11 (backend)
    - Affected boundaries: user files in the download folder (moves), new internal API, job start admission
    - Deployment compatibility: additive endpoints and field; nothing runs without an explicit UI action
    - Rollback/containment: per-run undo; revert PR leaves moved files valid (read by LAYOUT-PY-PATHS) and the journals on disk
    - Observability: journal per run; job result with counts per reason; no file contents logged
    - Review map: never delete/overwrite (exclusive move), revalidation at apply, journal intent-before-move, symlinks not followed, paths stay under the root, undo preconditions, lock on every entry, idempotency
    - Split decision: STANDALONE — own recovery owner (MigrationRunner) and own rollback (undo)
    - Architecture-surface ownership: migration journal, user-file moves
    - Failure/recovery owner coverage: MigrationRunner, LibraryLock
    - Cleanup inventory coverage: undo (empty dirs it created, metadata it wrote); plan TTL
    - PR role: STANDALONE
    - PR merge target/ref: develop
    - PR lifecycle state: NOT_CREATED
    - Dependencies: LAYOUT-PY-PATHS MERGED
    - Owned paths/component: `src/ssu_lecture_downloader/core/library_migration.py` (new), `service/routers/library.py` (new), `service/routers/__init__.py` / `service/api.py` (router registration), `service/app_context.py` (lock), `service/routers/download.py` (admission check), `service/routers/credentials.py` (`legacy_file_count`), `tests/test_library_migration.py` (new), `tests/test_api_library.py` (new)
    - Shared contracts/resources: design/09 § 내부 API 계약 (frozen at approval)
    - Verification lanes: pytest full + migration suite (temp trees, fault injection)
    - Next-unit condition: MERGED unlocks LAYOUT-UI-TIDY merge
    - Rollback: undo per run; revert
  - LAYOUT-UI-TIDY:
    - Status: PLANNED
    - Outcome: course-pane notice, Settings entries, `TidyDialog` (preview → 정리하기 → result → 되돌리기), busy gate, page attachments pass `module_name`/`week_position`, error copy for `library_busy` / `library_tidy_running` / `tidy_plan_expired`.
    - Included acceptance: AC11 (UI), AC12
    - Affected boundaries: React UI only
    - Deployment compatibility: hides the notice when the backend has no `legacy_file_count` (old backend)
    - Rollback/containment: revert
    - Observability: none new
    - Review map: UI reviewer checks the screen table in design/09, copy, a11y (dialog focus, list semantics), long lists (virtualised or capped with "n개 더"), screenshots light/dark
    - Split decision: STANDALONE — separate lane once the internal API is frozen
    - PR role: STANDALONE
    - PR merge target/ref: develop
    - PR lifecycle state: NOT_CREATED
    - Dependencies: Design approval; PL-UI-STATES MERGED (shared `types.ts`/`errors.ts`); develop in parallel with LAYOUT-PY-MIGRATION against design/09 § 내부 API 계약; merge after LAYOUT-PY-MIGRATION MERGED
    - Owned paths/component: `frontend/src/components/TidyDialog.tsx` (new), `frontend/src/components/SettingsDialog.tsx`, `frontend/src/components/LecturePane.tsx` (notice), `frontend/src/components/PageAttachments.tsx` / `hooks/usePageAttachments.ts` (week fields), `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/lib/errors.ts`, `frontend/src/App.tsx` (wiring), `frontend/scripts/contract-test.mjs`
    - Rollback: revert
- QA checkpoint: AC13 after all three units MERGED and the owner installs a develop build (installing/replacing the Mac app and running the tidy-up on the real folder stay owner-only).

- Rollout/rollback: no flag; LAYOUT-PY-PATHS changes where new files go on install; the tidy-up only runs on an explicit "정리하기"; undo per run.

## Autonomous sprint

- Sprint state: SPRINT_PLANNED (sprints/sprint-1.md, sha256 f18c3c7241c41d3aaf8c39e9d46fafd75c4d71b8f02925a919a55ee3bf75dab0)
- Planning base SHA: d0591cab533f4358c8c354855c0d8ec537f3ccc3
- Base drift policy: ALLOW_IF_DISJOINT
- Queue (initial): LAYOUT-PY-PATHS = READY; LAYOUT-PY-MIGRATION = WAITING_DEPENDENCY (LAYOUT-PY-PATHS); LAYOUT-UI-TIDY = WAITING_DEPENDENCY (LAYOUT-PY-PATHS; merge gate LAYOUT-PY-MIGRATION MERGED)
- Reservations / claims: none
- Budget snapshot: time 2 weeks (expiry 2026-11-13); units 0/3; WIP 2 impl lanes; repair 3/unit; same-failure 2; retry 3 — consumed 0
- Review: CODE_POLISH_QUORUM, quorum 2, PARALLEL, cycle budget 3; code-polish `round_gate.py` + `shared/scripts/review_quorum.py`
- Merge: squash into develop under standing grant `merge-ssu-develop`, exact frozen head
- Owner-only: running the tidy-up on the real download folder, app reinstall, AC13 checkpoint
- Upstream event contract: per-lecture record § Design amendment (points 1–5, 4a, 4b) unchanged by this sprint
- Confirmation evidence: owner 2026-10-30 (via coordinator) chose "이 순서로 진행 (Recommended)" in answer to "다운로드 폴더 정리 작업을 승인하신 설계 그대로 자동 진행할까요? 순서는 ① 새 경로 규칙 → ② 기존 파일 정리 백엔드 + ③ 정리 화면(병렬) → ④ 실제 폴더에서 직접 확인이고, 각 단계는 리뷰 2명 통과 후 develop에 병합해요. 실제 다운로드 폴더를 옮기는 건 마지막에 직접 하실 때만이에요." — bound to sprint manifest sha256:f18c3c7241c41d3aaf8c39e9d46fafd75c4d71b8f02925a919a55ee3bf75dab0 (re-hashed unchanged after confirmation; validate_review_topology.py ok). Design digest approval: "추천안 그대로 승인 (Recommended)" 2026-10-30.

## Safety and rollout

- Async candidate strategy: NOT_SELECTED
- Selection rationale / alternative evidence boundary: local desktop file layout; safety from temp-tree tests with fault injection, inode-preservation assertions, the preview-before-move UX, the undo journal and the owner's checkpoint on the real folder.
- Release state: N/A
- Activation mode: layout always on after install; tidy-up per explicit owner action
- Kill switch / rollback: undo per run; revert PRs

## Decisions and assumptions

### D1: Week → lecture folder layout

- Status: CONFIRMED
- User answer (2026-10-30, AskUserQuestion): "주차 → 강의별 폴더" with `<과목>/05주차/선대_5_2/{선대_5_2.mp4, 선대_5_2.srt, 선대_5_2 자막.txt, 선대_5_2 음성.mp3}`; `.srt` has the same stem as the video so players load it automatically.
- Confirmed at: 2026-10-30

### D2: Hidden metadata and tidy-up of existing files

- Status: CONFIRMED
- User answer (2026-10-30, AskUserQuestion): "숨김 폴더 + 기존 파일도 정리" — transcript `.md`/`.json` and the internal content id go into a hidden per-lecture `.ssu/`; no id in visible names; already-downloaded files are moved into the new layout and must not be downloaded again; the owner wants to see a preview before anything moves.
- Confirmed at: 2026-10-30

### D3: Sequenced after PL-PY-PIPELINE

- Status: CONFIRMED (brief 2026-10-30)
- Reason: PL-PY-PIPELINE rewrites `service/artifacts.py` and touches `core/stt.py` / `core/lms_service.py`; this feature reuses its `LecturePipeline` and only replaces path resolution.

### D4: Owner choices Q1–Q6 at the Design Gate

- Status: CONFIRMED
- User answer (2026-10-30, Design Gate via coordinator): "추천안 그대로 승인 (Recommended)" to "다운로드 폴더 구조 설계(과목 → 주차 → 강의 폴더, 예전 파일은 미리보기 후 정리·되돌리기 가능)를 승인할까요?" — the option took every recommended choice:
  - Q1: module without a week number → folder named after the module (not `기타`).
  - Q2: lecture materials directly under the week folder.
  - Q3: lecture folder/file names from the title shown in the app list.
  - Q4: tidy-up offered in the course notice line and in Settings.
  - Q5: `(2)` duplicate copies moved into the same lecture folder as `<stem> (2).<ext>`.
  - Q6: lectures no longer listed in a matched course → `기타/<title>/` (unmatched course folders always left in place).
- Confirmed at: 2026-10-30
- Note: design/09 § 확인이 필요한 결정 still shows these as questions; it is kept byte-identical so the approved digest stays valid — this record is the authority for the answers.

## Open questions

- O1 (verify in LAYOUT-PY-PATHS): how often real LMS modules carry `week_position` at module level (the parser reads only `module.week_position`; one test fixture puts it inside `content_data`). Check with the owner's 4 current courses via course detail; the `module_name` number fallback covers "n주차" names either way.
- O2 (verify in LAYOUT-PY-PATHS): materials saved as `.file` (unknown extension) — use the LMS `file_name` extension when the resolved one is generic; scoped as a small fix inside the path builder, not a new feature.

## Progress and evidence

- 2026-10-30: sprint-1 manifest CONFIRMED by the owner ("이 순서로 진행 (Recommended)", via coordinator) for sha256:f18c3c7241c41d3aaf8c39e9d46fafd75c4d71b8f02925a919a55ee3bf75dab0; manifest bytes unchanged, re-hash identical, validate_review_topology.py ok.
- 2026-10-30: sprint-1 manifest written (~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-download-folder-layout/sprints/sprint-1.md); planning base origin/develop d0591cab533f4358c8c354855c0d8ec537f3ccc3 (read-only fetch) after PL-PY-PIPELINE #10 and PL-UI-STATES #11 merged; integration re-check: `_ItemPaths` still in artifacts.py (l.39–58, 118, 450), extra writer call sites stt_remote.py l.674 and postprocess_media.py l.820 added to LAYOUT-PY-PATHS owned paths; validate_review_topology.py ok (0 failures); sha256 f18c3c7241c41d3aaf8c39e9d46fafd75c4d71b8f02925a919a55ee3bf75dab0. Record → SPRINT_CONTROL / AUTONOMOUS_SPRINT; cursor → AGENT_SQUAD dispatch LAYOUT-PY-PATHS. Design digest unchanged (63b53a26…c769).
- 2026-10-30: Design Gate GO — owner "추천안 그대로 승인 (Recommended)" (Q1–Q6 recommended, recorded as D4). validate_design.py re-run → valid, 0 errors, 0 warnings, artifact-set sha256:63b53a2600fab58b0f6efd7a66cd51b5ad517ec522a417d69120b2746685c769 unchanged; Design status APPROVED. Design artifacts untouched (09 not edited to keep the digest). Next: FEATURE_PLAN final manifest; implementation blocked until PL-PY-PIPELINE MERGED.
- 2026-10-30: validate_design.py --record feature.md → valid, 0 errors, 0 warnings; artifact-set sha256:63b53a2600fab58b0f6efd7a66cd51b5ad517ec522a417d69120b2746685c769 (00, 02, 04, 05, 07, 08, 09). Self-checklist review F1–F10 in design/00 (no independent reviewer agent — disclosed). PlantUML/DBML/Mermaid renderers not run (not installed) — sources not rendered. Gate: USER_DECISION.
- 2026-10-30: plan + STANDARD design drafted from a code survey of develop e2f0714 (core/download.py, core/lms.py, core/lms_service.py, core/stt.py, core/postprocess_media.py, service/artifacts.py, routers/credentials.py, routers/lms_pages.py, security.py, app_context.py, native_download.rs, frontend content.ts/api.ts/SettingsDialog.tsx) and a read-only name listing of the owner's real download folder (4 courses, 43 files). PL-PY-PIPELINE design (approved digest 8a74ddbd…) read; its worktree has uncommitted changes in artifacts.py/stt.py/lms_service.py — not merged yet. Design status AWAITING_APPROVAL.
