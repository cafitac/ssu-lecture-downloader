# D10a Performance Cache Plan

Status: completed

Goal: make course lookup feel fast before continuing Tauri packaging work.

Why this comes before D10:

- Current `/api/terms`, `/api/courses`, and `/api/courses/{course_id}` create a fresh `SsuLectureService`/`HttpClient` and run the full LMS/SmartID/Canvas login flow on each request.
- `/api/terms` scans many term IDs.
- `/api/courses` without an explicit `term_id` scans the same term range again.
- The frontend initially calls `/api/terms` and `/api/courses`, then auto-loads first course detail, multiplying the latency.
- D9 saved credentials only; it did not persist or cache Canvas cookies / `xn_api_token`.

Architecture:

1. API process owns a single reusable service instance by default.
2. `SsuLectureService` caches authenticated sessions in memory per username and credential fingerprint.
3. The cache stores only `HttpClient` cookies/session in process memory; do not write raw cookies, `xn_api_token`, passwords, or bearer values to disk or docs.
4. On auth failure or explicit invalidation, drop the cached session and re-login.
5. Add TTL caches for expensive LMS list calls:
   - terms by username/current candidate set,
   - courses by explicit term id or current-term key,
   - course detail by course id/name.
6. Frontend should avoid implicit current-term scanning when it already knows the current term from `/api/terms`; call `/api/courses?term_id=<current>` instead.

Acceptance criteria:

- Repeated `/api/courses?term_id=...` requests reuse a service/session and do not call login repeatedly within cache TTL.
- Repeated `/api/terms` requests use a TTL cache and avoid rescanning term candidates within TTL.
- Frontend initial load uses the current term id from `/api/terms` to load courses explicitly.
- Credential save/delete invalidates API caches so new credentials do not reuse stale sessions.
- Tests prove the behavior without depending on real LMS credentials.
- Full gate remains green: `uv run pytest -q && cd frontend && npm run test:contract && npm run build`.

Verification completed:

- `uv run pytest -q` => 88 passed.
- `cd frontend && npm run test:contract && npm run build` => passed.
- Existing local ports 8000/5173 were already occupied during final verification, so no live server processes were restarted or killed.

Deferred after D10a:

1. D10 minimal Tauri shell.
2. Tauri process supervision for Python FastAPI service.
3. Batch background jobs/cancel parity.
4. First-run Keychain UX polish.
5. Optional Rust byte-transfer boundary only if Python streaming/cancel semantics become limiting.
