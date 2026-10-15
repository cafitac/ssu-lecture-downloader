# D12 download progress UX polish

Status: completed

User feedback:

- The download area should feel less developer-oriented.
- Pressing `다운로드 시작` should show a progress-bar style status.
- Start and cancel can be one primary button instead of separate buttons.
- The default download folder should be `~/Downloads`, not `~/Downloads/ssu-lectures`.
- After completion, opening the downloaded folder is more valuable than exposing advanced diagnostics.

Product decisions:

1. Default destination is the OS Downloads folder directly.
   - Browser fallback: `~/Downloads`.
   - Tauri runtime: Rust `real_download_dir` command resolves the macOS login user's Downloads folder via `getpwuid(getuid())`, so Hermes profile HOME cannot redirect it to `~/.hermes/profiles/.../home/Downloads`.
2. The default download action is a single primary button.
   - Idle/finished: `다운로드 시작`.
   - Active job: `취소하기`.
3. User-facing progress is a compact card.
   - Hidden before the user starts an actual download, so the idle screen does not show an empty `0%` progress state.
   - After a background job/result exists, shows Korean status label, percent, progress bar, current friendly stage, and save location.
   - Developer fields such as job id, raw status, dry_run, and raw progress stages are not shown by default.
4. Completion action is user-centered.
   - Completed downloads show `다운로드 폴더 열기` in the progress card.
   - Tauri exposes `open_path_in_finder`; when given a downloaded file path it opens the parent folder in Finder.
   - The single-download `고급 정보` section was removed from the default UI because job id/raw status/dry-run fields are not useful after success.

Verification completed:

- `cd frontend && npm run test:contract && npm run build` => passed.
- `cd frontend/src-tauri && cargo test && cargo check` => passed, including regression coverage that the resolved Downloads path ends with `/Downloads` and does not contain `/.hermes/profiles/`, plus a downloaded-file path opens its parent folder.
- Browser smoke at `http://127.0.0.1:5173` confirmed:
  - default download field value `~/Downloads`,
  - visible `다운로드 시작` single primary action before starting,
  - no idle `다운로드 진행률` card/progressbar before starting a download,
  - no single-download `고급 정보` section in the default DOM,
  - horizontal overflow `0`.

Deferred after D12:

1. Actual Tauri-window Finder picker manual QA remains user-side because the native dialog cannot be driven from browser automation.
2. Batch download should eventually use the same background-job/progress/cancel UX model as single download.
3. Tauri should start/monitor the Python FastAPI backend itself.
