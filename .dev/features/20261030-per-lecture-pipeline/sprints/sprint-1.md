# Sprint manifest: per-lecture-pipeline sprint-1 (P1: per-lecture pipeline in the app)

- Sprint ID: per-lecture-pipeline-sprint-1
- Sprint version: 1
- Created: 2026-10-30
- Expiry: 2026-11-13 (2 weeks; replan after expiry)
- Planning base: origin/develop @ e2f0714fff132c3bead0778ec96cac51504d1e05 (read-only `git fetch` 2026-10-30; grades-view units merged)
- Base-drift policy: ALLOW_IF_DISJOINT — foreign commits (e.g. the auto-update work in ../ssu-lecture-downloader-wt/au-updater-shell, Tauri/Rust) may land on develop; before each reservation run `git diff <planning-base>..origin/develop --name-only` against the unit's owned paths; overlap → rebase and re-verify, never overwrite
- Outcome: each selected lecture runs its own linear chain (download → audio → transcript → cleanup) as soon as its own download finishes; ffmpeg work limited to 2 and the local STT engine to 1 process-wide; remote STT unthrottled with the queue-aware response budget on the interim `whisper_cpp_server` path; one lecture's failure never stops the others; "모두 취소" stops everything; the downloads drawer shows real per-lecture states. Ends at the owner's AC10 check.
- Target user: the owner (Soongsil student, daily macOS user, remote STT on the Mac Studio).
- Hypothesis: removing the batch barrier and the abort-on-first-failure rule shortens multi-lecture MP3/TXT runs and makes failures local, without overloading the MacBook or timing out remote transcripts.
- Non-goals: job-style remote STT provider `stt_job` (STT-JOB-PROVIDER, sprint-2 after AC11); any Mac Studio / modelhost / whisper-server / ComfyUI change; user-configurable limits; persistence of pipeline state across restarts; throughput measurement.
- Requirements: R1, R2, R3 (interim part), R4, R5, R6, R7 (feature.md)
- Acceptance IDs: AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8, AC10 (design/08-acceptance-trace.md)
- Design profile: STANDARD
- Canonical design root: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-per-lecture-pipeline/design
- Design approval evidence: user 2026-10-30 "모두 승인할게" (decisions 1–3 = yes: legacy kind unthrottled with the queue-aware budget; drawer title "진행 중 d/t"; protocol draft sent to the modelhost owner)
- Design artifact-set SHA-256: 8a74ddbd14d8be5c9607b5ad7dceb22d18ef65010f181d8ea67a4990c41e51ee (re-validated unchanged 2026-10-30 before writing this manifest)

## Metrics

- Primary success metric: in the owner's AC10 run with 3 lectures (MP3+TXT) a later lecture's audio extraction starts while an earlier/other lecture is still downloading, and all transcripts finish without `stt_remote_timeout` — evidence: AC10 owner report + drawer screenshots + event `at` timestamps in the job result.
- Guardrail metric: peak concurrent ffmpeg ≤ 2 and local STT ≤ 1 (AC3); no existing suite regresses (CI); one forced failure leaves the other lectures done (AC4/AC10).
- Observation window: from the install of the first develop build containing both units until the owner's AC10 answer.
- Comparison/decision threshold: any lost/overwritten output file, a gate leak (`in_use` ≠ 0 after a job), a cancel that leaves work running, or a remote timeout caused by queue time → stop, fix or revert before sprint-2.

## Eligible unit queue

- Eligible unit IDs: PL-PY-PIPELINE, PL-UI-STATES
- Dependency graph: [design APPROVED] → PL-PY-PIPELINE; PL-UI-STATES (no start dependency) merges only after PL-PY-PIPELINE MERGED; both MERGED → AC10 owner checkpoint. STT-JOB-PROVIDER is not eligible (sprint-2, after AC11 is recorded).
- Selection/rejection rule: select the lowest-ordered READY unit; PL-UI-STATES runs in parallel with the backend lane against the frozen event contract (design/09 § 진행 이벤트 계약) using recorded event logs; reject a merge of PL-UI-STATES while PL-PY-PIPELINE is not MERGED.
- Ownership / shared-resource exclusivity: backend lane owns `src/ssu_lecture_downloader/service/artifacts.py`, `src/ssu_lecture_downloader/service/pipeline_gates.py` (new), `src/ssu_lecture_downloader/service/app_context.py`, `src/ssu_lecture_downloader/core/lms_service.py` (additive `on_item_ready` only), `src/ssu_lecture_downloader/core/stt_remote.py` (response budget only), `src/ssu_lecture_downloader/core/stt.py` (local gate hook), `src/ssu_lecture_downloader/core/jobs.py` (coalesced-stage set only), `tests/test_artifact_pipeline.py` (new), `tests/test_api_robustness.py`, `tests/test_stt_remote.py`; frontend lane owns `frontend/src/lib/jobItems.ts`, `frontend/src/components/DownloadDrawer.tsx`, `frontend/src/lib/types.ts`, `frontend/src/lib/errors.ts`, `frontend/scripts/contract-test.mjs`. The lanes share no file; neither touches `frontend/src-tauri/**` (other agent's area).
- Approved initial queue state: PL-PY-PIPELINE = READY; PL-UI-STATES = READY (merge gated on PL-PY-PIPELINE MERGED).

## Delivery bundle

- N/A — every unit is a STANDALONE PR to develop, independently revertible.

## Review topology

- Review topology: CODE_POLISH_QUORUM
- Review quorum: 2
- Review scheduling: PARALLEL
- Review cycle budget: 3
- Unit review topology map: PL-PY-PIPELINE=INHERIT, PL-UI-STATES=INHERIT
- Unit review cycle budget map: PL-PY-PIPELINE=INHERIT, PL-UI-STATES=INHERIT
- Single-reviewer exception scope: N/A
- Single-reviewer exception rationale: N/A
- Single-reviewer exception approval: N/A

## Budgets

- Time budget: 2 weeks (expiry)
- Maximum-unit budget: 2
- WIP budget: 2 implementation lanes (backend lane + frontend lane), 3 lanes total including review
- Repair-pass budget: 3 per unit
- Same-failure budget: 2
- Retry budget: 3 (CI infra flakes; transient connection errors retried without asking)
- External-cost budget: GitHub Actions minutes within the free allowance (public repo); stop and ask if usage would exceed it.
- Consumed / remaining: tracked in the control record (feature.md).

## Authority matrix

- LOCAL_READ: ALLOW
- LOCAL_RECORD_WRITE: ALLOW
- GIT_WORKTREE: ALLOW (dedicated worktrees only; never ../ssu-lecture-downloader-wt/au-updater-shell)
- LOCAL_IMPLEMENT_TEST: ALLOW (fakes only: fake LMS, fake ffmpeg/extract, fake local engine, fake slow whisper server on localhost; no real LMS, no Mac Studio calls from tests)
- COMMIT_PUSH: ALLOW (cafitac identity, feature branches only; committed text uses `~` instead of `/Users/<name>`; no credentials, tokens, or real lecture content in commits, PRs, fixtures, or records)
- DRAFT_PR: ALLOW (cafitac/ssu-lecture-downloader → develop)
- TRACKER_SYNC: DENY (GitHub PRs are the tracker projection)
- REMOTE_TEST_RUNNER: DENY
- MAC_STUDIO / MODELHOST / WHISPER_SERVER access or change: DENY
- WORKTREE_CLEANUP: ALLOW (AUTO_AFTER_SAFE_TERMINAL only)
- Owner-only (DENY to agents): reinstalling or replacing the installed Mac app, the AC10 real-lecture check, changing the owner's remote STT settings, releases or sharing builds.

## Finalization

- Delivery finalization: AUTO_AFTER_GATES
- Exact PR/bundle scope: the two unit PRs above, target develop
- Required gate set: review quorum 2 converged on the exact head; GitHub Actions green on the exact head (macOS: pytest, lint-imports, typecheck, contract test, cargo test, sidecar + tauri build; windows job as configured); PL-PY-PIPELINE additionally needs the new pipeline suite (AC1–AC6) and the payload-key snapshot (AC8) green; PL-UI-STATES additionally needs rendered screenshots of every row state in design/09 § 화면 문구 (light and dark) reviewed. No Feature-QA lane per unit; the runtime gate is the owner's AC10 check at the sprint boundary.
- Approved-scope evidence: user 2026-10-30 "모두 승인할게" on the design digest above and the proposed sprint shape in feature.md § Autonomous sprint.
- Merge: main task squash-merges into develop under the standing grant `merge-ssu-develop`, only after every gate above (PL-UI-STATES only after PL-PY-PIPELINE MERGED).

## Stop, recovery, completion

- Stop: a needed change to an approved design artifact (event contract, limits, failure/outcome rule, budget formula, ownership) → stop and reopen the Design Gate; any test showing a gate leak, a cancel that leaves work running, or one lecture's failure stopping another; same-failure budget exhausted.
- Pause: GitHub Actions unavailable or over budget; base drift overlapping owned paths; user interrupts.
- Recovery: repair within the cycle budget; otherwise escalate.
- Rollback: revert the unit PR on develop; installed Mac app untouched until the owner reinstalls (previous build stays the fallback).
- Sprint-complete: both units MERGED, the main task builds the macOS app from develop (no install) and presents the AC10 checklist to the owner.
- Sprint-boundary: sprint-2 (STT-JOB-PROVIDER, R8/AC9) is planned only after AC11 (protocol agreed with the modelhost owner and the owner) is recorded; if the agreed protocol differs materially from design/remote-stt-job-protocol.md, the design reopens first.
- Mandatory human checkpoints: AC10 (owner installs the build when no auto-watch is running and runs 3 real lectures).

## Unit details

### PL-PY-PIPELINE

- Repository/target/base: cafitac/ssu-lecture-downloader, develop, origin/develop @ e2f0714 or later (ALLOW_IF_DISJOINT)
- Outcome: process-wide `StageGates` (media 2, local_stt 1; cancel-aware acquire, release in finally) in `service/pipeline_gates.py`, held by AppContext; additive `on_item_ready` callback in `batch_download_lectures` (called once per downloaded/skipped/failed item); `ArtifactPipeline` dispatches per-lecture `LecturePipeline` chains (no barrier, no `_abort`, download failures handled per lecture); new progress stages and `at` per design/09 § 진행 이벤트 계약 (`transcript_waiting`, `transcript_queued` added to coalesced stages); job outcome summary (`succeeded` / `failed` with `partial_failure` or the common code / `cancelled`) plus additive `result.summary` and `result.items_status`; legacy remote response budget `1200 s × (1 + ahead)` capped at 4 h with 60 s connect/upload idle timeout and 429/503 backoff; tests with controllable fakes replacing `test_artifact_job_failure_does_not_run_the_remaining_queued_items`.
- Acceptance subset: AC1, AC2, AC3, AC4, AC5, AC6, AC8 (backend)
- Dependencies / next-unit condition: design APPROVED / MERGED unlocks the PL-UI-STATES merge; both MERGED → AC10
- Owned paths: `src/ssu_lecture_downloader/service/artifacts.py`, `src/ssu_lecture_downloader/service/pipeline_gates.py`, `src/ssu_lecture_downloader/service/app_context.py`, `src/ssu_lecture_downloader/core/lms_service.py`, `src/ssu_lecture_downloader/core/stt_remote.py`, `src/ssu_lecture_downloader/core/stt.py`, `src/ssu_lecture_downloader/core/jobs.py`, `tests/test_artifact_pipeline.py`, `tests/test_api_robustness.py`, `tests/test_stt_remote.py`
- Shared contracts/resources: progress-event contract (design/09, frozen); import-linter contracts (core ↛ service/platforms) must stay green — gates live in service and are passed into core as callables
- Affected boundaries: job concurrency and failure semantics; remote STT response wait
- Deployment compatibility: payload keys unchanged; stages additive; request fields unchanged
- Rollback/containment: revert PR (restores barrier, 1 worker, abort-on-first-failure)
- Observability: per-lecture stage events with `at`; gate wait events; `summary` / `items_status` in the job result
- Review map: single PR; reviewers check gate release on every path, cancel-aware waits, no cross-lecture shared state except the artifacts lock, per-lecture deletion rules, executor sizing, budget arithmetic and cap, 400-event cap behavior (30-lecture test)
- Split decision: STANDALONE (one failure/recovery owner, one rollback)
- Coherent scope/refactor boundary: `artifacts.py` rewrite; additive edits elsewhere; no other refactor
- Risk/reversibility: medium (concurrency) / fully reversible
- Verification evidence: green CI on exact head incl. the new pipeline suite
- Design dependencies: design/02, 04, 05 Flows 1–3, 07
- Required authority subset: GIT_WORKTREE, LOCAL_IMPLEMENT_TEST, COMMIT_PUSH, DRAFT_PR, WORKTREE_CLEANUP
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: READY

### PL-UI-STATES

- Repository/target/base: same, develop
- Outcome: `jobItems.ts` phases `waiting_audio` ("음성 추출 대기 중") and `waiting_transcript` ("자막 대기 중", "(n번째)" when `queue_position`, "자막 서버 응답 기다리는 중 · 먼저 보낸 요청 n개" for `server_busy`, "(서버가 잠시 미룸)" for `server_deferred`), estimated progress shown as "약 n%"; waiting derived from `download_done` / `audio_done` + requested outputs (also against an old backend); `artifact_item_*` stages handled; drawer title "진행 중 d/t"; stage-weighted overall progress (download 40 / audio 20 / transcript 40, normalized to requested outputs); error copy for `stt_remote_job_lost`, `stt_remote_upload_too_large`, `stt_remote_bad_request`; contract tests with recorded old/new event logs.
- Acceptance subset: AC7, AC8 (UI)
- Dependencies / next-unit condition: none to start (event contract frozen by the approved design) / merge only after PL-PY-PIPELINE MERGED; both MERGED → AC10
- Owned paths: `frontend/src/lib/jobItems.ts`, `frontend/src/components/DownloadDrawer.tsx`, `frontend/src/lib/types.ts`, `frontend/src/lib/errors.ts`, `frontend/scripts/contract-test.mjs`
- Shared contracts/resources: design/09 § 진행 이벤트 계약, § 화면 문구 (consumer side)
- Affected boundaries: React UI only
- Deployment compatibility: works against old and new backends (derived waiting states; unknown fields ignored)
- Rollback/containment: revert PR
- Observability: none new
- Review map: single PR; UI reviewer checks the state table vs design/09, copy, aria labels on row progress bars, screenshots light/dark
- Split decision: STANDALONE (separate lane after the event contract)
- Coherent scope/refactor boundary: row state machine + drawer only
- Risk/reversibility: low
- Verification evidence: green CI (typecheck, contract test, tauri build) + reviewed screenshots of each row state against recorded logs
- Design dependencies: design/09 § 화면 문구, § 진행 이벤트 계약, state view
- Required authority subset: as PL-PY-PIPELINE
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: READY

### AC10 owner checkpoint (not a unit)

- Trigger: PL-PY-PIPELINE and PL-UI-STATES MERGED; main task builds the macOS app from develop (no install).
- Owner steps: install when no auto-watch runs; select 3 lectures with MP3+TXT on the current `whisper_cpp_server` remote setting; watch the drawer (one lecture extracting while another downloads; "음성 추출 대기 중" / "자막 서버 응답 기다리는 중 · 먼저 보낸 요청 n개" instead of "받는 중 100%"); all three finish without a timeout; repeat with one lecture forced to fail (e.g. delete its downloaded video while it waits) → the other two finish; start again and press "모두 취소" → everything stops.
- Evidence: owner report + screenshots; job result `summary` / `items_status`.
- Outcome: pass → sprint closes; sprint-2 waits for AC11; fail → fix/revert within this sprint.

### STT-JOB-PROVIDER (deferred, not eligible)

- Deferred to sprint-2. Precondition: AC11 recorded (protocol draft accepted or changed by the modelhost owner + the owner) and PL-PY-PIPELINE MERGED.
