# S0-CI: CI safety net and macOS characterization tests

- Record schema: 4
- Status: IN_PROGRESS
- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-windows-platform-split/feature.md
- Sprint ID: windows-platform-split-sprint-1
- Sprint manifest: ../../sprints/sprint-1.md
- Sprint manifest SHA-256: 1388772957c092e529131b0b0a23f7813b818e25551613490033ee4c56d67c04
- Repository: cafitac/ssu-lecture-downloader
- Target/base: develop
- Base ref: origin/develop
- Base decision: repository default integration branch
- Initialization base SHA: 048c35f5684c3463d80e75ae62ff7b1082743330
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/s0-ci (feature/s0-ci)
- Canonical record: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/s0-ci/.dev/features/20261029-windows-platform-split/units/S0-CI/feature.md
- Design profile: STANDARD (parent)
- Design status: APPROVED (parent, sha256 f0c28b02634270935586ad670eee0e755c932220f196f6a229604b932557c8a6)
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Worktree cleanup status: ACTIVE
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT

## Outcome

GitHub Actions on `macos-15-intel` runs the full existing suites and a macOS app build on every PR
to develop, plus characterization tests that pin the macOS invariants 1–5 of
`design/07-consistency-migration.md` before any code moves.

## Acceptance subset

- AC1 (baseline): CI green on the exact PR head.

## Workflow cursor

- Workflow stage: FEATURE_DEVELOP
- Next eligible action: FEATURE_DEVELOP
- Next action class: LOCAL_CONTINUE
- Gate state: NONE

## Progress and evidence

- 2026-10-29: worktree created from origin/develop 048c35f; approved design + manifest copied in (bytes unchanged, manifest sha prefix 1388772957c092e5).
- 2026-10-29: implemented. Added `.github/workflows/ci.yml` (job `macos` on `macos-15-intel`, concurrency cancel-in-progress per ref), `tests/test_macos_invariants.py` (20 tests) and `frontend/src-tauri/src/invariant_tests.rs` (14 tests, one `#[cfg(test)] mod invariant_tests;` line in lib.rs). No production code changed.
- 2026-10-29 local verification (x86_64 macOS): `uv run pytest` -> 491 passed; `cd frontend/src-tauri && cargo test` -> 150 passed (14 new); `cd frontend && npm ci && npm run typecheck` -> ok; `npm run test:contract` -> "frontend contract ok". `tauri build` not run locally (CI does it).
- Finding: `cargo test` fails in `build.rs` when `frontend/src-tauri/resources/backend-sidecar` does not exist (tauri-build resource check), so CI builds the sidecar before `cargo test`.
- Pinned (Python): chrome-watch-profile dir, chrome-watch.pid location, stt.json path (+0o600), default download dir, sidecar build output layout (+chmod 0o755), Chrome candidates, Chrome argv and `open -g -n -W` background argv, spawn kwargs (`start_new_session=True`, DEVNULL streams), kill sequence (SIGTERM, then SIGKILL after grace; `os.killpg`), local-STT predicate and Intel-Mac message, pyproject faster-whisper marker.
- Pinned (Rust): app data dir for a home, data file names, tauri identifier and bundled sidecar resource, owner-only atomic write, LaunchAgent label/interval/plist path, byte-exact plist, launchctl argv sequences (install, bootstrap fallback to `load -w`, moved-exe bootout, uninstall), osascript argv, backend host/port/token constants, sidecar relative path (resource dir and dev root), uvicorn fallback argv, sidecar spawn argv.
- Pinned only by source-text tripwires (`include_str!`, they fail on text change but do not prove behavior; S1-RS-PLATFORM must move each assertion with the code, not delete it): backend.rs `BACKEND_PID_FILE`, `BACKEND_TERMINATE_GRACE` (3 s), `.process_group(0)`, `.env(BACKEND_TOKEN_ENV, &token)`, `libc::killpg(.., SIGTERM/SIGKILL)`; reminders.rs `LOCK_FILE` (`reminders.lock`).
- Not pinnable without production changes (remaining gaps): backend spawn stdio and cwd wiring inside `launch_attempt` (behavior, not text), `real_user_home_dir` (getpwuid; private, partly covered by the existing lib.rs test), `open` calls in lib.rs, tray/Dock code. The SIGTERM-then-SIGKILL stop behavior of `terminate_process_group` stays covered by the existing `backend::tests`.
- 2026-10-29: Draft PR https://github.com/cafitac/ssu-lecture-downloader/pull/1 (feature/s0-ci -> develop).
- CI repair pass 1 (run 40835189182, head effcd0b): `cargo test` failed on two pre-existing tests (`backend::tests::managed_backend_becomes_ready_...`, `failed_backend_can_be_restarted_...`) that serve a fake backend with Python's `http.server`. Diagnosis (runs 40836182988, 40836971970): `HTTPServer.server_bind()` calls `socket.getfqdn()`; on `macos-15-intel` that reverse lookup takes ~35 s (measured), so the fake backend missed the tests' 30 s wait. Fix, CI-only: the `Rust tests` step puts a `sitecustomize.py` that skips `getfqdn` on `PYTHONPATH` for that step. No test or production code changed.
- CI green on head 2f12b94 (run 40838518903, ~6 min): pytest 491 passed, typecheck, contract test, sidecar build, cargo test 150 passed, `tauri build --bundles app` all success. Final head SHA and last CI run are in the PR (the last commit after 2f12b94 only drops a diagnostic print and records this evidence).
- 2026-10-29 review cycle 1 repair batch (two reviewers on 3c87c01), one push: (1) Python tests now pin the production `background_launch=True` path (default asserted; `open -g -n -W` Popen argv and kwargs; pid file holds Chrome main pid 11, not the launcher; terminate SIGTERM only, or SIGTERM then SIGKILL with the launcher waited and pid file removed) and assert SIGKILL only after `terminate_grace` elapsed in both foreground and background; (2) Rust source-text tripwires listed above; (3) job env `UV_LOCKED=1`; (4) characterization tests platform-gated (Rust `cfg(all(test, target_os = "macos"))`, Python `skipif sys.platform != "darwin"`); (5) getfqdn shim moved to an early job step exporting `PYTHONPATH` through `GITHUB_ENV` (pytest and cargo test both benefit; cleared for the sidecar and tauri build steps so it cannot enter the bundle; `runner` context is not allowed in job-level `env`, hence the step); (6) the pid-file test manager now uses inert `process_command`/`killpg` (no real `ps`/`killpg` at atexit); (7) CI asserts the built app has `Contents/Resources/resources/backend-sidecar/ssu-api/ssu-api` (executable) and `_internal/`; (8) `cancel-in-progress` only for pull_request events; (9) pyproject faster-whisper marker parsed with `tomllib`; (10) actions bumped to checkout@v7, setup-node@v7, setup-uv@v7 (tags verified; setup-uv has no floating v8/v10 tag; all node24); (11) setup-uv `python-version: "3.12"` (local sidecar build uses CPython 3.12.11).
- Local after batch: `UV_LOCKED=1 uv run pytest` 495 passed; `cargo test` 152 passed; `npm run typecheck` exit 0; `npm run test:contract` ok.
- 2026-10-29 review cycle 2 repair batch (last cycle), one push: (1) Rust tripwires now search only the production slice of backend.rs and reminders.rs (split at the first `#[cfg(test)]\nmod tests`; the marker's presence is asserted, so a match inside unit tests cannot satisfy them; `.process_group(0)` also occurs in tests at backend.rs 1109/1145/1174); (2) added the stop-path tripwire `terminate_process_group(&mut child, BACKEND_TERMINATE_GRACE)`; (3) the pid-file test also asserts the manager's default profile resolution (`profile_dir == default_profile_dir()`, inert hooks); (4) concurrency group is per SHA for push runs (`ci-<ref>-<sha>` on push, `ci-<ref>-pr` on pull_request), cancel-in-progress still pull_request only.
- Local after batch: `UV_LOCKED=1 uv run pytest` 495 passed; `cargo test` 152 passed; `npm run typecheck` exit 0; `npm run test:contract` ok.

