# Consistency and migration

## Scope of state

No database, server, or shared remote state. The only persisted state is the app-data files in
`04-target-schema.dbml`, the user's download folder, and one OS scheduler entry. File formats do not
change. No locks or transactions are introduced; the existing `reminders.lock` advisory lock keeps its
role and gains a Windows implementation.

## Current app-data (CURRENT, evidence)

| Item | Path computation (file:line) | Owner |
|---|---|---|
| chrome-watch-profile/ | `chrome_watch.py:125` `~/Library/Application Support/<id>/chrome-watch-profile` (env override exists) | Python |
| chrome-watch.pid | `chrome_watch.py:676` `profile_dir.parent / "chrome-watch.pid"` | Python |
| stt.json | `stt_remote_config.py:65`, written 0o600 at `:145` | Python |
| deadlines.json / notified.json / reminders.lock | `reminders.rs:19-22`, dir from `lib.rs:249` `data_dir_for_home` | Rust |
| backend.pid | `backend.rs:31` | Rust |
| default download dir | `app_context.py:34` `Path.home()/"Downloads"/...` | Python |
| Chrome binary | `chrome_watch.py:105-108` `/Applications`, `~/Applications` | Python |
| process group kill | `chrome_watch.py:260-261, 631, 725`; `backend.rs:407-535` (`libc::killpg`, `process_group(0)`) | Python / Rust |
| LaunchAgent | `launch_agent.rs:1-330` (`/bin/launchctl`) | Rust |
| notification | `reminders.rs:266-296` (`/usr/bin/osascript`) | Rust |
| open path / URL | `lib.rs:157, 175` (`open`) | Rust |
| real home dir | `lib.rs:112-135` `cfg(target_os = "macos")` with a non-mac fallback | Rust |
| sidecar path | `backend.rs:793-823` `resources/backend-sidecar/ssu-api/ssu-api`; build `scripts/build_backend_sidecar.py` (`chmod 0o755`) | Rust / build |
| local STT gate | `stt.py:82` `not (darwin and x86_64)` | Python |

## macOS invariants (must hold after every PR)

1. Every path in the table above resolves to the same absolute path on macOS.
2. LaunchAgent label, plist content, and launchctl calls are byte-identical.
3. Chrome launch argv (flags and order) and kill sequence (SIGTERM, grace, SIGKILL on the group) are identical.
4. Sidecar location and name, spawn flags, and stop sequence are identical.
5. Local STT stays off on Intel macOS and on for Apple Silicon (same predicate).
6. API surface (routes, payloads, error codes) is unchanged; the React UI is untouched in S1.

Enforcement: S0 adds characterization tests that pin (1)–(5) as constants/argv snapshots on the
current code before anything moves; every later PR must keep them green on the `macos-15-intel`
runner, together with the full existing suites and a `tauri build`.

## Migration steps (behavior-preserving)

1. S0 — CI on `macos-15-intel` + characterization tests. No production code moves.
2. S1a — Python package moves: create `core/` and `service/`, move modules, update imports, the
   PyInstaller hidden-import list, and tests. Add import-linter contracts. Pure moves; no logic edits.
3. S1b — Extract `platforms/`: split `chrome_watch.py` into core manager + macOS locator/process-tree
   adapter (code moved verbatim), inject `AppPaths`/`SttCapability`/`SecretFileWriter`, read
   `SSU_APP_DATA_DIR` with the identical macOS fallback. Only `app_context.py` and `cli.py` import
   `platforms`.
4. S1c — Rust `platform/` module: move LaunchAgent, osascript, killpg/process_group, `open`, real-home,
   lock, tray title/Dock badge behind a `PlatformShell` cfg module; macOS bodies moved verbatim; the
   shell passes `SSU_APP_DATA_DIR`; `libc` becomes a `cfg(unix)` dependency.
5. User checkpoint: user installs the S1 macOS build when no watch is running and confirms AC2.
6. S2 — Windows adapters (Python, Rust), Windows packaging + CI. macOS code paths untouched.

Compatibility: no data migration. Old and new builds read the same files at the same macOS paths, so
switching between the old installed app and a new build is safe in both directions.

## Rollback

- Any PR: revert on develop (each S1 step is self-contained and keeps the app working).
- Installed Mac app: reinstall `~/Desktop/SSU-Lecture-Downloader.dmg` (built from 048c35f). App-data
  stays compatible.
- Windows: new artifact only; nothing to roll back on the user's Mac.

## Concurrency and idempotency

- One watch queue at a time (unchanged). Windows Job Object closes the whole Chrome tree even if the
  backend crashes (KILL_ON_JOB_CLOSE), matching the macOS "group dies with stop" guarantee.
- Scheduled reminder runs and the in-app checker serialize on `reminders.lock` (macOS unchanged,
  Windows `File::try_lock`); `notified.json` dedups notifications (unchanged).
- `schtasks /Create ... /F` is idempotent (replaces the task); removal tolerates "task not found".

## Privacy

- `stt.json` holds a write-only API key: macOS keeps 0o600; Windows relies on the per-user
  `%LOCALAPPDATA%` ACL. LMS credentials stay in memory only (unchanged).
