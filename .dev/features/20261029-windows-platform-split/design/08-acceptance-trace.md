# Acceptance trace

| Acceptance ID | Design flow / artifact | Persisted effect and invariant | Automated evidence | Runtime evidence | Rollback evidence | PR unit |
|---|---|---|---|---|---|---|
| AC1 | 07 § macOS invariants; 02 layers | none changed; invariants 1–6 | `macos-15-intel` CI: pytest, cargo test, typecheck, test:contract, tauri build, characterization tests on exact head | — | revert PR | S0-PR1 (introduces), every later PR (keeps green) |
| AC2 | 05 Flow 1–3 (macOS branches) | same app-data paths, LaunchAgent, Chrome argv | characterization tests | user installs the S1 build: login, course list, one download, one auto-watch, reminder toggle | reinstall known-good dmg | S1 checkpoint after S1-PR4 |
| AC3 | 02 core invariant | core imports nothing from platforms/service | import-linter in CI + pytest guard with a deliberate-violation fixture | — | revert PR | S1-PR2 (core/service), S1-PR3 (platforms) |
| AC4 | 02 PlatformShell; 07 step 4 | `libc` cfg(unix) only | `cargo test` on macOS + `cargo check`/`cargo test` on windows-latest | — | revert PR | S1-PR4 (macOS module + Windows stubs compile) |
| AC5 | 05 Flow 1 (Windows branch); 04 app_data_root | sidecar `ssu-api.exe` in NSIS resources | windows-latest job: PyInstaller build, NSIS build, start sidecar → `/api/health` ok, upload installer artifact | — | n/a (new artifact) | S2-PR7 |
| AC6 | 05 Flow 2–3 (Windows branches); 02 WindowsPlatform/WindowsShell | Job Object kill; schtasks XML; File::try_lock; app-data under %LOCALAPPDATA% | Windows pytest (Chrome locator with fake registry/paths, Job Object kill of a spawned tree, AppPaths) + Windows cargo test (schtasks XML snapshot, lock, sidecar name) | — | revert PR | S2-PR5 (Python), S2-PR6 (Rust) |
| AC7 | all flows, Windows | real Windows install | — | friend on a real Windows PC: install, login, MP4/MP3/TXT download, auto-watch, reminder notification; screenshots | uninstall (NSIS removes the task) | S3 |
