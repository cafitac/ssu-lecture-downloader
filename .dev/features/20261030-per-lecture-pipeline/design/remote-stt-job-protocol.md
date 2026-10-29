# Remote STT job protocol — app-side draft (for the shared model server)

- Status: DRAFT for cross-project review (owner + interview-coach `modelhost` session). Not a commitment by the server project.
- Consumer: SSU Lecture Downloader sidecar, remote STT kind `stt_job` (feature 20261030-per-lecture-pipeline, unit STT-JOB-PROVIDER).
- Transport: HTTP/1.1 JSON over the owner's SSH tunnel to a localhost-only server (e.g. `127.0.0.1:8800` on the Mac Studio). No public exposure.
- Scope: what the app needs. Server internals (chunking, GPU policy, fair queue, instance count) are the server's decision.

## Principles

1. No long-held HTTP request: submit returns immediately; the client polls.
2. The server owns ordering, chunking of long audio, and (recommended) loop repair. The client uploads the whole lecture audio once and never chunks.
3. Waiting is not failure: queued time has no client deadline; `429` with `Retry-After` is backoff.
4. Idempotent submit: a resubmit with the same `Idempotency-Key` returns the existing job.
5. Additive evolution: unknown response fields are ignored by the client; the server ignores unknown request fields.

## Authentication and headers

| Header | Request | Meaning |
|---|---|---|
| `Authorization: Bearer <token>` | all | per-caller token issued by the owner (host file). The app stores it in its existing write-only `api_key` setting. |
| `X-Priority: BACKGROUND` | submit | the app always sends BACKGROUND (lecture transcripts are batch work). |
| `Idempotency-Key: <uuid4>` | submit | one per transcript attempt; reused on resubmit after 429 or a network error. |
| `X-Client: ssu-lecture-downloader/<version>` | all | diagnostics only. |

## Endpoints

### `GET /v1/stt/capabilities` (or a section of the server health document)

```json
{
  "protocol": "stt-job/1",
  "accepted_formats": ["audio/wav", "audio/mpeg"],
  "max_upload_bytes": 524288000,
  "languages": ["ko"],
  "chunks_server_side": true,
  "repairs_loops": true,
  "result_formats": ["srt", "verbose_json"],
  "progress": "measured"
}
```

- `progress`: `measured` (e.g. chunks done / total), `estimated`, or `none`.
- If `repairs_loops` is false the client runs its CURRENT loop repair by submitting short window jobs (same protocol).

### `POST /v1/stt/jobs` — submit

Request: `multipart/form-data`

| Part | Required | Value |
|---|---|---|
| `file` | yes | the lecture audio (16 kHz mono WAV today; MP3 if accepted) |
| `language` | yes | `ko` |
| `temperature` | no | default `0.0` (repair window jobs use 0.2 / 0.4) |
| `response_format` | no | `srt` (default) |
| `model` | no | server default when absent |
| `client_ref` | no | opaque string, e.g. lecture module item id; never interpreted |
| `deadline_at` | no | ISO-8601; the app does not send it by default |

Responses:

| Status | Body | Client behavior |
|---|---|---|
| `202 Accepted` | `{"job_id": "…", "state": "queued", "queue_position": 3, "created_at": "…"}` | start polling |
| `200 OK` | same body, existing job for this `Idempotency-Key` | continue with that job |
| `429` | `{"error": {"code": "BACKGROUND_DEFERRED"}}` + `Retry-After: <seconds>` | show "자막 대기 중", wait, resubmit with the same key |
| `429` | `{"error": {"code": "QUOTA_EXCEEDED"}}` + `Retry-After` | same as above |
| `400` | `{"error": {"code": "INVALID_AUDIO" \| "UNSUPPORTED_FORMAT" \| "UNSUPPORTED_LANGUAGE"}}` | fail the lecture (`stt_remote_bad_request`) |
| `401` / `403` | `{"error": {"code": "UNAUTHORIZED" \| "FORBIDDEN"}}` | fail (`stt_remote_auth_failed`, CURRENT code) |
| `413` | `{"error": {"code": "UPLOAD_TOO_LARGE"}}` | fail (`stt_remote_upload_too_large`) |
| `503` | `{"error": {"code": "UNAVAILABLE"}}` + optional `Retry-After` | backoff and resubmit (same key), fail after 2 min of continuous 503 |

### `GET /v1/stt/jobs/{job_id}` — status

```json
{
  "job_id": "…",
  "state": "queued | running | succeeded | failed | cancelled",
  "queue_position": 2,
  "started_at": "…",
  "finished_at": null,
  "progress_percent": 40,
  "progress_estimated": false,
  "chunks_done": 4,
  "chunks_total": 10,
  "audio_duration_seconds": 4321.5,
  "warnings": [],
  "error": null
}
```

- `queue_position`: present only in `queued`; 1 = next to run. Whether global or per caller is a server decision (the app only displays it).
- `progress_percent`: present only in `running` when known; `progress_estimated: true` when it is an estimate.
- `error` in `failed`: `{"code": "…", "message": "…"}`; codes the app maps: `TRANSCRIPTION_FAILED`, `INVALID_AUDIO`, `TIMEOUT`, `INTERNAL`.
- `warnings`: strings shown with the transcript (same role as the CURRENT repair/gap warnings).
- `404`: unknown or expired job → `stt_remote_job_lost` (lecture fails; the user retries).

Polling: every 2 s while `queued` or `running`; on network errors back off 2 → 4 → 8 → 15 s, give up after 2 minutes without any successful poll.

### `GET /v1/stt/jobs/{job_id}/result?format=srt`

- `200` with `text/plain` SRT (or `application/json` verbose_json when asked).
- `409` when the job is not `succeeded`.
- The server may delete the result after a successful fetch or after its TTL.

### `DELETE /v1/stt/jobs/{job_id}` — cancel / discard

- Queued: removed. Running: cancelled at the next chunk boundary or result discarded (server decision). Terminal: result deleted.
- `204` or `404` are both success for the client.
- The app calls it on user cancel, on budget timeout, and after it stops polling for any reason.

## Client state mapping (app side)

| Server state | App progress event | UI row |
|---|---|---|
| submit 429 | `transcript_waiting {reason: server_deferred, retry_after_seconds}` | 자막 대기 중 (서버가 잠시 미룸) |
| `queued` | `transcript_queued {queue_position}` | 자막 대기 중 (n번째) |
| `running` | `transcript_progress {progress_percent, progress_estimated}` | 자막 만드는 중 (원격) · n% / 약 n% |
| `succeeded` | result fetch → `transcript_done` | 완료 (when the lecture's chain ends) |
| `failed` | `artifact_item_failed {error_code, failed_stage: transcript}` | 실패 + reason |
| `cancelled` (not by the app) | `artifact_item_failed {error_code: stt_remote_cancelled}` | 실패 |

## Client timeouts

- Queued: no deadline.
- Running: `max(1200 s, audio_duration)` from `started_at`, then DELETE + `stt_remote_timeout`.
- Single HTTP calls: connect/upload idle 60 s; status/result/DELETE 15 s.

## Open points for the server project

1. Path prefix (`/v1/stt/...` vs a generic `/v1/jobs?task=stt`).
2. Server-side loop repair (recommended) vs client window jobs.
3. Global vs per-caller `queue_position`; measured vs estimated progress.
4. Upload size/format limits; MP3 acceptance.
5. Result retention TTL and delete-on-fetch.
6. Capabilities endpoint vs a field in the existing health document.
7. Whether the existing whisper-server (8795) is wrapped first (recommended by the modelhost owner) — no client impact either way.
