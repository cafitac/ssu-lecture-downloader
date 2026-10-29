# PL-UI-STATES

- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-per-lecture-pipeline/feature.md
- Sprint manifest: ../../sprints/sprint-1.md (sha256 ba36b127…)
- Target/base: develop (origin/develop e2f0714)
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/pl-ui-states (feature/pl-ui-states)
- Design status: APPROVED (parent 8a74ddbd…)
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL

## Progress and evidence

- 2026-10-30: worktree created.
- 2026-10-30: implemented row phases `waiting_audio` / `waiting_transcript` (derived from `download_done` / `audio_done` against an old backend, explicit `audio_waiting` / `transcript_waiting` / `transcript_queued` against the new one), `artifact_item_*` final states (locked against late events), `items_status` / `summary` reading, stage-weighted overall bar (40/20/40 normalised to requested outputs), "약 n%" for `progress_estimated`, drawer title "진행 중 d/t", polite hidden status line (no alert), three new error copies. `npm run typecheck` and `npm run test:contract` green; screenshots of every row state (light/dark, old and new logs, finished with retry) taken from a dev-only harness kept outside the commit (`.local/`, gitignored).
- 2026-10-30 (repair batch after review against PL-PY-PIPELINE 3910611): a row's phase never regresses (late `audio_waiting` ignored once audio or subtitles started); `transcript_waiting` reason `media` reads "자막 준비 대기 중"; `audio_waiting` step `mp4` reads "영상 확인 대기 중" (missing step = audio); each row keeps its furthest phase/overall per job id + row id across polls so event-cap eviction cannot send it back to "대기"/0% (final states still win); the screen-reader key is `phase|label`, plus `note` only for `waiting_transcript`. `npm run typecheck` and `npm run test:contract` green.
- 2026-10-30 (review cycle 1 repair): each lecture's events are stable-sorted by numeric `at` before reducing (untimed events keep their slot; contract case uses the recorded backend log from pl-py-pipeline f367be8); `JobRow.final` marks true finals (`artifact_item_*`, `items_status`, job end, done/cancelled) so a recoverable `failed` of a running job is not pinned by the high-water hold; `verifying` and the wait before it share a rank; a non-final row is capped at overall 99. `npm run typecheck` and `npm run test:contract` green.
- 2026-10-30 (review cycle 2 repair, backend 35a3352): the bare `failed` stage is a final result (locked), `download_failed` and other `*_failed` stay retryable; `JobRow.derivedWait` marks a wait guessed from `download_done` / `audio_done` and the high-water hold keeps a row that got further instead of it; contract tests now replay real logs captured from the 35a3352 test rig (`frontend/scripts/fixtures/`, with the capture script), the f367be8 out-of-order shape stays as a labelled synthetic case. `npm run typecheck` and `npm run test:contract` green.

## Touched paths

Sprint-owned (sprint-1.md): `frontend/src/lib/jobItems.ts`, `frontend/src/components/DownloadDrawer.tsx`, `frontend/src/lib/types.ts`, `frontend/src/lib/errors.ts`, `frontend/scripts/contract-test.mjs`.

Also touched, outside the sprint-owned list: `frontend/src/hooks/useDownloads.ts` (keeps the furthest row per job id + row id between polls via `holdFurthestRows`).

Test fixtures (new): `frontend/scripts/fixtures/backend-3b1a6e7-remote-run.json`, `frontend/scripts/fixtures/backend-3b1a6e7-over-cap.json`, `frontend/scripts/fixtures/capture_backend_progress.py`.
- 2026-10-30: fixtures re-captured from backend 3b1a6e7 (cap keeps stage-entry events); the over-cap run now shows the two slot-holding lectures as "음성 추출 중".
