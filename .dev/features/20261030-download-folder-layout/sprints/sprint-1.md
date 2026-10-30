# Sprint manifest: download-folder-layout sprint-1 (week → lecture layout, dedup over both layouts, safe tidy-up with preview and undo)

- Sprint ID: download-folder-layout-sprint-1
- Sprint version: 1
- Created: 2026-10-30
- Expiry: 2026-11-13 (2 weeks; replan after expiry)
- Planning base: origin/develop @ d0591cab533f4358c8c354855c0d8ec537f3ccc3 (fetched read-only 2026-10-30; contains PL-PY-PIPELINE #10 4ab55da and PL-UI-STATES #11 d0591ca)
- Base-drift policy: ALLOW_IF_DISJOINT — other features also write develop; before each reservation run `git diff d0591cab..origin/develop --name-only` against the unit's owned paths; an overlap stops the unit for a rebase/replan decision
- Outcome: new downloads land in `<과목>/<NN주차|모듈 이름|기타>/<강의 제목>/` with plain visible names (`제목.mp4`, `제목.srt`, `제목 자막.txt`, `제목 음성.mp3`) and hidden `.ssu/` metadata; files in the old flat layout are recognised (never downloaded or transcribed again) and can be moved into the new layout through a preview → "정리하기" → result → "되돌리기" flow that never deletes or overwrites.
- Target user: the owner (daily macOS user, `~/Downloads/SSU Lecture Downloader`, 4 courses / 43 files today).
- Hypothesis: a week → lecture folder tree with the subtitle next to the video makes downloaded lectures directly usable in Finder and players, and a previewed, undoable tidy-up lets the owner adopt it without losing or re-downloading anything.
- Non-goals: R12 (renaming folders after LMS renames, moving other terms' courses, Windows hidden attribute / long-path activation, configurable layout); any change to the per-lecture pipeline's concurrency, events or failure semantics; Mac Studio / modelhost.
- Requirements: R1–R11 (feature.md)
- Acceptance IDs: AC1–AC12, AC14 (units) + AC13 (owner checkpoint CP-TIDY-DOGFOOD) (design/08-acceptance-trace.md)
- Design profile: STANDARD
- Canonical design root: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-download-folder-layout/design
- Design approval evidence: owner 2026-10-30 "추천안 그대로 승인 (Recommended)" at the Design Gate (Q1–Q6 recommended; feature.md D4)
- Design artifact-set SHA-256: 63b53a2600fab58b0f6efd7a66cd51b5ad517ec522a417d69120b2746685c769
- Upstream contract carried in: per-lecture pipeline record `20261030-per-lecture-pipeline/feature.md` § Design amendment (2026-10-30, points 1–5, 4a, 4b) is the event contract of record. This sprint adds no pipeline stage and changes no coalescing/cap rule; it only changes `output_path` values. The tidy-up jobs live in their own registry and throttle their `tidy_moving` progress (≤ 1 event per 0.5 s or per 10 files), so the 400-event cap rules of point 4/4a/4b are not stressed.

## Integration point (re-checked on d0591ca)

- `service/artifacts.py` still defines `_ItemPaths` (l.39–58, `for_video(video_path, audio_format)` → `plan_audio_output_path` + `stt.transcript_output_paths` + `stt.transcript_srt_path`); it is used in `LecturePipeline._steps` (l.118) and passed to `_ensure_mp4` / `_ensure_audio` / `_ensure_transcript` (l.172, 184, 205), and in `ArtifactPipeline._needs_video` (l.450).
- LAYOUT-PY-PATHS replaces `_ItemPaths` with `LecturePaths` (core/library_layout.py). Paths come from the dry-run plan: `batch_download_lectures` returns an internal `BatchDownloadResult.library_paths: dict[module_item_id, LecturePaths]` (not serialised — `batch_payload` builds explicit keys, so the API payload stays unchanged, as design P8 requires); `ArtifactPipeline.run` keeps it and `LecturePipeline` / `_needs_video` look up their lecture there (fallback for an item without an entry: the CURRENT `for_video` derivation, kept private).
- Transcript writing has two call sites that must receive explicit lecture paths: `core/stt.py` l.324 (local) and `core/stt_remote.py` l.674 (remote) via an optional `TranscriptionRequest.paths`; `None` keeps today's naming for standalone `/api/stt/jobs`.
- Audio naming `<stem> 음성.mp3`: `core/postprocess_media.py` `extract_audio` uses `plan_audio_output_path(input, request.output_dir, fmt)` (l.820); `AudioExtractionRequest` gains an optional explicit `output_path`; standalone `/api/media/audio/jobs` unchanged.
- `on_item_ready` (lms_service l.1282–1302, 1453–1458) and `StageGates` (app_context l.109) are reused unchanged.

## Metrics

- Primary success metric: AC13 — the owner's real folder is tidied after a preview, migrated videos load their subtitles in a player, and re-opening every course shows all lectures as downloaded with zero re-downloads. Evidence source: owner report + screenshots + the run journal counts.
- Guardrail metrics: zero deleted or overwritten user files (AC8 inode/size multiset test; AC13 journal: every `intent` has a `done` or `skip`); zero re-downloads/re-transcriptions of a lecture present in either layout (AC4/AC10 event assertions). Evidence: pytest suites, journal.
- Observation window: from the owner's develop build install until the AC13 report.
- Comparison/decision threshold: any lost or overwritten file, or any re-download of an existing lecture → stop, keep/recommend undo, fix before further merges.

## Eligible unit queue

- Eligible unit IDs: LAYOUT-PY-PATHS, LAYOUT-PY-MIGRATION, LAYOUT-UI-TIDY
- Dependency graph: (PL-PY-PIPELINE MERGED #10, PL-UI-STATES MERGED #11 — satisfied) → LAYOUT-PY-PATHS → LAYOUT-PY-MIGRATION ∥ LAYOUT-UI-TIDY (UI implemented in parallel against design/09 § 내부 API 계약, merged only after LAYOUT-PY-MIGRATION MERGED) → CP-TIDY-DOGFOOD (AC13)
- Selection/rejection rule: select the lowest-ordered unit whose predecessors are MERGED. LAYOUT-UI-TIDY may start once LAYOUT-PY-PATHS is MERGED (it needs the canvas request fields and `downloaded_path` behaviour) and runs in parallel with LAYOUT-PY-MIGRATION; its merge gate requires LAYOUT-PY-MIGRATION MERGED. Reject a unit whose owned paths overlap a newer develop change until rebased.
- Ownership / shared-resource exclusivity: LAYOUT-PY-PATHS and LAYOUT-PY-MIGRATION are sequential (both touch `service/routers/credentials.py`); LAYOUT-UI-TIDY touches only `frontend/**` and never Python or `frontend/src-tauri/**`. `frontend/scripts/contract-test.mjs` is append-only. A needed Rust change, a change to pipeline event semantics, or a change to `core/jobs.py` coalescing is a stop condition.
- Approved initial queue state: LAYOUT-PY-PATHS = READY; LAYOUT-PY-MIGRATION = WAITING_DEPENDENCY (LAYOUT-PY-PATHS); LAYOUT-UI-TIDY = WAITING_DEPENDENCY (LAYOUT-PY-PATHS; merge gate LAYOUT-PY-MIGRATION MERGED).

## Delivery bundle

- N/A — every unit is a STANDALONE PR to develop, independently revertible (design/07 § Rollback).

## Review topology

- Review topology: CODE_POLISH_QUORUM
- Review quorum: 2
- Review scheduling: PARALLEL
- Review cycle budget: 3
- Unit review topology map: LAYOUT-PY-PATHS=INHERIT, LAYOUT-PY-MIGRATION=INHERIT, LAYOUT-UI-TIDY=INHERIT
- Unit review cycle budget map: LAYOUT-PY-PATHS=INHERIT, LAYOUT-PY-MIGRATION=INHERIT, LAYOUT-UI-TIDY=INHERIT
- Single-reviewer exception scope: N/A
- Single-reviewer exception rationale: N/A
- Single-reviewer exception approval: N/A
- Review mechanics: every unit is reviewed through `code-polish` with `skills/code-polish/scripts/round_gate.py` (start / next / record per round on the exact head + patch hash) and `shared/scripts/review_quorum.py` (prepare → dispatch → verify → finalize; adjudicate on disagreement); convergence = quorum 2 clean on one immutable snapshot.

## Budgets

- Time budget: 2 weeks (expiry)
- Maximum-unit budget: 3
- WIP budget: 2 implementation lanes, 3 lanes total including review
- Repair-pass budget: 3 per unit
- Same-failure budget: 2
- Retry budget: 3 (CI infra flakes)
- External-cost budget: GitHub Actions minutes (public repo); no release runs in this sprint.
- Consumed / remaining: tracked in the control record.

## Authority matrix

- LOCAL_READ: ALLOW
- LOCAL_RECORD_WRITE: ALLOW
- GIT_WORKTREE: ALLOW (dedicated worktrees only, `~/Project/cafitac/tools/ssu-lecture-downloader-wt/<unit>`)
- LOCAL_IMPLEMENT_TEST: ALLOW (tests run on temp trees only; never against the owner's real download folder)
- COMMIT_PUSH: ALLOW (cafitac identity, feature branches only; no secrets — public repo; review includes a secret scan of the diff)
- DRAFT_PR: ALLOW (cafitac/ssu-lecture-downloader → develop)
- TRACKER_SYNC: DENY (GitHub PRs are the tracker projection)
- REMOTE_TEST_RUNNER: DENY
- WORKTREE_CLEANUP: ALLOW (AUTO_AFTER_SAFE_TERMINAL only)
- REAL_DOWNLOAD_FOLDER_WRITE (running plan/apply/undo or any write on `~/Downloads/SSU Lecture Downloader`): USER_ONLY
- APP_REINSTALL (/Applications): USER_ONLY
- RELEASE_WORKFLOW_RUN / RELEASE_PUBLISH: DENY (not in scope)
- Merge to develop: main task under the standing grant `merge-ssu-develop` (squash) on the exact frozen head after the gates below; LAYOUT-UI-TIDY only after LAYOUT-PY-MIGRATION MERGED.

## Finalization

- Delivery finalization: AUTO_AFTER_GATES
- Exact PR/bundle scope: the three unit PRs above, target develop
- Required gate set: code-polish quorum 2 converged (round_gate + review_quorum) on the exact head; GitHub Actions green on that exact head (`macos` job: pytest, sidecar build, cargo test, typecheck, test:contract, tauri build; `windows` job); no secret material in the diff; Python units: the unit's acceptance tests listed below on temp trees; LAYOUT-UI-TIDY: contract tests + render screenshots of every TidyDialog state, the notice and the Settings entries in light and dark (visual check before reporting done).
- Approved-scope evidence: owner Design Gate approval 2026-10-30 "추천안 그대로 승인 (Recommended)" on design digest 63b53a26…c769, whose review pack (design/09 § 전달 계획 참고) presented exactly this unit order and the owner checkpoint; coordinator instruction 2026-10-30 to freeze it as sprint-1 (recorded in the control record with this manifest's sha256).
- Merge: squash into develop under `merge-ssu-develop` on the exact frozen head. Merge never publishes a release and never runs a tidy-up.

## Stop, recovery, completion

- Stop: any code path that deletes or overwrites a user file, or moves a file outside the planned set; a needed change to pipeline event semantics, `core/jobs.py` coalescing/cap, or `frontend/src-tauri/**`; a test touching the owner's real download folder; same-failure budget exhausted; any secret in a diff or log; a design deviation (schema of `item.json`/journal, name mapping, API contract) → back to FEATURE_DESIGN (REVISE).
- Pause: owner interrupts; GitHub Actions unavailable; base drift overlapping owned paths.
- Recovery: repair within the cycle budget; otherwise escalate to the owner.
- Rollback: revert the unit PR (design/07 § Rollback); a tidy-up run on the real folder is undone by its journal ("되돌리기").
- Sprint-complete (desired finish state): three units MERGED to develop and the owner's AC13 report recorded.
- Sprint-boundary: Windows activation of PathRules / hidden attribute belongs to the Windows platform work, not this sprint.
- Mandatory human checkpoints: CP-TIDY-DOGFOOD (AC13), owner-only.

## Unit details

### LAYOUT-PY-PATHS

- Repository/target/base: cafitac/ssu-lecture-downloader, develop, origin/develop@d0591cab
- Outcome: `core/library_layout.py` (week folder, stem, visible names, collisions with ` (n)`, `PathRules` macOS + Windows, `LecturePaths` with per-role targets and existing files); `core/library_meta.py` (`.ssu/item.json` / `<week>/.ssu/materials.json` atomic write + bounded parser, legacy name parser); `LibraryIndex` (new-layout scan + read-only legacy `[id]` fallback); `plan_download_path` / `find_downloaded_files` delegate to it (thin wrappers kept for CLI/standalone); `lms_service` passes the content item (week_position, module_name, title, lesson_position) to the planner for batch, single (`download_lecture`), native (`native_download_plan`) and Canvas downloads, assigns suffixes single-threaded in plan order, creates the lecture dir under a per-course lock, and returns the internal `BatchDownloadResult.library_paths`; `service/artifacts.py` replaces `_ItemPaths` with `LecturePaths` at `LecturePipeline._steps`, `_ensure_mp4/_ensure_audio/_ensure_transcript` and `ArtifactPipeline._needs_video`; explicit transcript paths through `TranscriptionRequest.paths` (local `stt.py` and remote `stt_remote.py` writers) and explicit audio `output_path` in `AudioExtractionRequest`; `CanvasFileDownloadRequest` optional `module_name`/`week_position`; course detail / page files `downloaded_path` from LibraryIndex; O1 (`week_position` coverage) and O2 (`.file` extension from LMS `file_name`) verified and recorded.
- Acceptance subset: AC1, AC2, AC3, AC4, AC5, AC6, AC14
- Dependencies / next-unit condition: PL-PY-PIPELINE MERGED (#10, satisfied), Design APPROVED (satisfied) / MERGED unlocks LAYOUT-PY-MIGRATION and LAYOUT-UI-TIDY implementation
- Owned paths: `src/ssu_lecture_downloader/core/library_layout.py` (new), `src/ssu_lecture_downloader/core/library_meta.py` (new), `src/ssu_lecture_downloader/core/download.py`, `src/ssu_lecture_downloader/core/lms_service.py` (planner call sites, `BatchDownloadResult.library_paths`, per-course lock), `src/ssu_lecture_downloader/core/stt.py` (`TranscriptionRequest.paths`, writer paths), `src/ssu_lecture_downloader/core/stt_remote.py` (writer call site l.674 only), `src/ssu_lecture_downloader/core/postprocess_media.py` (`AudioExtractionRequest.output_path` only), `src/ssu_lecture_downloader/service/artifacts.py` (`_ItemPaths` → `LecturePaths` only; no change to pipeline order, gates, events or failure handling), `src/ssu_lecture_downloader/service/routers/credentials.py` (`downloaded_path`), `src/ssu_lecture_downloader/service/routers/lms_pages.py` (`downloaded_path`, canvas week fields), `src/ssu_lecture_downloader/service/api_models.py` (canvas request fields), `src/ssu_lecture_downloader/platforms/base.py`, `platforms/macos.py`, `platforms/windows.py` (`path_rules()` only), tests: `tests/test_library_layout.py` (new), `tests/test_library_index.py` (new), `tests/test_download_planner.py`, `tests/test_download.py`, `tests/test_service.py`, `tests/test_api.py`, `tests/test_stt.py`, `tests/test_artifact_pipeline.py` (path expectations only)
- Shared contracts/resources: design/04 `item_json`, `materials_json`; design/07 § Name mapping, § Week folder and stem rules, § Collisions, § Dedup; per-lecture event contract (amendment points 1–5, 4a, 4b) unchanged
- Affected boundaries: download path planning (all entrypoints incl. native plan and CLI batch), dedup, artifact pipeline paths, transcript/audio writers, course-detail and page-attachment `downloaded_path`
- Deployment compatibility: legacy layout read by the fallback → no re-download after update; API additive only (canvas request fields); downgrade re-download risk documented in design/07
- Rollback/containment: revert PR; files already written in the new layout stay valid
- Observability: `output_path` in existing events shows the new paths; one debug log line per LibraryIndex scan (counts, ignored metadata)
- Review map: no id in visible names; byte-length truncation; collision determinism under parallel downloads; legacy fallback covers every role incl. legacy `.txt`; `자막.txt` last; metadata parser bounds + root containment + no symlink follow; pipeline behaviour/events unchanged (diff of artifacts.py limited to paths)
- Split decision: STANDALONE — one owner (LibraryLayout/LibraryIndex), one rollback
- Coherent scope/refactor boundary: new layout modules + path call sites; no pipeline logic change
- Risk/reversibility: medium (every download path changes), reversible by revert; no file is moved by this unit
- Verification evidence: green CI + AC1–AC6, AC14 pytest (temp trees, fake LMS/media/STT)
- Design dependencies: design/00 P1–P10, F1, F5–F8; 02; 04; 05 Flow 1; 07 § Name mapping, Week folder, Collisions, Dedup, Compatibility, Windows; 08 rows AC1–AC6, AC14
- Required authority subset: GIT_WORKTREE, LOCAL_IMPLEMENT_TEST, COMMIT_PUSH, DRAFT_PR, WORKTREE_CLEANUP
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: READY

### LAYOUT-PY-MIGRATION

- Repository/target/base: same, develop (starts from LAYOUT-PY-PATHS MERGED)
- Outcome: `core/library_migration.py` (scan + attribute + plan, `ExclusiveMove` link+unlink with check+rename fallback, write-ahead fsynced journal, apply with per-move revalidation, crash recovery on re-plan, undo, run list); `service/routers/library.py` (`GET /api/library/legacy-summary`, `POST /api/library/tidy/plan/jobs`, `POST /api/library/tidy/apply/jobs`, `POST /api/library/tidy/undo/jobs`, `GET /api/library/tidy/runs`, job status/cancel via `add_job_routes`); `LibraryLock` in AppContext; download/artifact/canvas-file job start checks it (409 `library_tidy_running`); plan/apply/undo refuse while Python download jobs run (409 `library_busy`); `tidy_plan_expired`; course detail additive `legacy_file_count`; throttled `tidy_moving` progress.
- Acceptance subset: AC7, AC8, AC9, AC10, AC11 (backend)
- Dependencies / next-unit condition: LAYOUT-PY-PATHS MERGED / MERGED opens the LAYOUT-UI-TIDY merge gate
- Owned paths: `src/ssu_lecture_downloader/core/library_migration.py` (new), `src/ssu_lecture_downloader/service/routers/library.py` (new), `src/ssu_lecture_downloader/service/routers/__init__.py`, `src/ssu_lecture_downloader/service/api.py` (router registration), `src/ssu_lecture_downloader/service/app_context.py` (lock + plan store + job registry), `src/ssu_lecture_downloader/service/routers/download.py` (admission check), `src/ssu_lecture_downloader/service/routers/lms_pages.py` (admission check for canvas-file), `src/ssu_lecture_downloader/service/routers/credentials.py` (`legacy_file_count`), `src/ssu_lecture_downloader/service/api_errors.py` (new codes), tests: `tests/test_library_migration.py` (new), `tests/test_api_library.py` (new)
- Shared contracts/resources: design/09 § 내부 API 계약 (frozen), design/04 `journal_line`, `tidy_plan_memory`, payload tables; design/07 § Migration run, § Concurrency
- Affected boundaries: user files in the download folder (moves only on explicit apply), new internal API, job admission
- Deployment compatibility: additive endpoints/fields; nothing runs without an explicit UI action
- Rollback/containment: per-run undo; revert PR leaves moved files valid for LAYOUT-PY-PATHS and journals on disk
- Observability: journal per run; job result counts per skip reason; no file contents logged
- Review map: never delete/overwrite (exclusive move, `rename` fallback only under the lock); revalidation (size, mtime_ns, inode, destination absent); journal intent-before-move + fsync; no symlink follow, paths contained in root, depth ≤ 2; undo preconditions and empty-only rmdir; lock on every entry and in every download start endpoint; plan_id single use; idempotency
- Split decision: STANDALONE — own recovery owner (MigrationRunner) and own rollback (undo)
- Coherent scope/refactor boundary: new migration module + router + admission checks
- Risk/reversibility: high-impact on user files, contained by preview, revalidation, exclusive move, journal and undo; tests only on temp trees
- Verification evidence: green CI + AC7–AC11 pytest (owner's 43 real names reproduced as empty/sized temp files, fault injection, monkeypatched `os.link` failure, inode multiset)
- Design dependencies: design/00 P11–P14, P16, F2–F4, F9; 05 Flows 2–4; 07 § Migration run, Concurrency, Files open in other apps, Failure and cleanup owner map; 08 rows AC7–AC11
- Required authority subset: GIT_WORKTREE, LOCAL_IMPLEMENT_TEST, COMMIT_PUSH, DRAFT_PR, WORKTREE_CLEANUP (REAL_DOWNLOAD_FOLDER_WRITE not included)
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY

### LAYOUT-UI-TIDY

- Repository/target/base: same, develop (starts from LAYOUT-PY-PATHS MERGED; merge after LAYOUT-PY-MIGRATION MERGED, rebased on it)
- Outcome: course-pane notice from `legacy_file_count` ("이 과목에 예전 방식으로 저장된 파일이 {n}개 있어요…" [정리하기] [나중에], "나중에" in memory until restart); Settings "저장 폴더" adds [폴더 정리…] and [마지막 정리 되돌리기] (disabled while any download incl. native is busy: "다운로드가 끝나면 정리할 수 있어요"); `TidyDialog` states 확인 중 → 미리보기 (grouped 과목 › 주차 › 강의, from → to, collapsed skips with reason copy, open-files note) → 정리 중 ({done}/{total}, 멈추기) → 결과 (폴더 열기 · 되돌리기 · 닫기) / 정리할 파일 없음 / 오류; auto re-plan on `tidy_plan_expired`; page attachments send `module_name`/`week_position`; error copy for `library_busy`, `library_tidy_running`, `tidy_plan_expired`; hidden when the backend lacks the fields/endpoints.
- Acceptance subset: AC11 (UI), AC12
- Dependencies / next-unit condition: LAYOUT-PY-PATHS MERGED (start), LAYOUT-PY-MIGRATION MERGED (merge gate), PL-UI-STATES MERGED (#11, satisfied) / MERGED + both Python units MERGED → CP-TIDY-DOGFOOD
- Owned paths: `frontend/src/components/TidyDialog.tsx` (new), `frontend/src/hooks/useLibraryTidy.ts` (new), `frontend/src/components/SettingsDialog.tsx`, `frontend/src/components/LecturePane.tsx` (notice), `frontend/src/components/PageAttachments.tsx`, `frontend/src/hooks/usePageAttachments.ts`, `frontend/src/lib/api.ts`, `frontend/src/lib/types.ts`, `frontend/src/lib/errors.ts`, `frontend/src/App.tsx` (wiring only), `frontend/src/styles/overlays.css` (dialog list styles), `frontend/scripts/contract-test.mjs` (append)
- Shared contracts/resources: design/09 § 내부 API 계약, § 화면과 문구 (frozen copy); existing `busy` flag (also gates "저장 폴더 변경")
- Affected boundaries: React UI only
- Deployment compatibility: notice/entries hidden against an older backend
- Rollback/containment: revert PR
- Observability: none new (visible states)
- Review map: UI reviewer checks design/09 screen table and copy, dialog focus/a11y (labelled list, Esc, focus return), long lists (cap with "n개 더" or virtualised), busy gate incl. native jobs, screenshots light/dark
- Split decision: STANDALONE — separate lane once the internal API is frozen
- Coherent scope/refactor boundary: new dialog/hook + small mounts; no Python, no Rust
- Risk/reversibility: low; revert
- Verification evidence: green CI + contract tests (notice visibility, dialog state machine, grouping, reasons, busy gate, expired re-plan) + screenshots of every state light/dark with a mocked backend
- Design dependencies: design/00 P15–P16; 09 § 화면과 문구, § 내부 API 계약, run state diagram; 08 rows AC11–AC12
- Required authority subset: GIT_WORKTREE, LOCAL_IMPLEMENT_TEST, COMMIT_PUSH, DRAFT_PR, WORKTREE_CLEANUP
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY

## Checkpoints (not eligible units)

### CP-TIDY-DOGFOOD (AC13), owner-only

- When: all three units MERGED and a develop macOS build is ready.
- Owner steps: install the build (no auto-watch running); open each course (notice shows old-file counts); open the preview on `~/Downloads/SSU Lecture Downloader` and check the list; press 정리하기; open a migrated mp4 in a player (subtitles load); re-open the courses (all lectures marked downloaded) and press 받기 on one (skipped, no transfer); 되돌리기 once (names restored); 정리하기 again.
- Evidence: owner report + screenshots; journal run ids and counts; any skip reasons listed.
