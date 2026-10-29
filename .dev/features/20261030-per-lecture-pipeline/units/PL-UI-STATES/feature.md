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
