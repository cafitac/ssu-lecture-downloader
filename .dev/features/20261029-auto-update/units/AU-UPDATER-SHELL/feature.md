# AU-UPDATER-SHELL

- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-auto-update/feature.md
- Sprint manifest: ../../sprints/sprint-1.md
- Target/base: develop (origin/develop e2f0714)
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/au-updater-shell (feature/au-updater-shell)
- Design status: APPROVED (parent, revision 2)
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL

## Progress and evidence

- 2026-10-30: worktree created; CP-KEY done (secrets set, key backed up).
- 2026-10-30: implemented (local, not pushed). Plugins: `tauri-plugin-updater` 2.14.1 + `tauri-plugin-process` 2.4.0 (Cargo.lock), `@tauri-apps/plugin-updater` ^2.14.1 + `@tauri-apps/plugin-process` ^2.4.0, registered under `#[cfg(desktop)]` in `lib.rs`. `tauri.conf.json`: `bundle.createUpdaterArtifacts: true`, `plugins.updater.{pubkey, endpoints, windows.installMode: passive}` (public key content only). `capabilities/default.json` (main window): `updater:default`, `process:allow-restart`; the remote `watch-remote` capability is untouched. New `src/update.rs`: `update_install_guard` (-> `{status: allowed}` | `{status: refused, reason: watch_active | download_active}`) and `prepare_for_update` (re-checks the guard, then stops the sidecar; error `{code: refused, reason} | {code: backend_starting} | {code: backend_unavailable}`); both registered in `generate_handler!` and `permissions/main-window-commands.toml`.
- Design deviation (decided here, flag for review): design/05 and 07 say `prepare_for_update` calls `BackendManager::shutdown()`. `shutdown()` sets `shutting_down`, after which `begin_start` refuses forever, so the existing `backend_restart` recovery (06 Failed state) would silently do nothing. Added `BackendManager::stop_for_update()` (same `terminate_child` -> `platform::terminate_process_group`, but restartable; refuses with `backend_starting` while a startup worker runs; leaves state `failed` with a Korean note). The exit handler's `shutdown()` on relaunch is unchanged. Also added `NativeDownloadJobs::has_running_job()` (fails closed). The guard's watch check is `watch_runtime::is_queue_active`, which already covers the app's own queue and an external run. `watch_runtime::shutdown` is not called from prepare (the guard proved idle; the exit handler runs it on relaunch).
- Log lines: `updater: install guard allowed|refused (...)`, `updater: backend sidecar stopped for the update`.
- Tests: Rust unit tests (guard table, JSON shapes, refused-stops-nothing, stop-after-guard order, `stop_for_update` kills the tree and `restart()` still works, native job running detection), `acl_tests` (updater/restart main-window only; update commands unreachable from the LMS page), `contract-test.mjs` (pubkey/endpoint/createUpdaterArtifacts/installMode/ACL scoping/plugin registration/inert frontend).
- CI signing finding (verified locally with a real `tauri build --bundles app`): with `createUpdaterArtifacts: true` and no `TAURI_SIGNING_PRIVATE_KEY`, the build compiles and bundles the .app and then fails at the signing step (exit 1, "A public key has been found, but no private key"). `ci.yml` PR build now passes `--config '{"bundle":{"createUpdaterArtifacts":false}}'` (verified: bundle finishes, no error). The release workflow (AU-RELEASE-WF) must not use the override and must set the key secrets.
