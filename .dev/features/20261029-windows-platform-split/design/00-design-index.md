# Design index

- Feature record: ../feature.md
- Feature ID: 20261029-windows-platform-split
- Current slice: S0 (CI safety net) → S1 (layer refactor, macOS only) → S2 (Windows) → S3 (Windows QA)
- Current PR unit: CONTROL (PR map is proposed here and finalized by feature-plan after approval)
- Design profile: STANDARD
- Design status: AWAITING_APPROVAL
- Target/base: develop (origin/develop @ 048c35f5684c3463d80e75ae62ff7b1082743330)

## Source evidence

- Code survey (read-only explorer, 2026-10-29) of `src/ssu_lecture_downloader/` (9,460 lines, 40 modules incl. routers) and `frontend/src-tauri/src/` (8,583 lines). Key file:line anchors are listed in `07-consistency-migration.md`.
- Import check (2026-10-29, grep of every core-candidate module): the only core→platform edge is `stt_remote.py → stt_remote_config.py` (config file path). No core module imports `app_context`, `api*`, `routers/*`, `security`, `credentials`, or `cli`.
- Windows research (2026-10-29, official docs + PyPI/docs.rs): Job Objects for process trees; `schtasks /XML` for per-user scheduled tasks; Tauri 2.12 tray `set_title` unsupported on Windows (use tooltip), `set_badge_count` unsupported (use `set_overlay_icon`); notification plugin works only for installed apps; NSIS `installMode: currentUser` + `NSIS_HOOK_PREUNINSTALL`; PyInstaller 6.22; imageio-ffmpeg win_amd64 wheel bundles ffmpeg.exe; ctranslate2 4.8 win_amd64 CPU wheels; GitHub runners: `macos-13` retired, Intel = `macos-15-intel`, Windows = `windows-latest` (Server 2025).
- Installed macOS app: x86_64 (`lipo -archs`), running under Rosetta on an M2 Pro; Rust host toolchain `x86_64-apple-darwin`; uv Python x86_64.
- App-data on this Mac: `~/Library/Application Support/com.local.ssu-lecture-downloader/` holds `backend.pid`, `chrome-watch-profile/`, `chrome-watch.pid`, `deadlines.json`, `notified.json`, `reminders.lock`, `stt.json`.

## Artifact ledger

| Artifact | Status | Evidence / reason |
|---|---|---|
| Context map | N/A | Single local desktop app with one external system (Soongsil LMS, unchanged). The external boundary is shown in the review-pack overview; no bounded-context ownership changes. |
| Domain model | REQUIRED | `02-domain-model.puml`: layer/module model and platform interfaces (the design's core change). |
| Current schema | N/A | No database. Current app-data file paths are recorded as evidence in `07-consistency-migration.md` (§ Current app-data). |
| Target schema | REQUIRED | `04-target-schema.dbml`: app-data files as persisted records, their per-OS location and single owner. |
| Transaction flows | REQUIRED | `05-transaction-flows.puml`: startup/app-data handoff, auto-watch Chrome lifecycle, reminder scheduling. |
| State machines | N/A | No lifecycle states change; the watch-queue state machine (`watch_queue.rs`) moves unchanged. |
| Consistency and migration | REQUIRED | `07-consistency-migration.md`: macOS invariants, refactor steps, rollback. |
| Acceptance trace | REQUIRED | `08-acceptance-trace.md`. |
| Visual review pack | REQUIRED | `09-design-review.md`. |

## Confirmed decisions

- D1 one repository, layered split (user, 2026-10-29). D2 GitHub Actions builds Windows. D3 all features on Windows. D4 STANDARD design, autonomous within approved scope, Claude merges to develop after 2 reviews + CI.

## Proposed decisions

- P1 Python layers: `core/` (LMS, download, media, STT, watch logic), `platforms/` (`base.py` protocols + `macos.py` + `windows.py`), `service/` (FastAPI, wiring). `cli.py` stays at the package root as a second composition root. Name `platforms` (plural) avoids shadowing the stdlib `platform` module.
- P2 Only composition roots (`service/app_context.py`, `cli.py`) import `platforms`; `core` receives adapters and paths by constructor injection. Enforced by import-linter contracts in CI plus a pytest guard.
- P3 Rust is the single owner of the app-data root: it passes `SSU_APP_DATA_DIR` to the sidecar. macOS value stays `~/Library/Application Support/com.local.ssu-lecture-downloader`; Windows uses `%LOCALAPPDATA%\com.local.ssu-lecture-downloader`. Python falls back to the same per-OS value when the variable is absent (CLI/dev).
- P4 Process trees: macOS keeps `start_new_session` + `killpg` (Python) and `process_group(0)` + `killpg` (Rust) byte-for-byte; Windows uses a Job Object with KILL_ON_JOB_CLOSE via ctypes (Python) and `windows-sys` (Rust), `taskkill /T /F` as fallback, `CREATE_NO_WINDOW` for the sidecar.
- P5 Reminders: macOS LaunchAgent unchanged; Windows `schtasks /Create /XML` per-user task, removed by an NSIS `NSIS_HOOK_PREUNINSTALL` hook; notifications on Windows via `tauri-winrt-notification` (WinRT toast with the bundle identifier as app id, works in the Tauri-less `--check` CLI run), `osascript` unchanged on macOS; Windows file lock via `std::fs::File::try_lock`.
- P6 Tray: macOS title + Dock badge unchanged; Windows tooltip text + status menu item (+ optional overlay icon).
- P7 Windows packaging: NSIS currentUser, sidecar `ssu-api.exe`, faster-whisper CPU int8 bundled, imageio-ffmpeg ffmpeg.exe collected, `--noconsole`, no UPX; VC++ runtime verified on a clean Windows (open risk).
- P8 CI: `macos-15-intel` (matches the installed x86_64 app) runs the full suites + `tauri build`; `windows-latest` runs pytest, cargo test, sidecar build, NSIS build, sidecar `/api/health` smoke, uploads the installer.
- P9 S1 changes no runtime behavior on macOS; characterization tests pin the macOS constants (paths, LaunchAgent label/plist, Chrome args, sidecar path) before code moves.

## Open questions

- O1 msvcp140/VC++ runtime need for ctranslate2/onnxruntime on a clean Windows (verify in S2 smoke or S3; fallback: NSIS hook installs the redistributable).
- O2 `curl_cffi` async under the Windows Proactor event loop (verify in S2 Windows CI integration test).
- O3 Smart App Control on Windows 11 may block the unsigned app with no bypass (S3 finding; signing is DEFERRED R8).
- O4 Intel macOS runner retirement (~fall 2027): move the Mac build to arm64/universal2 later (out of this feature).

## Review findings

- F1 (accepted, fixed): the scheduled reminder run is a Tauri-less CLI path (`lib.rs` `run()` exits before building Tauri), so `tauri-plugin-notification` cannot serve it. Windows notifications now use `tauri-winrt-notification` directly with the installed app's identifier.
- F2 (accepted): existing mac-assuming tests (`backend.rs` killpg tests, `launch_agent.rs` fixtures, `test_chrome_watch*.py` paths) must move under the macOS module or get platform skips in S2-PR5/PR6 so the Windows runner is green without weakening macOS coverage.
- F3 (accepted): `scripts/build_backend_sidecar.py` must emit `ssu-api.exe` and skip `chmod` on Windows (S2-PR7); `backend.rs` sidecar name comes from `PlatformShell.sidecar_executable_name()` (S1-PR4).
- F4 (rejected): moving `ssh_tunnel.py` into platforms — it only shells out to `ssh`, which exists on both OSes; remote STT is out of scope on Windows (R9), so it stays in core unchanged.

## Validation evidence

- 2026-10-29 `python3 <agent-skills>/skills/feature-design/scripts/validate_design.py --record ../feature.md` → valid, 0 errors, 0 warnings (artifact-set digest is stored in ../feature.md).
- PlantUML and DBML renderers are not installed on this Mac (`plantuml`, `dbml2sql` not found): the `.puml`/`.dbml` sources were not rendered or parsed — named evidence limitation, not a semantic pass. The Mermaid review pack is the rendered view.
- Design-review pass: self checklist (STANDARD; independent agent review not run for STANDARD). Findings F1–F4 above.
