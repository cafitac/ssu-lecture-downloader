# Architecture

## Service layer

The CLI commands call `ssu_lecture_downloader.core.lms_service.SsuLectureService`, which owns reusable login/course-discovery/course-detail/media-resolution/download orchestration. The local FastAPI app and React/Vite prototype sit on top of that service layer; future desktop UI should reuse the same boundary instead of duplicating CLI internals. API/UI credential access goes through a writable in-memory runtime store: login values are accepted only through `POST /api/credentials`, ignored from `.env`, not stored in Keychain by default, and cleared by `DELETE /api/credentials` or backend process exit. API/UI responses expose only presence/source booleans, never credential values. In the API process, the service keeps in-memory authenticated session and TTL list-result caches per credential fingerprint so repeated term/course/detail lookups avoid full login and duplicate term scans; these caches are never written to disk and are cleared on credential save/delete.

## Download engine boundary

The current byte writer is Python (`download_to_plan`) and uses the same authenticated `HttpClient` session that resolved the Commons URL. It now streams with `curl-cffi` `stream=True`, writes to `.part`, atomically renames on success, emits byte-progress events, retries interrupted streams, and uses `Range` to resume partial temp files when Commons returns `206 Partial Content`. Tauri uses Rust native transfer jobs for the desktop primary download path after Python returns a native plan. Python remains responsible for LMS login, course/media resolution, credentials, and trace redaction; Rust is responsible only for final byte streaming in the app path.

Recommended Rust module contract:

- input: already-resolved direct download URL, output path, optional `Referer`, timeout, and redacted display metadata;
- output: final file path, bytes written, elapsed time, and progress events;
- responsibilities: streaming write, temp-file + atomic rename, retry/resume/range support if supported by Commons, cancellation safety;
- non-responsibilities: LMS login, cookies, course discovery, Commons metadata resolution, credential handling, or trace redaction.

This keeps the risky authenticated LMS flow in the tested Python service and makes Rust an optional acceleration module for file transfer only.
