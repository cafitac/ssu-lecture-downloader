# PL-PY-PIPELINE

- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-per-lecture-pipeline/feature.md
- Sprint manifest: ../../sprints/sprint-1.md (sha256 ba36b127…)
- Target/base: develop (origin/develop e2f0714)
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/pl-py-pipeline (feature/pl-py-pipeline)
- Design status: APPROVED (parent 8a74ddbd…)
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL

## Progress and evidence

- 2026-10-30: worktree created.
- 2026-10-30: implemented (local commit, not pushed). `service/pipeline_gates.py` (StageGates: media 2, local_stt 1, FIFO, cancel-aware, release in finally); `service/artifacts.py` rewritten as `ArtifactPipeline` + per-lecture `LecturePipeline` (no barrier, no shared abort, per-lecture `LectureResult`); additive `on_item_ready` in `batch_download_lectures`; `RemoteSTTService.in_flight`/`request_slot`, queue-aware response budget (`1200 s x (1 + ahead)`, cap 4 h, 60 s idle connect/upload), 429/503 backoff + resend in `stt_remote.py`; media gate hook (`WorkGates`) in `stt.py`; `transcript_waiting`/`transcript_queued` coalesced in `jobs.py`; `AppContext.gates`.
- Event contract implemented as design/09 (see the unit report for the exact fields); `result.summary` and `result.items_status` additive.
- Tests: new `tests/test_artifact_pipeline.py` (AC1-AC5, AC8, 30-lecture items_status vs 400-event cap, gates, `on_item_ready`), new AC6 cases in `tests/test_stt_remote.py`; `test_artifact_job_failure_does_not_run_the_remaining_queued_items` replaced by isolation tests in `tests/test_api_robustness.py`. `tests/test_api.py` FakeService only gained the `on_item_ready=None` parameter (outside the owned list, signature-only).
- Verification: `UV_LOCKED=1 uv run pytest -q` 726 passed; `uv run lint-imports` 4 contracts kept. Two mutations (media limit 3; no `on_item_ready`) are caught by the new suite.
