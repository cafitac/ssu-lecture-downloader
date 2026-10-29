# S1-RS-PLATFORM

- Record schema: 4
- Status: IN_PROGRESS
- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-windows-platform-split/feature.md
- Sprint ID: windows-platform-split-sprint-1
- Sprint manifest: ../../sprints/sprint-1.md
- Sprint manifest SHA-256: 1388772957c092e529131b0b0a23f7813b818e25551613490033ee4c56d67c04
- Repository: cafitac/ssu-lecture-downloader
- Target/base: develop
- Base ref: origin/develop
- Base decision: repository default integration branch
- Initialization base SHA: 0dfab558429420b0e48c73d59a652cc5632c5ae0
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/s1-rs-platform (feature/s1-rs-platform)
- Canonical record: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/s1-rs-platform/.dev/features/20261029-windows-platform-split/units/S1-RS-PLATFORM/feature.md
- Design status: APPROVED (parent, sha256 f0c28b02634270935586ad670eee0e755c932220f196f6a229604b932557c8a6)
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Worktree cleanup status: ACTIVE
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT

## Outcome

See sprint-1.md § "S1-RS-PLATFORM".

## Workflow cursor

- Workflow stage: FEATURE_DEVELOP
- Next eligible action: FEATURE_DEVELOP
- Next action class: LOCAL_CONTINUE
- Gate state: NONE

## Progress and evidence

- 2026-10-29: worktree created from origin/develop 0dfab55 (S0-CI merged).
- 2026-10-29: implemented locally (not pushed; CI budget decision pending).
  - `frontend/src-tauri/src/platform/{mod.rs,macos.rs,windows.rs}`: cfg(unix) selects macos.rs (bodies moved verbatim), cfg(windows) selects compile-only stubs. `launch_agent.rs` moved (git mv, unchanged) to `platform/macos/launch_agent.rs`.
  - Shared code (`lib.rs`, `backend.rs`, `reminders.rs`, `watch_runtime.rs`) calls `crate::platform::*`; `libc` is now `[target.'cfg(unix)'.dependencies]`.
  - Shell passes `SSU_APP_DATA_DIR` (= Tauri app data dir = `~/Library/Application Support/com.local.ssu-lecture-downloader` on macOS) to the sidecar; pinned by `invariant_tests` and the fake-backend e2e test. Python ignores it.
  - Tripwires moved: process_group / killpg assertions now read `platform/macos.rs` (production part); backend.rs assertions kept, `configure_sidecar_command(&mut command);` added.
  - CI: appended `windows` job (windows-latest, `cargo check --lib`, empty sidecar placeholder).
  - Local: `cargo test` 156 passed (3 new tests; baseline count not re-measured); `cargo clippy` only the pre-existing `native_download.rs:405` type_complexity warning; `npm run typecheck` clean. No Windows target installed locally (no rustup); windows.rs was type-checked once by temporarily selecting it on macOS.
  - Not verified locally: a real Windows `cargo check` (left to CI).
