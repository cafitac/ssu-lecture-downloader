# AU-DEPS

- Record schema: 4
- Status: IN_PROGRESS
- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-auto-update/feature.md
- Sprint manifest: ../../sprints/sprint-1.md
- Repository: cafitac/ssu-lecture-downloader (public)
- Target/base: develop
- Base ref: origin/develop
- Base decision: repository default integration branch
- Initialization base SHA: de384cedd14eb20426742c32a4b135eec58a35d1
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/au-deps (feature/au-deps)
- Canonical record: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/au-deps/.dev/features/20261029-auto-update/units/AU-DEPS/feature.md
- Design status: APPROVED (parent)
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Worktree cleanup status: ACTIVE
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT

## Outcome

See ../../sprints/sprint-1.md § "AU-DEPS".

## Workflow cursor

- Workflow stage: FEATURE_DEVELOP
- Next eligible action: FEATURE_DEVELOP
- Next action class: LOCAL_CONTINUE
- Gate state: NONE

## Progress and evidence

- 2026-10-29: worktree created from origin/develop de384ce.
- 2026-10-29: tauri 2.11.2 -> 2.12.5, tauri-build 2.6.2 -> 2.7.4, @tauri-apps/api/cli 2.11.x -> 2.12.4, rust-version 1.77.2 -> 1.90 (rustc 1.95.0). No src/** change needed. Local: cargo test 156 passed, 0 warnings; typecheck + test:contract ok. Windows `cargo check --lib` is CI-only.
- 2026-10-29: actual base is origin/develop de384ce (includes S1-PY-PLATFORMS; manifest planning base 30c43a9 — disjoint drift). Review quorum 2 CLEAN on 15d83d5 (A: versions/lock consistent, no macOS behavior change found by source diff of tray-icon 0.25 / tao 0.37 / muda 0.20; B: base vs head `cargo test --lib -- --list` byte-identical, 156 tests; tauri 2.11.2→2.12.5 signatures used by the app unchanged; no new default permissions). Residual runtime risk (not covered by automated tests): tray menu left/right click, menu-bar title, Dock badge count, unfocused bottom-right watch window, folder dialog — verify at the next owner macOS checkpoint (CP-DOGFOOD at the latest).
