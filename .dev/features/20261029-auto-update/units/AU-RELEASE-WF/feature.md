# AU-RELEASE-WF

- Record schema: 4
- Status: IN_PROGRESS
- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-auto-update/feature.md
- Sprint manifest: ../../sprints/sprint-1-v2.md
- Repository: cafitac/ssu-lecture-downloader (public)
- Target/base: develop
- Base ref: origin/develop
- Base decision: repository default integration branch
- Initialization base SHA: 6e1ab43 (includes AU-UPDATER-SHELL, PR #8)
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/au-release-wf (feature/au-release-wf)
- Canonical record: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/au-release-wf/.dev/features/20261029-auto-update/units/AU-RELEASE-WF/feature.md
- Design status: APPROVED (parent)
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Worktree cleanup status: ACTIVE
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT

## Outcome

See ../../sprints/sprint-1-v2.md § "AU-RELEASE-WF". Acceptance subset: AC5.

## Workflow cursor

- Workflow stage: FEATURE_DEVELOP
- Next eligible action: CODE_POLISH (review cycle 1 repairs applied)
- Next action class: LOCAL_CONTINUE
- Gate state: NONE (AC5 dry run is pre-approved but runs only after merge, by the parent)

## Progress and evidence

- 2026-10-30: worktree on origin/develop 6e1ab43. Implemented locally (not pushed).
- `.github/workflows/release.yml` (new). Workflow `permissions: contents: read`; triggers `push: tags: v*` and `workflow_dispatch` (inputs `version`, `publish` default false); no `pull_request`. Concurrency group per version, no cancel.
  - `verify-version` (ubuntu): `scripts/bump_version.py --check <version>` against the tag (push) or the dispatch input; outputs `version` and `tag`. A mismatch fails before any build or upload.
  - `build-macos` (`macos-15-intel`, `contents: write`): fails fast if either signing secret is empty (values never printed); then the ci.yml steps (setup-uv 3.12, setup-node 22, rust toolchain + cache, `uv sync`, `npm ci`, `npm run build:backend-sidecar`, `scripts/smoke_backend_sidecar.py`), then `tauri-apps/tauri-action` with `GITHUB_TOKEN`, `releaseDraft: true`, `uploadUpdaterJson: true`, `projectPath: frontend`, signing secrets as step env only; then ci.yml's "sidecar is inside the app bundle" check. Uses the committed signed `tauri.conf.json`: no `no-updater.conf.json`, no `createUpdaterArtifacts` override. Outputs the draft `releaseId`.
  - `finalize` (ubuntu, `contents: write`): reads the draft by the release id (asserts draft and tag), lists its assets, downloads `latest.json`, asserts `version` equals the release version and `platforms.darwin-x86_64` has a non-empty `signature` and an https `url` inside this repository (both the github.com download form and the api.github.com asset form that tauri-action 1.x writes pass); then publishes (`draft=false`, `make_latest=true`) on a tag push or when dispatched with `publish` ticked. With `publish: false` the assertion still runs and the publish step is skipped (dry run, AC5).
  - Third-party action pin: `tauri-apps/tauri-action@1deb371b0cd8bd54025b384f1cd735e725c4060f` = release `action-v1.0.0` (published 2026-06-29, annotated tag `v1.0.0`). Other actions are the same major tags as ci.yml (`actions/checkout@v7`, `astral-sh/setup-uv@v7`, `actions/setup-node@v7`, `dtolnay/rust-toolchain@stable`, `Swatinem/rust-cache@v2`); the signing secrets are exposed only to the secret-check step and the tauri-action step.
- `scripts/bump_version.py` (new): `bump_version.py x.y.z` sets, `--check x.y.z` verifies, `--show` prints. Besides the four manifests it also updates the project's own entry in `Cargo.lock`, `frontend/package-lock.json` (2 fields) and `uv.lock`, because stale locks break `UV_LOCKED=1` and `cargo --locked` builds (verified on a throwaway copy bumped to 0.2.0: `uv lock --check`, `cargo metadata --locked` and `npm ci --dry-run` pass). Edits are textual (formatting preserved) and all-or-nothing; only plain `x.y.z` is accepted (a leading `v` is stripped). `verify-version` reuses `--check`.
- `tests/test_bump_version.py` (new, 19 cases): repository versions agree (drift guard), set on a copy of the real files changes only version lines, files stay parseable and dependency versions untouched, idempotence, per-file mismatch report, invalid versions, missing field/file aborts before any write, Cargo `[package]` scoping, CLI.
- Deviation, README release section: the sprint's owned path lists a `README.md` release section. README.md became a student-facing Korean page in PR #9, so release mechanics moved to `docs/development.md` § "Releasing" (English: steps, key-loss warning, key rotation, first manual install into /Applications, failure and rollback). README only gets one plain Korean sentence in the install section saying that after installing from Releases once, new versions arrive as in-app updates. `docs/development.md` and `README.md` therefore join the owned paths of this unit.
- Not changed: `.github/workflows/ci.yml`, application code, `tauri.conf.json` (no version bump here; the owner picks N).
- Static validation: actionlint 1.7.x (via actionlint-py, shellcheck integration unavailable locally) clean on release.yml and ci.yml; negative control (undefined `needs` output) is reported. The jq assertions were exercised locally on sample `latest.json` files (ok, api-url form ok, empty signature, missing platform, foreign url, wrong version).
- AC5 evidence still to collect after merge (pre-approved, draft only): dispatch `version=0.1.0 publish=false` on develop, check the Actions log and the draft's asset list (`.dmg`, `.app.tar.gz`, `.sig`, `latest.json`), dispatch a mismatching version (for example 9.9.9) and confirm `verify-version` refuses it, then delete the draft. Not run by this unit.
- Review cycle 1 repair batch (second commit):
  - P2 `tests/test_bump_version.py` no longer hardcodes 0.1.0: every test reads the copy's start version, and the `repo_copy` fixture runs both as committed and after a bump to 0.2.0 (verified by bumping the real tree to 0.2.0 and running the suite, then restoring).
  - P3 signing-job hardening: every action in release.yml is pinned to a commit at least two weeks old with a version comment (`actions/checkout` v7.0.1 `3d3c42e`, `astral-sh/setup-uv` v7.6.0 `37802ad`, `actions/setup-node` v7.1.0 `949feb2`, `dtolnay/rust-toolchain` `stable` branch commit of 2026-10-09 `e2a55d2` with `toolchain: stable`, `tauri-apps/tauri-action` action-v1.0.0 `1deb371`). Caches are off in the release workflow: `enable-cache: false`, no npm cache, `Swatinem/rust-cache` removed (so releases build Rust from scratch).
  - P3 README: the contradictory "no auto update yet" and the new line became one conditional Korean paragraph that also names /Applications.
  - P4 finalize: asserts `darwin-x86_64` and `darwin-x86_64-app` (tauri-action 1.0.0 writes both for a non-universal build), and compares the minisign key id inside each signature with the key id of `plugins.updater.pubkey` (new `scripts/check_updater_signature.py`, tests in `tests/test_check_updater_signature.py`). The decode was verified against signatures made with two throwaway keys generated with `tauri signer generate` (the real key was never used; the test file contains only the throwaway public key and signatures). A signature from the other key fails both platform entries.
  - P4 concurrency: one workflow-level group per normalized release tag (`push`: ref name; dispatch: `v`-prefixed input), so a tag push and a dispatch for the same version serialize.
  - P4 `VERSION_RE` uses `re.fullmatch` with `[0-9]` (rejects trailing newline and non-ASCII digits; tests added).
  - New owned paths: `scripts/check_updater_signature.py`, `tests/test_check_updater_signature.py`.
