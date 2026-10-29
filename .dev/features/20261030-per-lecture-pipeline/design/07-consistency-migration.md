# Consistency, concurrency and migration

## Boundaries

- No database and no transaction. The unit of consistency is one lecture's output set, which keeps the CURRENT atomic rules: audio and transcript files are written to a temporary name and swapped in; the `.txt` is committed last and marks a complete transcript set; a lecture's chain reuses complete outputs on the next run.
- One `ArtifactPipeline` per artifact job owns its `LecturePipeline`s. A `LecturePipeline` writes only its own state record and its own files (paths derive from its own video path). The shared `artifacts` list keeps its CURRENT lock.
- `StageGates` is the only cross-job shared state added. It lives in `AppContext` (one per sidecar process).

## Concurrency model

| Work | Limit | Scope | Where configured | Reason |
|---|---|---|---|---|
| Video downloads | `max_parallel_downloads` (request, default 4) | per job | CURRENT request field | unchanged |
| Lecture chains | planned lecture count threads | per job | `ArtifactPipeline` | threads block on gates/subprocess/HTTP; cheap |
| Media CPU (ffmpeg: extract audio, MP4 reconstruction, STT WAV conversion, repair cuts) | 2 | process | `service/pipeline_gates.py` `MEDIA_CONCURRENCY = 2` | MacBook CPU; owner asked 2–3, 2 leaves headroom for downloads + UI |
| Local STT (faster-whisper) | 1 | process | `LOCAL_STT_CONCURRENCY = 1` | large-v3 memory; keeps the 2026-10-15 intent of worker = 1 |
| Remote STT | none in the app | — | — | owner D2: the server owns queueing |

- Acquire order inside a lecture is strictly sequential (one gate at a time, released before the next step), so no lock-order deadlock is possible. The remote path takes `media` only around WAV conversion / window cuts and releases it before the HTTP call.
- Gate waits poll `should_cancel` every 0.25 s; a `audio_waiting` / `transcript_waiting` event is emitted once when the first acquire attempt fails.
- The executor per job is shut down only after every submitted chain returned; `on_item_ready` submissions after a cancel are accepted and end immediately as cancelled (no work).
- Thread-safety of `on_item_ready`: called from download worker threads; `ArtifactPipeline.submit` only does `executor.submit` + a dict insert under a lock.

## Failure isolation

- A lecture fails alone: download failure (handed off as a failed item), mp4 check failure, audio failure, transcript failure, cleanup error (logged, lecture still `done` if outputs exist — CURRENT behavior is "unlink missing_ok").
- Shared prerequisites still fail the job: login failure, plan failure, invalid request. No lecture is submitted in those cases.
- An unexpected exception inside a chain is caught at the chain boundary and becomes `LectureResult(failed, error_code=<ExceptionName>)`; it never propagates to the executor (which would otherwise be silently swallowed or abort `as_completed`).
- Job outcome: all done → `succeeded`; `job.should_cancel()` → `cancelled` (CURRENT runner rule); otherwise `failed` with `partial_failure` if ≥ 1 lecture done, the common code if all failed with the same code, else `partial_failure`. `result.summary` and `result.items_status` are additive.

## Timeouts

### Interim kinds (`whisper_cpp_server`, `openai_compatible`)

- Today `http.client` uses `timeout=1200 s` as an idle timeout for every socket operation, including the wait for the response. A request queued behind another one at a one-at-a-time whisper-server receives no byte until it is done, so with two lectures of 15 minutes compute each the second request fails at minute 20.
- Proposed: connect + upload keep a short idle timeout (60 s per socket operation); after the upload the socket timeout is set to the response budget `1200 s × (1 + ahead)` (cap 14 400 s), where `ahead` = number of remote requests of this process already in flight when this one was sent (`RemoteSTTService.in_flight`, incremented/decremented in `try/finally`). Repair windows count as requests.
- Trade-off: queue time no longer fails requests as long as the server's queue consists of this app's own requests (the realistic case today). A genuinely hung server is detected later (up to the budget, max 4 h); cancel stays immediate. Requests from other clients of the same server are not visible to the app and can still exhaust the budget — the job kind removes this limitation.
- If the server rejects concurrent requests (HTTP 429/503) instead of serialising them (O1), the provider waits 5–30 s (backoff) and resends; this degrades to one-at-a-time without failing.

### Job kind (`stt_job`)

- Queue time has no client deadline; it ends only by cancel, by server-reported terminal state, or by the server becoming unreachable (poll errors for > 2 min → `stt_remote_unreachable`, then DELETE best effort).
- Running time budget: `max(1200 s, 1.0 × audio_duration)` from the server-reported `started_at`; exceeded → `stt_remote_timeout` + DELETE.
- `429 BACKGROUND_DEFERRED` with `Retry-After` on submit: wait (cancel-aware) and resubmit with the same `Idempotency-Key`; never a failure. Missing `Retry-After` → 30 s.
- Optional `deadline_at` is not sent by default (protocol allows it).

## Identity and idempotency

- Legacy kinds: no idempotency (CURRENT); a retried request may run twice on the server — acceptable (read-only computation, outputs written once by the client atomically).
- Job kind: `Idempotency-Key` = UUID4 generated per transcript attempt (and per repair window if client repair is used). The same key is reused on 429 resubmits and on network errors during submit (result unknown → resubmit with the same key returns the existing job). A user "다시 시도" starts a new attempt with a new key.
- No durable operation record: an app restart loses the job handle; the server's TTL cleans abandoned jobs; the user retries. This matches CURRENT behavior for every job type (F7).

## Progress, memory and payload compatibility

- Progress events: additive stages and keys only (contract in `09-design-review.md`). `transcript_waiting` and `transcript_queued` are added to `COALESCED_PROGRESS_STAGES` so position updates replace each other per lecture.
- 400-event cap: per-lecture terminal state is also stored in `result.items_status` so a long job's UI can still classify every row after compaction (F5).
- Old UI + new backend: `applyEvent` ignores unknown stages; `artifact_item_failed` contains "failed" → failed row (correct); `artifact_item_done` falls through. The job's `status` and `error_code` semantics are unchanged (`partial_failure` already exists in the UI copy).
- New UI + old backend: waiting states are derived from `download_done` / `audio_done` and requested outputs, so rows improve even without the new stages; position notes simply never appear.
- `stt.json`: new allowed `kind` value only with STT-JOB-PROVIDER. Older builds reading `stt_job` hit `ConfigError`/validation → `load()` returns defaults (remote off) and the file is not rewritten until saved. Downgrade note for the owner: switch kind back before installing an older build.

## Failure and cleanup owner map

| Effect / failure | Owner | Recovery / cleanup | Evidence |
|---|---|---|---|
| Download failure of one lecture | LmsService worker → `on_item_ready(failed item)` → that LecturePipeline | item result failed; partial file handling CURRENT (download.py) | AC4 |
| Audio / mp4 failure | LecturePipeline | `artifact_item_failed`; partial outputs CURRENT atomic rules | AC4 |
| Transcript failure (local) | LecturePipeline under `local_stt` | as above | AC4 |
| Transcript failure (legacy remote) | WhisperCppServerProvider / OpenAICompatibleProvider | budget timeout, rejection backoff, `record_failure` (CURRENT) | AC6 |
| Transcript failure (job remote) | JobQueueProvider | 429 backoff, poll backoff, running budget, DELETE | AC9 |
| Gate leak | StageGates | release in `finally`; test asserts `in_use == 0` after every suite | AC3, AC5 |
| Intermediate audio | LecturePipeline (own chain only, after success) | CURRENT rule | AC4 (others untouched) |
| Source video (mp4 not requested, written by this job) | LecturePipeline (own chain only, after success) | CURRENT rule | AC4 |
| Remote job left on server after cancel/crash | JobQueueProvider DELETE (best effort); server TTL | — | AC5, AC9 |
| Job terminal status | JobRunner (CURRENT) | — | AC4, AC5 |

## Migration, rollout and rollback

- No data migration. P1 is active after the owner installs a build containing PL-PY-PIPELINE (+ PL-UI-STATES). Rollback: revert the PRs / reinstall the previous build.
- P2 is inert until the owner selects kind `stt_job` in Settings after the shared server exposes the agreed protocol. Rollback: select `whisper_cpp_server` again (remote port 8795).

## Privacy and security

- No new data leaves the machine beyond what the remote kinds already send (the lecture audio). The job kind adds a bearer token (the existing write-only `api_key` field), never logged, only sent over the SSH-tunnelled localhost URL. Job ids and positions appear in progress events; transcript text does not.
- Server JSON is untrusted: the job provider validates types and bounds (`queue_position` 0..10 000, `progress_percent` 0..100, string lengths) before emitting events.

## External dependency: shared model server STT (EXT-MODELHOST-STT)

Not part of this feature's delivery. Owner + interview-coach (modelhost) session decide. This feature supplies `remote-stt-job-protocol.md`. Questions the shared server work must answer before STT-JOB-PROVIDER starts:

1. Does modelhost adopt the draft endpoints/states as is, or which changes (path prefix, field names, result format)?
2. Is loop repair (re-transcribing looped windows) done server-side as part of chunking? (recommended yes; otherwise the client submits window jobs and needs an ordering rule for them.)
3. Caller identity: token per caller from the host file — how the owner issues the app's token, and which quota/fair-share the app gets for BACKGROUND STT.
4. Is `queue_position` global or per caller, and is `progress_percent` measured (chunks done / total) or estimated?
5. Upload limits and accepted formats (16 kHz mono WAV ≈ 110 MB/h; MP3 accepted to save tunnel bandwidth?).
6. Result retention on the SSD (TTL after completion, delete on result fetch or explicit DELETE).
7. Throughput plan: how many whisper instances / threads the shared server runs, measured with real lectures (the measurement belongs to that project, not to this app).
8. Health contract the app uses for "연결 확인" (probe) and whether a `capabilities` document is exposed.
9. Change freeze: 글로벌도시이해 asks for no changes 11/9–11/21 on the Mac Studio; the STT integration rollout must avoid that window.
