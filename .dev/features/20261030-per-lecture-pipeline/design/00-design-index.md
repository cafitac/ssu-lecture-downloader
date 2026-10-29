# Design index

- Feature record: ../feature.md
- Feature ID: 20261030-per-lecture-pipeline
- Current slice: P1 (per-lecture pipeline in the app, interim remote path); P2 (job-style remote STT provider) designed against a protocol draft and gated on the cross-project decision
- Current PR unit: NONE (proposed PL-PY-PIPELINE, PL-UI-STATES; then STT-JOB-PROVIDER)
- Design profile: STANDARD
- Design status: AWAITING_APPROVAL
- Target/base: develop (planning base e2f0714)

## Source evidence

Code survey 2026-10-30, develop @ e2f0714, read-only:

- `src/ssu_lecture_downloader/service/artifacts.py`
  - `run()` (l.102–139): dry-run plan → `self._batch(dry_run=False, …)` downloads **all** needed videos and returns → only then `_process_all(...)`. Confirmed batch barrier.
  - l.117–119: if the batch download result is not ok (`partial_failure` when any one download failed), the whole job returns `JobOutcome.failed` before any post-processing — one failed download cancels post-processing of every other lecture.
  - `MAX_ARTIFACT_POSTPROCESS_WORKERS = 1` (l.24): post-processing (mp4 check, audio, transcript) of all lectures runs one at a time.
  - `_guarded_item` (l.170–181): first item error sets `_abort`; `should_cancel()` includes `_abort`, so a running sibling is stopped and `executor.shutdown(cancel_futures=True)` drops the queued ones. The job then fails with the first error code.
  - `_process_item` (l.185–216): per-lecture order mp4 → audio → transcript → delete intermediate audio (unless mp3 wanted) → delete source video (when mp4 not wanted and the video was written by this job). This per-lecture order is kept as is.
- `src/ssu_lecture_downloader/core/lms_service.py`
  - `batch_download_lectures` (l.1269–1381): login + module fetch, parallel resolve (`resolve_workers`), then `_run_batch_downloads` with `ThreadPoolExecutor(max_parallel_downloads)`; per-download failures already become item results (`stage: failed`), results are returned only after all downloads finish.
  - `_run_batch_downloads.download_planned` (l.1427–1465) emits `downloading` / `done`; `core/download.py` emits `download_started/progress/done`.
- `src/ssu_lecture_downloader/core/jobs.py`: `Job.progress` compacted (`COALESCED_PROGRESS_STAGES` = download/audio/transcript progress + `transcript_repair`), `MAX_JOB_PROGRESS_EVENTS = 400`, cancel = `cancel_requested` flag polled by work; `JobRunner` maps outcome/exception to terminal status.
- `src/ssu_lecture_downloader/core/stt_remote.py`
  - `http_request` (l.91–149): `http.client` connection with `timeout=REQUEST_TIMEOUT_SECONDS` (20 min). This timeout applies to each blocking socket operation, so while whisper-server holds a request (queued behind another or transcribing) and sends nothing, the read fails after 20 idle minutes. A second concurrent request to a one-at-a-time server therefore fails if the first one takes > 20 min minus its own time.
  - `transcribe_with_remote` (l.423–502): `transcript_connecting` (2 %) → WAV 16 kHz conversion (local ffmpeg) → `transcript_started` (5 %) → one POST of the whole WAV → `transcript_progress` 70 % → `repair_repeated_cues` re-sends cut windows (one POST per window, temperatures 0.2/0.4) → 99 %.
- `src/ssu_lecture_downloader/core/stt.py`: `transcribe_audio` prefers remote when enabled; local faster-whisper model cache `_WHISPER_MODELS_LOCK` guards loading only (no concurrency limit on transcription itself).
- `src/ssu_lecture_downloader/core/stt_remote_config.py`: `RemoteKind = Literal['whisper_cpp_server', 'openai_compatible']`; `RemoteSTTSettingsStore.load()` returns defaults on any validation error (an unknown kind makes an older build see "remote off").
- `frontend/src/lib/jobItems.ts`: `download_done` sets phase `downloading` with 100 % and nothing changes until `extracting_audio` arrives → with the barrier and 1 worker, a finished lecture shows "받는 중 · 100%" for a long time (the reported symptom). `stage.includes('failed')` → failed row; unknown stages fall through without effect.
- `frontend/src/components/DownloadDrawer.tsx`: title `받는 중 {done}/{total}`, per-row `label · note · bytes · percent`, "모두 취소".
- `tests/test_api_robustness.py:404` `test_artifact_job_failure_does_not_run_the_remaining_queued_items` pins today's abort behavior (to be replaced).
- Git: commit 3b22e0d (2026-10-15 audit) introduced `MAX_ARTIFACT_POSTPROCESS_WORKERS = 1` inside a broad hardening commit with no stated reason; the likely intent (memory/CPU for local large-v3) is kept by the local STT gate = 1.
- Mac Studio facts (from the owner/coordinator, not re-checked): whisper-server `-m ggml-large-v3 -l ko --port 8795`, threads 4, processors 1, one request at a time; 128 GB RAM, 16 cores. Shared model server candidate: interview-coach `modelhost` (127.0.0.1:8800); constraints recorded in `../feature.md` § External constraints.

## Artifact ledger

| Artifact | Status | Evidence / reason |
|---|---|---|
| Context map | N/A | No bounded-context ownership change inside the app; the one external boundary (remote STT provider) is drawn in the review-pack target overview and specified in `remote-stt-job-protocol.md`. |
| Domain model | REQUIRED | `02-domain-model.puml`: ArtifactPipeline, LecturePipeline, StageGates, providers, invariants. |
| Current schema | N/A | No database or new app file. Existing `stt.json` shape is in the code (`stt_remote_config.py`); only an allowed enum value is added. |
| Target schema | REQUIRED | `04-target-schema.dbml`: in-memory pipeline records, progress-event shapes, `stt.json` kind enum, remote job handle. |
| Transaction flows | REQUIRED | `05-transaction-flows.puml`: per-lecture chain, failure isolation, cancel-all, interim remote request budget, job-style provider. |
| State machines | N/A | No persisted lifecycle. The in-memory per-lecture lifecycle is drawn as a state view in `09-design-review.md` and owned by `LecturePipeline`; remote job states are owned by the server (protocol draft). |
| Consistency and migration | REQUIRED | `07-consistency-migration.md`: limits, cancellation, isolation, timeouts, compatibility, failure/cleanup owner map, external dependency questions. |
| Acceptance trace | REQUIRED | `08-acceptance-trace.md`. |
| Visual review pack | REQUIRED | `09-design-review.md` (Korean, target overview first). |

Additional artifact (in the digest): `remote-stt-job-protocol.md` — app-side protocol draft for the shared model server decision (EXT-MODELHOST-STT). It is an input for another project, not an external API this app serves.

## Confirmed decisions

- D1 per-lecture linear pipeline; lectures independent and concurrent (owner 2026-10-30).
- D2 app limits only local resources; remote STT queueing belongs to the server (owner 2026-10-30).
- D3 no standalone Mac Studio queue service; one shared model server later (candidate modelhost, not changed here) (owner 2026-10-30).
- D4 failure isolation, cancel-all, per-lecture UI states (brief 2026-10-30).

## Proposed decisions

- P1 Pipeline shape: `ArtifactPipeline.run()` keeps the dry-run plan, then (a) submits a `LecturePipeline` immediately for every planned lecture that needs no video download, and (b) calls `batch_download_lectures(..., on_item_ready=submit)` so each lecture is submitted from the download worker the moment its own download (or skip) finishes. Lecture chains run on a per-job `ThreadPoolExecutor` sized to the planned lecture count (threads mostly block on gates, subprocesses or HTTP); no `_abort`, no barrier.
- P2 `StageGates` (new `service/pipeline_gates.py`), one instance in `AppContext`, process-wide (shared by concurrent artifact jobs): `media` = 2 (ffmpeg audio extraction, MP4 reconstruction, STT WAV conversion, repair window cuts), `local_stt` = 1 (faster-whisper). Constants in that module (`MEDIA_CONCURRENCY = 2`, `LOCAL_STT_CONCURRENCY = 1`); no settings UI (R9). Acquire is cancel-aware (`acquire(timeout=0.25)` loop checking `should_cancel`), release in `finally`. A lecture emits `audio_waiting` / `transcript_waiting` only when it actually has to wait.
- P3 Remote STT is not gated by the app. The WAV conversion inside the remote path takes the `media` gate (local CPU), the HTTP part does not.
- P4 Download hand-off: `batch_download_lectures` gains an optional `on_item_ready: Callable[[BatchDownloadItemResult], None]`, called once per finished item (downloaded, skipped, or failed) from the worker thread after its `done`/`skipped`/`failed` event; the existing return value is unchanged. A failed download is handed off as a failed item (its LecturePipeline records the failure and does nothing else).
- P5 Failure isolation: `LecturePipeline.run()` returns `LectureResult(ok, error_code, stage)`; it never touches other lectures. After all lectures are terminal, the job outcome is: all ok → `succeeded`; cancelled → `cancelled` (runner rule unchanged); else `failed` with `error_code = partial_failure` if any lecture succeeded, the common code if all failed with one code, `partial_failure` otherwise. `result` keeps its keys and adds `summary: {total, succeeded, failed, cancelled}` (additive).
- P6 Cancellation: unchanged single source `job.should_cancel()`; every stage boundary, gate wait and provider wait checks it; downloads keep their current cancel. The job provider DELETEs its open job on cancel. Lectures not yet started end as cancelled without events beyond `artifact_item_cancelled`.
- P7 Interim remote timeout (legacy kinds): the upload keeps a short idle timeout; the response wait uses a queue-aware budget `REQUEST_TIMEOUT_SECONDS × (1 + ahead)`, `ahead` = this process's remote requests already in flight when the request was sent (counter in `RemoteSTTService`), capped at 4 h. Implemented by reading the response on the worker thread with the socket timeout set to the budget. Trade-off in design/07.
- P8 Job-style provider (kind `stt_job`, P2): submit → poll → result per `remote-stt-job-protocol.md`; queue time unbounded (only cancel and server health end it), running time bounded; 429 + Retry-After = wait and resubmit with the same Idempotency-Key; no client chunking; client-side loop repair only when the server's capabilities say it does not repair.
- P9 Progress events (additive): `artifact_item_queued`, `audio_waiting`, `transcript_waiting` (`reason: local_engine | server_queue | server_busy`, optional `queue_position`, `ahead_requests`), `transcript_queued` (job kind), `transcript_progress` gains optional `progress_estimated`, `artifact_item_done`, `artifact_item_failed` (`error_code`, `failed_stage`), `artifact_item_cancelled`, plus `at` (epoch seconds) on every pipeline event. Contract in `09-design-review.md` § 진행 이벤트 계약.
- P10 UI: new row phases `waiting_audio` ("음성 추출 대기 중"), `waiting_transcript` ("자막 대기 중", "(n번째)" when `queue_position` exists, "자막 서버 응답 기다리는 중 · 먼저 보낸 요청 n개" for legacy); after `download_done` with mp3/txt pending the row derives `waiting_audio`, after `audio_done` with txt pending `waiting_transcript`, so the "받는 중 100%" state disappears even against an old backend. Drawer title "진행 중 d/t". Overall bar: per-row stage weights (download 40, audio 20, transcript 40 when txt; normalized to requested outputs).
- P11 Protocol ownership: the job protocol is a draft owned jointly with the shared model server; this feature implements only the client; the server side is an external dependency (EXT-MODELHOST-STT).

## Open questions

- Q1 (owner) legacy kind unthrottled with the budget (recommended) vs one-at-a-time for that kind until the shared server exists.
- Q2 (owner) drawer title "진행 중 d/t" (recommended).
- Q3 (owner + modelhost session) protocol draft acceptance; loop repair server-side (recommended) vs client window jobs.
- O1 whisper-server behavior under concurrent `/inference` (serialize vs reject) — fake in tests; real check in AC10. If it rejects (HTTP 503/429), the legacy provider retries after a short backoff instead of failing, which degrades into one-at-a-time behavior.
- O2 Upload bandwidth through the SSH tunnel when several WAVs (≈ 110 MB per 60 min at 16 kHz mono s16) upload at once — acceptable for 2–6 lectures; observed in AC10.

## Review findings

Self-checklist review pass (STANDARD; no independent agent review was run for this draft — disclosed limitation):

- F1 (accepted, fixed in P4/P5): removing `_abort` alone is not enough — `run()` also fails the whole job when the batch download is `partial_failure` (artifacts.py l.117–119). Download failures now flow per lecture through `on_item_ready`.
- F2 (accepted, fixed in P2): gates must be process-wide, not per job; two artifact jobs (two courses) would otherwise run 4 ffmpeg and 2 local large-v3 models.
- F3 (accepted, fixed in P3): `convert_to_stt_wav` in the remote path is local ffmpeg work and would bypass the media limit if only `extract_audio` were gated.
- F4 (accepted, fixed in P7): `http.client` timeout is an idle timeout per socket operation; "raise the timeout" must be expressed as a response-wait budget, not as the connect/upload timeout, or a dead tunnel at connect time would hang for hours.
- F5 (accepted): `Job.progress` is capped at 400 events; with more lectures and more stages the compaction could evict early per-lecture terminal events. `artifact_item_done/failed` are emitted last per lecture and the UI also uses `result.artifacts`/`summary` after the job ends; additionally `artifact_item_*` terminal events are added to a small protected list kept by the pipeline and re-emitted into the result (`result.items_status`). Test with 30 lectures.
- F6 (accepted): source-video deletion (`source_video_removed`) happens per lecture after its own chain — unchanged rule, but now concurrent with other lectures' downloads; paths are per lecture so no cross-lecture deletion is possible. Test asserts another lecture's files are untouched when one fails.
- F7 (rejected): a durable operation table for remote jobs — app restarts drop in-flight work today too, the server cleans up by TTL, and the user retries with "다시 시도"; bounded retry + Idempotency-Key suffice.
- F8 (accepted): legacy budget can let a genuinely hung server occupy a lecture for up to the cap; cancel stays immediate and the failure code is `stt_remote_timeout` with the elapsed time in the message.

## Validation evidence

- 2026-10-30 `python3 <agent-skills>/skills/feature-design/scripts/validate_design.py --record ../feature.md` → result and artifact-set digest recorded in `../feature.md` § Progress and evidence.
- PlantUML and DBML renderers are not installed on this Mac (`plantuml`, `dbml2sql` not found in the sibling records' checks); `.puml`/`.dbml` are not rendered or parsed — named evidence limitation, not a semantic pass. The Mermaid review pack is the rendered view.
