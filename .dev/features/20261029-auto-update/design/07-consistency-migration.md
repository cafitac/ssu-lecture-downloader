# Consistency, migration, and recovery

## Versions and identity

- Version is the release identity: one tag `v<x.y.z>` per release, never re-used or re-tagged. The updater compares semver of `latest.json.version` with the running app's `tauri.conf.json` version; only a strictly higher version is offered.
- Current: 0.1.0 in `tauri.conf.json`, `frontend/src-tauri/Cargo.toml`, `frontend/package.json`, `pyproject.toml` (CURRENT). `scripts/bump_version.py` (PROPOSED, AU-PR4) rewrites all four; `verify-version` job fails before any build on mismatch with the tag.
- Toolchain prerequisite (AU-PR1): tauri/tauri-build ≥ 2.12, `@tauri-apps/api` ^2.12, `@tauri-apps/cli` ^2.12, `rust-version = "1.90"`; plugins in AU-PR2: `tauri-plugin-updater` 2.14.x / `@tauri-apps/plugin-updater` 2.14.x, `tauri-plugin-process` 2.4.x / `@tauri-apps/plugin-process`.

## `latest.json` (plugin-defined, produced by tauri-action)

- Fields: `version`, `notes`, `pub_date`, `platforms.<key>.{signature,url}`; keys `darwin-x86_64` (sprint-1) and `windows-x86_64` (sprint-2; NSIS preferred via `updaterJsonPreferNsis`).
- Consistency rule: clients must never see a `latest.json` that lacks an expected platform or has an empty signature, because one bad entry fails the check for all clients. Enforced by draft upload + `finalize` assertion + publish (05 release-publication).
- Read path: `https://github.com/cafitac/ssu-lecture-downloader/releases/latest/download/latest.json` resolves to the newest published, non-prerelease release only; anonymous (public repo).

## Publication permissions

- `release.yml` is separate from `ci.yml`. Workflow-level `permissions: contents: read`; only `build-macos` (later `build-windows`) and `finalize` raise to `contents: write`, using the built-in `GITHUB_TOKEN`. No PAT exists.
- Triggers: `push: tags: ['v*']` and `workflow_dispatch` only — never `pull_request` (fork PRs on a public repo cannot reach the signing secrets or write releases).

## Key custody

- Generation (CP-KEY, Claude on the owner's Mac at the owner's go): `tauri signer generate -w ~/.tauri/ssu-lecture-downloader.key` with a password — outside any repository.
- Backup: the owner stores the key file and password (password manager + offline copy) and confirms before the secrets are set and before AU-PR2 merges the public key. Claude never prints, commits, logs, or records the private key or password; `.dev` records only that the backup was confirmed.
- CI: Actions secrets `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` on cafitac/ssu-lecture-downloader.
- Public repo rule: only the public key is committed; a secret scan of each PR diff is part of review.
- Loss/compromise: lost key → no further updates for installed apps (manual reinstall of a build with a new pubkey). Compromised key → rotate: new key, release a version carrying the new pubkey signed by the old key while it is still trusted, replace secrets. Documented in the README release section.

## Failure and recovery owners

| Failure | Owner | Behavior |
|---|---|---|
| Check fails (offline, 404 before first release, rate limit) | useAppUpdate | log, stay Idle, retry at next timer |
| Download / signature failure | updater plugin → UI | old app untouched; Failed → retry |
| Busy (watch, native download) | Rust `update_install_guard` (`src/update.rs`) | Refused with reason; nothing stopped |
| Busy (backend job) | UI busy check | refuse before download |
| Install fails after sidecar stop | UI Failed state | call existing `backend_restart` so the old app is usable, show retry |
| macOS swap blocked (app translocated / not in /Applications / no write permission) | updater plugin error → UI | Korean message "앱을 응용 프로그램 폴더로 옮긴 뒤 다시 시도"; README install guide |
| Version mismatch at release | `verify-version` | fail before build |
| Incomplete platform set | `finalize` | fail; draft stays unpublished |
| Bad published release | user | un-publish/delete it (clients fall back to the previous published `latest`) or publish a higher fixed version |

## Cleanup inventory

| Resource | Created by | Cleaned by |
|---|---|---|
| Sidecar process group | BackendManager (backend.rs) via `platform::configure_sidecar_command` | `prepare_for_update` → `BackendManager::shutdown()` → `platform::terminate_process_group` (macOS: `src/platform/macos.rs`; Windows: no-op stub until sibling S2); exit handler again on relaunch |
| Stale sidecar from a crashed run | — | existing `platform::reap_stale_backend` at next start (unchanged) |
| Auto-watch Chrome tree | watch_runtime | not touched — guard refuses while active; `watch_runtime::shutdown` on exit as today |
| Downloaded update bytes | updater plugin | plugin (in memory/temp); discarded on guard refusal |
| 6 h timer, 24 h dismiss window | useAppUpdate | cleared on unmount; dismiss window in memory only |
| Draft release from a failed run | tauri-action | user deletes or re-runs the same tag (documented); never visible to clients |
| Local private key file | CP-KEY | stays in `~/.tauri/` (outside repos) plus the owner's backup; never copied elsewhere |

## Migration (first install)

1. Existing installs (0.1.0, no updater) cannot update themselves. The first updater-enabled version N (proposed 0.2.0) is installed manually from the repo's Releases `.dmg` into `/Applications`, replacing the current app while no auto-watch runs (sibling R6).
2. App-data under `~/Library/Application Support/com.local.ssu-lecture-downloader` and the LaunchAgent are unchanged (same identifier, same path).
3. From N on, updates arrive in-app. Windows installs start with the first release that contains the NSIS build (sprint-2).

## Platform notes (research, partly inference)

- macOS ad-hoc/unsigned: the updater swaps the `.app` atomically and adds no quarantine flag; first manual install still needs the Gatekeeper "열기" step; TCC permissions may be re-requested after each update (cdhash changes) — measured at AC6.
- Windows NSIS currentUser + `installMode: passive`: no UAC; SmartScreen likely not shown on update; AV false positives possible. Sidecar stop before the installer is an inference to be verified in sprint-2 (O5); today the Windows `terminate_process_group` is a no-op stub.
- No delta updates: each update downloads the full bundle (sidecar included).

## Rollback

- Code: revert any PR on develop; installed apps are unaffected until a release is published.
- Releases: no downgrade through the updater (R12). Rollback = publish a higher fixed version, or un-publish the bad release so `latest` points to the previous one, or reinstall a known-good build manually.
