# Development guide

How to run and verify the backend, frontend and desktop app locally. Architecture is in [architecture.md](architecture.md), the API in [api.md](api.md).

## Local setup

1. Copy `.env.example` to `.env` only for terminal CLI commands.
2. Fill in `SSU_USERNAME` and `SSU_PASSWORD` locally for CLI fallback. The API/UI login flow ignores `.env`, does not use Keychain by default, and keeps entered credentials only in the running backend process memory.
3. Do not commit `.env` or `.local/` artifacts.

## CLI commands

Login only:

```bash
uv run ssu-lecture-downloader login --trace .local/traces/login.json
uv run ssu-lecture-downloader login --trace .local/traces/login.json --json
```

Login and summarize LearningX courses/modules/lecture-like items:

```bash
uv run ssu-lecture-downloader discover-lms --trace .local/traces/discover-lms.json
uv run ssu-lecture-downloader discover-lms --trace .local/traces/discover-lms.json --json
```

`discover-lms` now defaults to the latest active/current term by scanning LearningX term ids and selecting courses where `ended=False`. Use repeated `--term-id` flags only when you intentionally want to inspect older completed terms, for example `--term-id 43 --term-id 40`.

Login and list lecture rows for one course so a UI/user can choose lectures without manually discovering raw module item ids:

```bash
uv run ssu-lecture-downloader course-detail --course-id 44070 --course-name 'UX방법론' --json
```

Resolve one Canvas module item to Commons download metadata without downloading bytes:

```bash
uv run ssu-lecture-downloader resolve-media --course-id 42045 --module-item-id 3206622 --trace .local/traces/resolve-media.json --json
```

This performs login, launches the LTI context, fetches LearningX/Commons metadata, and performs a HEAD request against the Commons download URL.

Plan a download path without writing bytes:

```bash
uv run ssu-lecture-downloader download --course-id 42045 --module-item-id 3206622 --course-name '데이터사이언스' --output-dir .local/downloads --dry-run --json
```

Download a lecture video to a selected output directory:

```bash
uv run ssu-lecture-downloader download --course-id 42045 --module-item-id 3206622 --course-name '데이터사이언스' --output-dir .local/downloads --trace .local/traces/download-live.json --json
```

`download` creates a safe course folder and filename, avoids overwriting existing files by adding numbered suffixes, streams bytes through a `.part` temp file, atomically renames the temp file only after success, reports byte-progress events, and resumes partial files with `Range` when Commons supports it. If a server ignores `Range`, the partial file is discarded and the download restarts from byte 0.

Plan selected lectures without writing bytes:

```bash
uv run ssu-lecture-downloader batch-download --course-id 44070 --course-name 'UX방법론' --module-item-id 3268578 --output-dir .local/downloads --dry-run --json
```

Plan or download all currently uncompleted lectures in a course:

```bash
uv run ssu-lecture-downloader batch-download --course-id 44070 --course-name 'UX방법론' --uncompleted --output-dir .local/downloads --dry-run --json
uv run ssu-lecture-downloader batch-download --course-id 44070 --course-name 'UX방법론' --uncompleted --output-dir .local/downloads --json
```

`batch-download` emits progress events (`resolving`, `planned`, `downloading`, `download_started`, `download_progress`, `download_done`, `done`, `failed`, `download_retrying`) in JSON and compact progress lines in human output. Repeat `--module-item-id` for multi-select downloads.

## Frontend and desktop app

Run the React/Vite prototype:

```bash
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:8000` for browser-only development. Inside Tauri, API calls use the dynamic backend base URL exposed by the desktop shell, so Tauri-managed backend ports do not have to be 8000. The prototype currently shows a session-first login/status panel, current-term courses, explicit current/past term selection from `/api/terms`, lecture rows, API server supervision status, automatic download folder default, native Finder folder picker in Tauri, primary background single-download start/cancel with 1.5s auto-polling, advanced path preview/diagnostic actions, selected-lecture batch planning/download, and uncompleted-lecture batch planning.

Run the Tauri desktop shell for app QA:

```bash
cd frontend
npm run tauri:dev
```

Tauri now manages the Python API automatically for desktop QA: it reuses an existing healthy `127.0.0.1:8000` backend when present, otherwise starts the backend on a free random localhost port and cleans up that owned child process when the app exits. In development, the fallback command is still `uv run uvicorn ...`; for distributable builds, build the bundled sidecar first:

```bash
cd frontend
npm run build:backend-sidecar
npm run tauri:build
```

`build:backend-sidecar` runs PyInstaller (`--onedir`, so there is no unpack-to-temp step on every launch) from the repo root and writes `frontend/src-tauri/resources/backend-sidecar/ssu-api/ssu-api` plus its `_internal/` directory, which are included as a Tauri bundle resource. A leftover single-file `backend-sidecar/ssu-api` from older builds is removed by the script. The sidecar contains the Python runtime/backend and packaged media dependencies such as `imageio-ffmpeg`; `faster-whisper` is bundled when target-compatible wheels are available for the build machine/target.

## Verification

```bash
uv run pytest -q
cd frontend && npm run test:contract && npm run build
cd frontend/src-tauri && cargo test
```

### CI

GitHub Actions (`.github/workflows/ci.yml`) runs on every pull request to `develop` and every push
to `develop`, on an Intel macOS runner (`macos-15-intel`, same architecture as the installed app):
`uv run pytest`, `uv run lint-imports`, `npm run typecheck`, `npm run test:contract`, the backend sidecar build,
`cargo test` and `npm run tauri build -- --bundles app` (no dmg, signing or upload).
`tests/test_macos_invariants.py` and `frontend/src-tauri/src/invariant_tests.rs` pin the macOS
behavior (app-data paths, LaunchAgent, Chrome argv, sidecar location, local-STT gate) that
platform-split changes must keep identical.

## Releasing

Releases are published only by the repository owner, from `.github/workflows/release.yml`. The workflow builds the Intel macOS app, signs the updater artifacts and uploads them to a **draft** GitHub release (drafts are invisible to `releases/latest/download/latest.json`, the updater endpoint in `frontend/src-tauri/tauri.conf.json`). A final job reads the draft's `latest.json` back, checks it, and only then publishes the release. Installed apps see the new version from that moment on. `ci.yml` is separate and never publishes anything.

Local builds are unaffected: `npm run tauri:build` stays unsigned and does not produce updater artifacts. `npm run tauri:build:release` signs and needs `TAURI_SIGNING_PRIVATE_KEY` (and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` if the key has a password); it is what the workflow's build corresponds to.

### Signing key (read this first)

The updater only installs updates signed by the private key that matches the public key committed in `tauri.conf.json` (`plugins.updater.pubkey`).

- **If the private key or its password is lost, installed apps can never update again.** They have to be reinstalled by hand from a build that carries a new public key. There is no recovery.
- Keep the key file and its password in two places (a password manager and an offline copy). Never commit them, paste them into an issue or a log, or put them in a PR. Only the public key belongs in the repository.
- The workflow reads two Actions secrets on `cafitac/ssu-lecture-downloader`: `TAURI_SIGNING_PRIVATE_KEY` (the key content) and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. Without both, the build fails in its first step.
- If the key may have leaked: generate a new key, release a version that carries the new public key while it is still signed with the old key, then replace the secrets.

### Publishing a version

1. Pick the next version `x.y.z`. It must be higher than the last published one (the updater only offers a strictly higher version). Pre-release suffixes are not supported.
2. Set it everywhere with one command, then review the diff and merge it to `develop` through a PR:

   ```bash
   python3 scripts/bump_version.py 0.2.0
   ```

   This edits `tauri.conf.json`, `Cargo.toml`, `package.json` and `pyproject.toml`, plus the project's own entry in `Cargo.lock`, `package-lock.json` and `uv.lock` so locked CI builds keep working. `--show` lists the current values and `--check 0.2.0` fails unless all of them agree.
3. Optional dry run. It builds and signs exactly like a release, uploads a draft, runs the `latest.json` checks, and stops before publishing:

   ```bash
   gh workflow run release.yml --repo cafitac/ssu-lecture-downloader --ref develop -f version=0.2.0 -f publish=false
   ```

   Open the draft under Releases and look for the `.dmg`, the `.app.tar.gz`, its `.sig` and `latest.json`. Then delete the draft on the Releases page; the real tag creates it again.
4. Tag the merged commit and push the tag. The tag pins the commit that is released:

   ```bash
   git tag v0.2.0 <commit-on-develop>
   git push origin v0.2.0
   ```

   The workflow runs `verify-version` (the tag must equal every version file; otherwise nothing is built), `build-macos` (sidecar first, then the signed Tauri build into a draft), and `finalize` (asserts that `latest.json` is for this version and that `platforms.darwin-x86_64` has a signature and a url, then publishes the draft as the latest release).
5. Check the run and the published release. Never re-use or move a tag that has been published.

`workflow_dispatch` with `publish` ticked also publishes, but prefer the tag so the release is tied to an exact commit. Dispatch runs for a version that has no tag yet create the tag on publish, pointing at the commit the run used.

If a run fails after the upload, the draft stays unpublished and clients never see it: delete it and re-run (or fix and tag again). If a published release turns out to be bad, un-publish or delete it (clients then fall back to the previous release) or publish a higher fixed version. The updater never downgrades.

### First install into /Applications

Apps installed before the updater existed cannot update themselves. Install the first updater-enabled release by hand, once:

1. Quit the app and make sure no auto-watch run is active.
2. Download the `.dmg` from the release, open it and drag `SSU Lecture Downloader.app` into `/Applications`, replacing the old copy. The updater needs the app to live there and to be writable by the user; an app run from the Downloads folder or the mounted DMG cannot replace itself.
3. The app is not notarized, so the first launch needs the Gatekeeper "Open anyway" step (see the README install section). macOS may ask again for app permissions after an update.

From then on, newer versions arrive through the in-app update chip.
