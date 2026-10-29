# Sprint manifest: auto-update sprint-1 (macOS updater + release pipeline)

- Sprint ID: auto-update-sprint-1
- Sprint version: 1
- Created: 2026-10-29
- Expiry: 2026-11-12 (2 weeks; replan after expiry)
- Planning base: origin/develop @ 30c43a98c95fa0f64ad76409ed4169455e391081 (read-only; main checkout HEAD equals origin/develop at planning time)
- Base-drift policy: ALLOW_IF_DISJOINT — other features (windows-platform-split remaining units, grades-view) may also write develop; before each reservation run `git diff <planning-base>..origin/develop --name-only` against the unit's owned paths; an overlap stops the unit for a rebase/replan decision instead of silently proceeding
- Outcome: the app checks for updates on launch and every 6 h, shows a notice, and installs a signed update on the user's click; a user-triggered workflow publishes signed macOS releases as drafts on cafitac/ssu-lecture-downloader and publishes them only when complete. Ends at the AC5 dry run and the offer of CP-DOGFOOD.
- Target user: the owner (daily macOS user); friend's Windows PC comes with sprint-2.
- Hypothesis: with the official Tauri updater, draft-then-publish releases, and a busy guard, the owner can move to new versions with one click and no risk to a running auto-watch.
- Non-goals: Windows update entry (AU-PR5, sprint-2 after the sibling S2 NSIS unit), code signing/notarization, delta updates, silent install, beta channels, publishing a real release (user-only).
- Requirements: R1–R9 (feature.md)
- Acceptance IDs: AC1, AC2, AC3, AC4, AC5 (+ AC6 at the CP-DOGFOOD user checkpoint) (design/08-acceptance-trace.md)
- Design profile: LIGHT
- Canonical design root: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-auto-update/design
- Design approval evidence: pending — revision 2 needs the user's GO (revision 1 approved 2026-10-29 "둘 다 승인 (추천)", superseded by the public-repo simplification)
- Design artifact-set SHA-256: d113ae5bb73f7ccc7ca6d04ad18c6cd8bb02848f99ee96e0e31a344f43a47efb (revision 2; this manifest is not executable until that digest is APPROVED)

## Metrics

- Primary success metric: all four units MERGED with green CI on each exact head (macOS job + Windows `cargo check --lib` + contract test) — evidence: GitHub Actions run per PR head.
- Guardrail metric: no behavior change in existing features before a release is published (updater inert without a release) — evidence: CI suites + the user's AC6 report later.
- Observation window: from AU-DEPS merge to the AC5 dry run.
- Comparison/decision threshold: any AC1 regression (existing suite or build) stops AU-UPDATER-SHELL admission until fixed or reverted.

## Eligible unit queue

- Eligible unit IDs: AU-DEPS, AU-UPDATER-SHELL, AU-UPDATE-UI, AU-RELEASE-WF
- PR aliases: AU-DEPS = AU-PR1, AU-UPDATER-SHELL = AU-PR2, AU-UPDATE-UI = AU-PR3, AU-RELEASE-WF = AU-PR4
- Dependency graph: AU-DEPS → [CP-KEY] → AU-UPDATER-SHELL → AU-UPDATE-UI; AU-UPDATER-SHELL → AU-RELEASE-WF (parallel with AU-UPDATE-UI); AU-RELEASE-WF MERGED + CP-KEY secrets → AC5 dry run (user OK) → CP-DOGFOOD (user)
- Satisfied external dependencies: sibling S0-CI and S1-RS-PLATFORM MERGED at 30c43a9
- Selection/rejection rule: select the lowest-ordered unit whose predecessors are MERGED and whose checkpoint is satisfied; AU-UPDATE-UI and AU-RELEASE-WF may run in parallel.
- Ownership / shared-resource exclusivity: AU-DEPS and AU-UPDATER-SHELL own `frontend/src-tauri/**` and `frontend/package.json`/lock (sequential, so never concurrent); AU-UPDATE-UI owns `frontend/src/**`; AU-RELEASE-WF owns `.github/workflows/release.yml` (new; `ci.yml` untouched), `scripts/bump_version.py`, `tests/test_bump_version.py`, README release section. `frontend/scripts/contract-test.mjs` is shared: AU-UPDATER-SHELL and AU-UPDATE-UI may only append required-file entries and assertions; if both are open at once, the later one rebases on the earlier.
- Approved initial queue state: AU-DEPS = READY (after design GO + manifest confirmation); AU-UPDATER-SHELL = WAITING_DEPENDENCY (AU-DEPS + CP-KEY); AU-UPDATE-UI, AU-RELEASE-WF = WAITING_DEPENDENCY.

## Delivery bundle

- N/A — every unit is a STANDALONE PR to develop, independently revertible and inert for users until the user publishes a release.

## Review topology

- Review topology: CODE_POLISH_QUORUM
- Review quorum: 2
- Review scheduling: PARALLEL
- Review cycle budget: 3
- Unit review topology map: AU-DEPS=INHERIT, AU-UPDATER-SHELL=INHERIT, AU-UPDATE-UI=INHERIT, AU-RELEASE-WF=INHERIT
- Unit review cycle budget map: AU-DEPS=INHERIT, AU-UPDATER-SHELL=INHERIT, AU-UPDATE-UI=INHERIT, AU-RELEASE-WF=INHERIT
- Single-reviewer exception scope: N/A
- Single-reviewer exception rationale: N/A
- Single-reviewer exception approval: N/A

## Budgets

- Time budget: 2 weeks (expiry)
- Maximum-unit budget: 4
- WIP budget: 2 implementation lanes, 3 lanes total including review
- Repair-pass budget: 3 per unit
- Same-failure budget: 2
- Retry budget: 3 (CI infra flakes)
- External-cost budget: GitHub Actions minutes (public repo, standard runners); the AC5 dry run is one macOS release run; stop and ask before any additional release-workflow run.
- Consumed / remaining: tracked in the control record.

## Authority matrix

- LOCAL_READ: ALLOW
- LOCAL_RECORD_WRITE: ALLOW
- GIT_WORKTREE: ALLOW (dedicated worktrees only)
- LOCAL_IMPLEMENT_TEST: ALLOW
- COMMIT_PUSH: ALLOW (cafitac identity, feature branches only; no secrets — public repo; review includes a secret scan of the diff)
- DRAFT_PR: ALLOW (cafitac/ssu-lecture-downloader → develop)
- TRACKER_SYNC: DENY (GitHub PRs are the tracker projection)
- REMOTE_TEST_RUNNER: DENY
- WORKTREE_CLEANUP: ALLOW (AUTO_AFTER_SAFE_TERMINAL only)
- SIGNING_KEY_GENERATE: ALLOW only at CP-KEY after the owner's explicit go; key written to `~/.tauri/` outside any repo; never printed, committed, or recorded
- ACTIONS_SECRETS_WRITE: USER_ONLY (or Claude with the owner's explicit OK at CP-KEY, after the backup is confirmed)
- RELEASE_WORKFLOW_RUN (tag push / workflow_dispatch): USER_ONLY; the AC5 dry run needs the owner's explicit OK and stays a draft
- RELEASE_PUBLISH: USER_ONLY
- APP_REINSTALL (/Applications): USER_ONLY

## Finalization

- Delivery finalization: AUTO_AFTER_GATES
- Exact PR/bundle scope: the four unit PRs above, target develop
- Required gate set: review quorum 2 converged on the exact head; GitHub Actions green on the exact head (`macos` job: pytest, sidecar build, cargo test, typecheck, test:contract, tauri build; `windows` job: `cargo check --lib`); no secret material in the diff; unit-specific evidence (AU-UPDATE-UI: state screenshots with a mocked endpoint).
- Approved-scope evidence: pending — user GO on design digest d113ae5b… and confirmation of this manifest's digest
- Merge: main task merges into develop under the standing grant `merge-ssu-develop` (squash), only after every gate above. Merge never publishes a release.

## Stop, recovery, completion

- Stop: the Tauri upgrade requires a behavior change in existing features; a design boundary change is needed (e.g. a different endpoint, a token in the app, install without a click); same-failure budget exhausted; any secret appears in a diff or log.
- Pause: GitHub Actions unavailable; CP-KEY not yet done (AU-UPDATER-SHELL waits); user interrupts.
- Recovery: repair within the cycle budget; otherwise escalate.
- Rollback: revert the unit PR on develop; installed apps unaffected until the user publishes a release; delete any draft release.
- Sprint-complete: all four units MERGED and the AC5 dry-run evidence recorded (or the owner declines the dry run); then present CP-DOGFOOD to the owner.
- Sprint-boundary: sprint-2 (AU-WIN-ENTRY = AU-PR5) is planned after the sibling windows-platform-split S2 NSIS packaging unit merges.
- Mandatory human checkpoints: CP-KEY (owner backs up the key before secrets are set), AC5 dry-run permission, CP-DOGFOOD (owner installs N manually, publishes N+1, updates in-app).

## Unit details

### AU-DEPS

- Repository/target/base: cafitac/ssu-lecture-downloader, develop, origin/develop@30c43a9
- Outcome: tauri/tauri-build ≥ 2.12 (Cargo.lock), `@tauri-apps/api` ^2.12, `@tauri-apps/cli` ^2.12, `rust-version = "1.90"`; compile fixes only (lib.rs, watch_runtime.rs, `src/platform/*.rs`).
- Acceptance subset: AC1
- Dependencies / next-unit condition: sibling S0-CI, S1-RS-PLATFORM MERGED (satisfied) / MERGED + CP-KEY unlock AU-UPDATER-SHELL
- Owned paths: `frontend/src-tauri/Cargo.toml`, `frontend/src-tauri/Cargo.lock`, `frontend/package.json`, `frontend/package-lock.json`, compile fixes under `frontend/src-tauri/src/**`
- Shared contracts/resources: none (if a file moves, update `frontend/scripts/contract-test.mjs` required files)
- Affected boundaries: build toolchain, Tauri runtime
- Deployment compatibility: no IPC/app-data change
- Rollback/containment: revert PR
- Observability: CI logs
- Review map: single PR
- Split decision: N/A_SINGLE_BOUNDARY
- Coherent scope/refactor boundary: version bumps + compile fixes only
- Risk/reversibility: medium (2.11→2.12 API changes) — CI macOS build + Windows cargo check guard
- Verification evidence: green CI on the exact head
- Design dependencies: design/07 § Versions
- Required authority subset: GIT_WORKTREE, LOCAL_IMPLEMENT_TEST, COMMIT_PUSH, DRAFT_PR, WORKTREE_CLEANUP
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: READY (after design GO + manifest confirmation)

### AU-UPDATER-SHELL

- Repository/target/base: same, develop
- Outcome: `tauri-plugin-updater` 2.14.x + `tauri-plugin-process` 2.4.x (+ JS packages); `tauri.conf.json`: `bundle.createUpdaterArtifacts: true`, `plugins.updater.pubkey` (public key from CP-KEY), `plugins.updater.endpoints: ["https://github.com/cafitac/ssu-lecture-downloader/releases/latest/download/latest.json"]`; `capabilities/default.json` (main window): `updater:default`, `process:allow-restart`, new command permissions; new `src/update.rs` with `update_install_guard` and `prepare_for_update` (`watch_runtime::shutdown` + `BackendManager::shutdown()` → `platform::terminate_process_group`), registered in `generate_handler!` and `permissions/main-window-commands.toml` (acl_tests parity); `contract-test.mjs`: add `src-tauri/src/update.rs` to required files + config/capability assertions; Rust unit tests for the guard table and shutdown-before-install ordering. Platform-neutral (Windows `cargo check --lib` green). No UI.
- Acceptance subset: AC2, AC3
- Dependencies / next-unit condition: AU-DEPS MERGED + CP-KEY done / MERGED unlocks AU-UPDATE-UI and AU-RELEASE-WF
- Owned paths: `frontend/src-tauri/{Cargo.toml,Cargo.lock,tauri.conf.json,capabilities/default.json,permissions/main-window-commands.toml,src/lib.rs,src/update.rs}`, `frontend/package.json` + lock, `frontend/scripts/contract-test.mjs` (append only)
- Shared contracts/resources: Tauri command names `update_install_guard`, `prepare_for_update` (consumed by AU-UPDATE-UI); public key
- Affected boundaries: plugin set, ACL, sidecar lifecycle
- Deployment compatibility: inert (no caller, no release)
- Rollback/containment: revert PR
- Observability: `updater:` log lines for guard decisions and sidecar stop
- Review map: single PR
- Split decision: N/A_SINGLE_BOUNDARY
- Coherent scope/refactor boundary: new module + registration; no change to existing command behavior
- Risk/reversibility: low-medium — ACL tests + contract test
- Verification evidence: green CI; no private key material in the diff
- Design dependencies: design/05 install flow, 06 guard states, 07 key custody + cleanup inventory
- Required authority subset: as AU-DEPS
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY

### AU-UPDATE-UI

- Repository/target/base: same, develop
- Outcome: `useAppUpdate` hook + `UpdateNotice` component mounted in `App.tsx`: check on launch + every 6 h; notice with version/notes; "나중에" hides 24 h; "지금 업데이트": UI busy check (tracked backend jobs) → `download` with progress → `update_install_guard` → `prepare_for_update` → `install` → `relaunch`; failed install → `backend_restart` + retry; Korean messages; new files added to `contract-test.mjs` required files; tests with a mocked plugin API.
- Acceptance subset: AC4
- Dependencies / next-unit condition: AU-UPDATER-SHELL MERGED / MERGED
- Owned paths: `frontend/src/**` (new hook/component + App.tsx mount), `frontend/scripts/contract-test.mjs` (append only)
- Shared contracts/resources: commands from AU-UPDATER-SHELL
- Affected boundaries: frontend UI only
- Deployment compatibility: no release → no notice
- Rollback/containment: revert PR
- Observability: logged check results; visible states
- Review map: single PR + design review on state screenshots
- Split decision: N/A_SINGLE_BOUNDARY
- Coherent scope/refactor boundary: new UI only
- Risk/reversibility: low
- Verification evidence: green CI + screenshots of Idle/Available/Downloading/refusal/Failed with a mocked endpoint
- Design dependencies: design/06, design/09 notice copy
- Required authority subset: as AU-DEPS
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY

### AU-RELEASE-WF

- Repository/target/base: same, develop
- Outcome: new `.github/workflows/release.yml`: workflow `permissions: contents: read`; `on: push: tags: ['v*']` + `workflow_dispatch` (never `pull_request`); job `verify-version` (tag equals tauri.conf.json, Cargo.toml, package.json, pyproject.toml); job `build-macos` (`macos-15-intel`, `permissions: contents: write`): uv sync → npm ci → `npm run build:backend-sidecar` → `tauri-apps/tauri-action@v1` on the same repo with `GITHUB_TOKEN`, `releaseDraft: true`, `uploadUpdaterJson: true`, `TAURI_SIGNING_PRIVATE_KEY[_PASSWORD]` from secrets; job `finalize` (`needs` all build jobs, `permissions: contents: write`): read the draft's `latest.json`, assert `darwin-x86_64` (list extended in sprint-2) with non-empty `signature`/`url`, then publish the draft. `scripts/bump_version.py` + tests; README release section (how to release, key-loss warning, first manual install into /Applications).
- Acceptance subset: AC5
- Dependencies / next-unit condition: AU-UPDATER-SHELL MERGED / MERGED; AC5 dry run additionally needs CP-KEY secrets + the owner's OK
- Owned paths: `.github/workflows/release.yml` (new), `scripts/bump_version.py` (new), `tests/test_bump_version.py` (new), `README.md` release section
- Shared contracts/resources: Actions secrets names; release tag format `v<x.y.z>`
- Affected boundaries: public release publication
- Deployment compatibility: runs only on the user's tag/dispatch
- Rollback/containment: revert PR; delete drafts
- Observability: Actions logs; release asset list
- Review map: single PR
- Split decision: N/A_SINGLE_BOUNDARY
- Coherent scope/refactor boundary: new workflow + script; `ci.yml` untouched
- Risk/reversibility: low (nothing runs until the user triggers it)
- Verification evidence: green CI (bump script tests); actionlint if available; AC5 dry-run log
- Design dependencies: design/05 release-publication, 07 publication permissions + versions
- Required authority subset: as AU-DEPS (release run itself is USER_ONLY)
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY

## Checkpoints (not eligible units)

### CP-KEY

- When: after AU-DEPS merges, before AU-UPDATER-SHELL starts.
- Steps: with the owner's go, Claude runs `tauri signer generate -w ~/.tauri/ssu-lecture-downloader.key` with a password the owner chooses; the owner backs up key file + password (password manager + offline) and confirms; then the two Actions secrets are set (owner, or Claude with the owner's explicit OK); Claude reads only the public key for AU-UPDATER-SHELL.
- Evidence recorded: "backup confirmed by owner <date>", secret names present (`gh secret list` names only). Never the key or password.

### CP-DOGFOOD (AC6)

- When: after AU-UPDATE-UI and AU-RELEASE-WF are MERGED and AC5 passed.
- Steps (owner): bump to N (e.g. 0.2.0) and publish; install N manually into /Applications while no auto-watch runs; bump to N+1 and publish; the running app shows the notice; click; app relaunches into N+1; check login, download, auto-watch, reminder; note any TCC prompts.
- Evidence: owner report + app log lines + installed `CFBundleShortVersionString`.
