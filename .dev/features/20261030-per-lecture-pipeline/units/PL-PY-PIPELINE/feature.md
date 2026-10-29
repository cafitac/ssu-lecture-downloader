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
- 2026-10-30 (repair batch after review): event-table note for the UI lane. (1) `audio_waiting` is only for waits before audio extraction / MP4 reconstruction and now carries `step` = `"audio"` | `"mp4"`; media waits inside the transcript stage (remote WAV conversion, repair cuts) are `transcript_waiting` with new `reason: "media"` (no `ahead_requests`). (2) `transcript_waiting`, `transcript_queued` and `audio_waiting` are coalesced per lecture and the newest event moves to the end of the list (`at` stays monotonic). (3) 400-event cap: plain events are evicted first; milestones (`artifact_item_*`, `done`, `*_done`, `*_waiting`, `transcript_queued`) only when nothing else is left. (4) A lecture that ended with a real error code stays `failed` even if the job cancel flag was set afterwards. (5) Gate names `MEDIA`/`LOCAL_STT` live in `core.stt` next to `WorkGates`.
- 2026-10-30 (review cycle 1 repair): every coalesced stage now deletes its old entry and appends the new one at the end (jobs-level test for the remote repair sequence); the new tests wait on a rig-owned condition notified on progress append / worker enter-leave / request seen, and `finish()` joins the runner thread (no polling sleeps; fakes simulate latency with `Event.wait`); `time.sleep` lines under tests/ 35 (base 36); crash log of a lecture chain names `module_item_id` and stage. Design amendment recorded in the parent feature record (`## Design amendment (2026-10-30, from review)`), which supersedes 09 § 진행 이벤트 계약 and 04 `waiting_reason`/`progress_event` on the points listed there.
