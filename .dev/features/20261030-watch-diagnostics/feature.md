# Watch diagnostics (WATCH-DIAG)

- Status: IN_PROGRESS
- Target/base: develop (origin/develop d697c33)
- Worktree: ssu-lecture-downloader-wt/watch-diagnostics, branch feature/watch-diagnostics
- Owner decision (2026-10-30): the owner reported that "순서대로 시청" in the dedicated Chrome window
  stops partway since the 0.2.0 release ("zip 파일 전달할 때까지만 해도 괜찮았는데"). Nobody watched the screen when it
  stopped. The owner will retry on a version that logs the run, and wants a way to hand over
  diagnostics ("예기치 않은 동작이 발생했다면 그 앱을 추출해서 보낼 수 있도록").
- Evidence so far: the app and backend ran 2h+ without a restart; backend.log has no watch lines;
  watch.log only covers the in-app runtime (last written 10/19). Chrome-watch code changed since 0.1.0
  only by the verbatim platform extraction (#3).

## Scope (this PR)

- WD-PY: the backend Chrome watch logs each run's lifecycle to backend.log, at INFO, with ids, stages,
  counters and reasons only (no cookies, tokens or full URLs).
- WD-UI: "진단 파일 저장" in Settings (and on the backend failure screen) saves a zip of the app's log
  files plus version/OS info to a location the user picks. Nothing is uploaded.

## Out of scope (later)

- A release smoke gate that watches 2+ short videos on a signed local build (process step).
- Crash detection with a next-launch prompt.

## Acceptance

- AC1: a chrome-watch run writes INFO lines for: run start (count), item start (index, module_item_id),
  progress milestones, item end, stall detected (seconds without progress), skip (reason), Chrome process
  exit or CDP disconnect, worker exception, and run end (done/skipped counts, phase).
- AC2: no log line contains a cookie, token, password, or query string.
- AC3: the diagnostics zip contains backend.log, backend.previous.log and watch.log when present, plus
  `info.json` (app version, OS, arch, time). Missing files are skipped, not fatal.
- AC4: the zip is written only where the user chose, and the UI reports success or failure in plain Korean.
