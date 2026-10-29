# Design index

- Feature record: ../feature.md
- Feature ID: 20261029-auto-update
- Current slice: S1 (updater-ready app, macOS) → S2 (release pipeline + first-update dogfood) → S3 (Windows entry, sprint-2)
- Current PR unit: CONTROL (sprint-1 manifest in ../sprints/sprint-1.md)
- Design profile: LIGHT
- Design status: AWAITING_APPROVAL (revision 2)
- Target/base: develop (planning base origin/develop 30c43a98c95fa0f64ad76409ed4169455e391081)

## Revision history

- Revision 1 (approved by the user 2026-10-29, digest recorded in ../feature.md): releases in a separate public releases-only repo, source private, PAT for cross-repo upload.
- Revision 2 (2026-10-29): the owner made the source repo public; releases now publish from the same repo with the workflow `GITHUB_TOKEN`. Dropped: releases repo, fine-grained PAT, CP-REPO-NAME, AU-OPS1. Updated: endpoint, release-job permissions, sidecar-stop references to the merged platform module, CI/contract-test notes. Simplification only; no new scope.

## Source evidence

- Repository inspection 2026-10-29 on develop 30c43a9 (read-only):
  - `frontend/src-tauri/Cargo.lock`: tauri 2.11.2, tauri-plugin-dialog 2.7.1, reqwest 0.12.28 (direct) and 0.13.3 (transitive).
  - `frontend/src-tauri/Cargo.toml`: `rust-version = "1.77.2"`, version 0.1.0; `frontend/package.json` 0.1.0 with `@tauri-apps/api ^2.11.0`, `@tauri-apps/cli ^2.11.2`; `pyproject.toml` 0.1.0; `tauri.conf.json` 0.1.0, identifier `com.local.ssu-lecture-downloader`, resources `resources/backend-sidecar`.
  - `frontend/src-tauri/src/platform/{mod,macos,windows}.rs` (S1-RS-PLATFORM, merged): `terminate_process_group`, `reap_stale_backend`, `configure_sidecar_command`, `sidecar_executable_name` — macOS bodies in `macos.rs`, Windows stubs are no-ops (`terminate_process_group` does nothing on Windows yet).
  - `frontend/src-tauri/src/backend.rs`: `BackendManager::shutdown()` (idempotent, ~line 428) → `terminate_process_group(&mut child, BACKEND_TERMINATE_GRACE)`.
  - `frontend/src-tauri/src/lib.rs`: `RunEvent::ExitRequested | Exit` → `watch_runtime::shutdown` + `shutdown_backend`; command handlers registered in `generate_handler!`.
  - `frontend/src-tauri/permissions/main-window-commands.toml` + `src/acl_tests.rs`: every app command needs a permission entry; a test keeps the handler list and the permission file in step. `capabilities/default.json` has core/dialog/main-window-commands only.
  - `frontend/scripts/contract-test.mjs`: required-file list (includes `src-tauri/src/platform/*.rs`, `lib.rs`, `backend.rs`, `tauri.conf.json`, `Cargo.toml`) and source-marker assertions; new/moved files must be added there.
  - `.github/workflows/ci.yml` (S0-CI + S1-RS-PLATFORM): `macos` job on macos-15-intel (pytest, sidecar build, cargo test, tauri build `--bundles app`), `windows` job `cargo check --lib`; workflow `permissions: contents: read`.
  - `watch_queue.rs` `is_active()`, `watch_runtime.rs` `external_active()`, `native_download.rs` job status commands; backend job router in the Python service layer.
  - Local toolchain rustc 1.95.0 (≥ 1.90).
- Research (official docs, verified by the main task 2026-10-29): see ../feature.md § Research. Key points: plugin 2.14.1 needs tauri ^2.12 / Rust 1.90; tauri-action@v1 publishes to the workflow's own repo with `GITHUB_TOKEN` (`contents: write`); one incomplete platform in `latest.json` breaks every client → draft then publish; macOS ad-hoc update swaps the .app atomically but TCC re-prompts; no delta updates.
- Sibling design: ../../20261029-windows-platform-split/design/ (S2 adds the NSIS build, `ssu-api.exe`, and the Windows process-tree stop).

## Artifact ledger

| Artifact | Status | Evidence / reason |
|---|---|---|
| Context map | REQUIRED | `01-context-map.md`: publication authority (GitHub Releases on the public source repo), signing-key custody, GitHub Actions, and who may write to each. |
| Domain model | N/A | No domain entities or aggregates change; the updater is a plugin call path plus a guard. |
| Current schema | N/A | No database. `latest.json` is a plugin-defined file, described in `07-consistency-migration.md`. |
| Target schema | N/A | No database or app-data file changes. |
| Transaction flows | REQUIRED | `05-transaction-flows.puml`: check-and-install flow in the app; user-triggered release publication flow. |
| State machines | REQUIRED | `06-state-machines.puml`: update notice / install lifecycle (guard refusal, error, relaunch). |
| Consistency and migration | REQUIRED | `07-consistency-migration.md`: versioning, key custody, publication consistency (draft → publish), failure/recovery and cleanup owners, first-install migration, rollback. |
| Acceptance trace | REQUIRED | `08-acceptance-trace.md`. |
| Visual review pack | REQUIRED | `09-design-review.md`. |

## Confirmed decisions

- D1 develop now; D2 launch + 6 h check, notify, install on click, "나중에" hides 24 h; D4 only the user publishes releases, develop merges never publish; D5 autonomous within scope, Claude merges reviewed + CI-green PRs to develop, LIGHT profile; D6 macOS first, Windows after sibling S2; D7 source repo is public and releases publish from it (supersedes D3); D8 Claude generates the signing key, the owner backs it up before the secrets are set.

## Proposed decisions

- P1 Official `tauri-plugin-updater` + `tauri-plugin-process`; JS API drives check/download/install from the React UI; no custom update server.
- P2 Endpoint: `https://github.com/cafitac/ssu-lecture-downloader/releases/latest/download/latest.json` (static JSON produced by tauri-action). `latest` excludes drafts and pre-releases, so a draft is invisible to clients.
- P3 Check schedule: on launch (after the main window is ready) and every 6 hours; failures logged, never shown.
- P4 Two-layer busy guard before install: UI layer refuses while any tracked backend job (download/convert/STT) runs; Rust command `update_install_guard` (new `src/update.rs`) refuses while `watch_queue` is active / an external watch is active / a native download job runs. Then Rust `prepare_for_update` calls `watch_runtime::shutdown` and the existing idempotent `BackendManager::shutdown()` (→ `platform::terminate_process_group`), and only then the UI calls `install` and `relaunch`.
  - Order: download first (sidecar still running, app usable), then guard + prepare + install; the sidecar-off window is only the install itself. If the guard refuses after a download, the downloaded update is discarded and re-downloaded on the next click. A failed install calls the existing `backend_restart` command.
  - `update.rs` is platform-neutral so the Windows `cargo check --lib` job stays green; the Windows sidecar stop becomes real when sibling S2 implements the Windows `terminate_process_group`.
- P5 Capabilities: only the `main` window gets `updater:default`, `process:allow-restart` and the two new app-command permissions (`permissions/main-window-commands.toml`); `watch-remote.json` gets nothing new.
- P6 Signing: minisign key from `tauri signer generate` with a password, written outside any repo; private key + password only in the owner's backup and the Actions secrets `TAURI_SIGNING_PRIVATE_KEY` / `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`; the public key is committed in `tauri.conf.json`. The repo is public, so nothing secret is ever committed.
- P7 Release workflow (`.github/workflows/release.yml`, separate from `ci.yml`): workflow `permissions: contents: read`; `verify-version` → `build-macos` (`contents: write`; sidecar first, then tauri-action into a draft release on the same repo with `GITHUB_TOKEN`) → `finalize` (`contents: write`; needs all builds; asserts expected platform keys and signatures in the draft's `latest.json`, then publishes). Expected keys: `darwin-x86_64` (sprint-1), + `windows-x86_64` (sprint-2).
- P8 Single version source: `scripts/bump_version.py <x.y.z>` rewrites tauri.conf.json, Cargo.toml (+ lock entry), package.json, pyproject.toml; `verify-version` fails on any mismatch with the tag.
- P9 Contract-test upkeep: units that add Rust/TS files add them to `contract-test.mjs` required files; AU-PR2 adds the updater config/capability assertions there.

## Open questions

- O3 Whether TCC re-prompts after each update are acceptable (expected because the ad-hoc cdhash changes); measured at AC6.
- O4 Updater target key for the x86_64 build running under Rosetta on Apple Silicon (expected `darwin-x86_64`; verify at AC6 via the request log).
- O5 Windows sidecar stop before the NSIS installer (`on_before_exit` vs explicit `prepare_for_update`) — inference, verified in sprint-2.
- O6 2.11 → 2.12 breaking changes in the tray/window APIs used by lib.rs/watch_runtime.rs/platform — unknown until AU-PR1 compiles.

## Review findings

- F1 (accepted): the plugin installs on macOS by replacing the bundle in place; the LaunchAgent reminder entry references the app path inside /Applications, which stays the same, so no LaunchAgent rewrite is needed. Verify at AC6 that the next reminder still fires.
- F2 (accepted): an incomplete `latest.json` breaks clients on every platform, so tauri-action must not publish directly; publication is a separate `finalize` job (P7).
- F3 (accepted): stopping the sidecar before download would make the app unusable for the whole download; reordered to download → guard → prepare → install (P4).
- F4 (accepted): `latest/download/latest.json` returns the newest non-draft, non-prerelease release; un-publishing a bad release reverts clients to the previous one only if that one is still published — documented as the kill switch.
- F5 (rejected): embedding a GitHub token in the app — unnecessary now that the repo is public (anonymous read).
- F6 (accepted, revision 2): `contents: write` only on the two release jobs, not workflow-wide; `ci.yml` stays `contents: read`; pull-request events never run `release.yml`.
- F7 (accepted, revision 2): new app commands must be listed in `permissions/main-window-commands.toml` or `acl_tests.rs` fails; new files must be listed in `contract-test.mjs`.

## Validation evidence

- `python3 <agent-skills>/skills/feature-design/scripts/validate_design.py --record ../feature.md` — result and artifact-set digest recorded in ../feature.md (not here, to avoid a self-referential hash).
- PlantUML and Mermaid renderers are not installed on this Mac (`plantuml`, `mmdc` not found): `.puml` sources are envelope-checked only and Mermaid blocks were not rendered — named evidence limitation.
- Design-review pass: self checklist (LIGHT; no independent agent review). Findings F1–F7.
