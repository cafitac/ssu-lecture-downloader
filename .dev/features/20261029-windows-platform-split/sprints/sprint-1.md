# Sprint manifest: windows-platform-split sprint-1 (S0 + S1, macOS-only refactor)

- Sprint ID: windows-platform-split-sprint-1
- Sprint version: 1
- Created: 2026-10-29
- Expiry: 2026-11-12 (2 weeks; replan after expiry)
- Planning base: origin/develop @ 048c35f5684c3463d80e75ae62ff7b1082743330 (fetched read-only 2026-10-29 immediately before this snapshot)
- Base-drift policy: ALLOW_IF_DISJOINT — only this sprint's own units write develop; any foreign commit must not touch the unit's owned paths (revalidation evidence: `git diff <planning-base>..origin/develop --name-only` against the unit's owned paths before reservation)
- Outcome: CI safety net plus the behavior-preserving split into Python `core`/`platforms`/`service` and a Rust `platform` module, macOS adapter only; Windows stubs compile. Ends at the user's Mac check (AC2).
- Target user: the owner (daily macOS user); Windows users come in sprint-2.
- Hypothesis: the macOS app can be restructured without any behavior change when every move is pinned by characterization tests and an Intel-Mac CI build on the exact head.
- Non-goals: Windows adapter behavior, Windows packaging, UI changes, signing, Apple-Silicon/universal build.
- Requirements: R1, R2, R3, R6, R7 (feature.md)
- Acceptance IDs: AC1, AC2, AC3, AC4 (design/08-acceptance-trace.md)
- Design profile: STANDARD
- Canonical design root: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-windows-platform-split/design
- Design approval evidence: user 2026-10-29 "일단 다 승인할게 진행해줘"
- Design artifact-set SHA-256: f0c28b02634270935586ad670eee0e755c932220f196f6a229604b932557c8a6

## Metrics

- Primary success metric: macOS characterization + full suites green on every merged head — evidence: GitHub Actions `macos-15-intel` run per PR head.
- Guardrail metric: zero macOS behavior regressions at the user check — evidence: AC2 user report (login, course list, one download, one auto-watch, reminder toggle).
- Observation window: from the S1 build install until the user's AC2 answer.
- Comparison/decision threshold: any AC2 difference → stop sprint-2 admission, fix or revert first.

## Eligible unit queue

- Eligible unit IDs: S0-CI, S1-PY-MOVE, S1-PY-PLATFORMS, S1-RS-PLATFORM
- Dependency graph: S0-CI → S1-PY-MOVE → S1-PY-PLATFORMS; S0-CI → S1-RS-PLATFORM (independent of the Python units)
- Selection/rejection rule: select the lowest-ordered unit whose predecessors are MERGED; S1-RS-PLATFORM may run in parallel with S1-PY-MOVE / S1-PY-PLATFORMS.
- Ownership / shared-resource exclusivity: Python units own `src/`, `tests/`, `scripts/build_backend_sidecar.py`, `scripts/ssu_api_sidecar.py`, `pyproject.toml`; the Rust unit owns `frontend/src-tauri/` (src, Cargo.toml, tauri.conf.json). `.github/workflows/` is owned by S0-CI only; later units may add steps only by appending a job step they need (sequential with S0-CI already merged). The two lanes share no file.
- Approved initial queue state: S0-CI = READY; others = WAITING_DEPENDENCY.

## Delivery bundle

- N/A — every unit is a STANDALONE PR to develop, independently revertible.

## Review topology

- Review topology: CODE_POLISH_QUORUM
- Review quorum: 2
- Review scheduling: PARALLEL
- Review cycle budget: 3
- Unit review topology map: S0-CI=INHERIT, S1-PY-MOVE=INHERIT, S1-PY-PLATFORMS=INHERIT, S1-RS-PLATFORM=INHERIT
- Unit review cycle budget map: S0-CI=INHERIT, S1-PY-MOVE=INHERIT, S1-PY-PLATFORMS=INHERIT, S1-RS-PLATFORM=INHERIT
- Single-reviewer exception scope: N/A
- Single-reviewer exception rationale: N/A
- Single-reviewer exception approval: N/A

## Budgets

- Time budget: 2 weeks (expiry)
- Maximum-unit budget: 4
- WIP budget: 2 implementation lanes (Python lane + Rust lane), 3 lanes total including review
- Repair-pass budget: 3 per unit
- Same-failure budget: 2
- Retry budget: 3 (CI infra flakes)
- External-cost budget: GitHub Actions minutes within the free private-repo allowance; stop and ask if a month's usage would exceed it.
- Consumed / remaining: tracked in the control record.

## Authority matrix

- LOCAL_READ: ALLOW
- LOCAL_RECORD_WRITE: ALLOW
- GIT_WORKTREE: ALLOW (dedicated worktrees only)
- LOCAL_IMPLEMENT_TEST: ALLOW
- COMMIT_PUSH: ALLOW (cafitac identity, feature branches only)
- DRAFT_PR: ALLOW (cafitac/ssu-lecture-downloader → develop)
- TRACKER_SYNC: DENY (GitHub PRs are the tracker projection; no separate tracker)
- REMOTE_TEST_RUNNER: DENY
- WORKTREE_CLEANUP: ALLOW (AUTO_AFTER_SAFE_TERMINAL only)

## Finalization

- Delivery finalization: AUTO_AFTER_GATES
- Exact PR/bundle scope: the four unit PRs above, target develop
- Required gate set: review quorum 2 converged on the exact head; GitHub Actions green on the exact head (macOS characterization + pytest + cargo test + typecheck + contract test + tauri build; plus Windows `cargo check` job once S1-RS-PLATFORM adds it); no runtime/Feature-QA lane for S0/S1 units (the runtime gate is the human AC2 check at the sprint boundary).
- Approved-scope evidence: user 2026-10-29 "승인 범위 안에서 자율" + "Claude가 자동으로 (추천)" merge choice + "일단 다 승인할게 진행해줘"
- Merge: main task merges into develop under the standing grant `merge-ssu-develop` (squash), only after every gate above.

## Stop, recovery, completion

- Stop: any macOS characterization test needs changing to pass (= behavior change) → stop and ask; same-failure budget exhausted; design boundary change needed.
- Pause: GitHub Actions unavailable or over budget; user interrupts.
- Recovery: repair within the cycle budget; otherwise escalate.
- Rollback: revert the unit PR on develop; installed Mac app untouched until the user reinstalls; known-good dmg on the Desktop.
- Sprint-complete: all four units MERGED, then the main task builds the macOS app from develop (no install) and presents AC2 to the user.
- Sprint-boundary: sprint-2 (Windows adapters + packaging) is replanned after AC2 passes.
- Mandatory human checkpoints: AC2 (install + check) at the boundary; reinstalling /Applications app is user-only.

## Unit details

### S0-CI

- Repository/target/base: cafitac/ssu-lecture-downloader, develop, origin/develop@048c35f
- Outcome: `.github/workflows/ci.yml` on `macos-15-intel` running `uv run pytest`, `cargo test` (frontend/src-tauri), `npm run typecheck`, `npm run test:contract`, sidecar build + `npm run tauri build` (no upload); characterization tests pinning macOS invariants 1–5 (design/07): app-data paths, LaunchAgent label/plist, Chrome argv, sidecar path/name, local-STT predicate.
- Acceptance subset: AC1 (baseline)
- Dependencies / next-unit condition: none / MERGED unlocks S1 units
- Owned paths: `.github/workflows/**`, new `tests/test_macos_invariants.py`, new Rust test module for invariants (e.g. `frontend/src-tauri/src/invariant_tests.rs` + `mod` line in lib.rs under `#[cfg(test)]`)
- Shared contracts/resources: lib.rs gets one `#[cfg(test)] mod` line only
- Affected boundaries: CI only, tests only
- Deployment compatibility: no runtime change
- Rollback/containment: revert PR
- Observability: CI logs; slowest-tests report (`pytest --durations=15`)
- Review map: single PR
- Split decision: N/A_SINGLE_BOUNDARY
- Coherent scope/refactor boundary: no production code changes
- Risk/reversibility: low
- Verification evidence: green CI on the PR head
- Design dependencies: 07 § macOS invariants
- Required authority subset: GIT_WORKTREE, LOCAL_IMPLEMENT_TEST, COMMIT_PUSH, DRAFT_PR, WORKTREE_CLEANUP
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: READY

### S1-PY-MOVE

- Repository/target/base: same, develop
- Outcome: create `src/ssu_lecture_downloader/core/` and `service/`; move modules per design/02 (service.py → core/lms_service.py; api, api_models, api_errors, payloads, app_context, security, dependency_installer, routers → service/); `cli.py` stays at root; update imports, tests, `scripts/ssu_api_sidecar.py`, PyInstaller hidden imports; add import-linter contract (core must not import service) + pytest guard. Pure moves, no logic edits.
- Acceptance subset: AC1, AC3 (core↛service part)
- Dependencies / next-unit condition: S0-CI MERGED / MERGED unlocks S1-PY-PLATFORMS
- Owned paths: `src/**`, `tests/**`, `scripts/*.py`, `pyproject.toml`, `uv.lock`, README command references
- Shared contracts/resources: Python package import paths (internal only); HTTP API unchanged
- Affected boundaries: Python package layout
- Deployment compatibility: same sidecar behavior; app-data untouched
- Rollback/containment: revert PR
- Observability: CI + characterization tests
- Review map: single PR (mechanical move; reviewers verify `git diff -M` shows renames + import edits only)
- Split decision: N/A_SINGLE_BOUNDARY
- Coherent scope/refactor boundary: moves + import rewrites only
- Risk/reversibility: low-medium (PyInstaller hidden imports) — covered by CI sidecar build
- Verification evidence: green CI incl. sidecar build + tauri build
- Design dependencies: design/02 P1
- Required authority subset: as S0-CI
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY

### S1-PY-PLATFORMS

- Repository/target/base: same, develop
- Outcome: `platforms/` package (`base.py` Protocols AppPaths, ChromeLocator, ProcessTree, SttCapability, SecretFileWriter; `macos.py` with code moved verbatim from chrome_watch.py, stt_remote_config.py, stt.py:82, credentials.py keychain; `windows.py` placeholder raising NotImplementedError; `select_platform()` honoring `SSU_APP_DATA_DIR` with the identical macOS fallback); core receives adapters by injection; only `service/app_context.py` and `cli.py` import `platforms`; import-linter contract core↛platforms.
- Acceptance subset: AC1, AC3
- Dependencies / next-unit condition: S1-PY-MOVE MERGED / MERGED + S1-RS-PLATFORM MERGED → sprint boundary
- Owned paths: `src/**`, `tests/**`, `pyproject.toml`
- Shared contracts/resources: env var `SSU_APP_DATA_DIR` (read here, written by S1-RS-PLATFORM; absent → identical fallback, so order-independent)
- Affected boundaries: Python platform seam
- Deployment compatibility: macOS paths/argv/kill sequence identical (characterization tests)
- Rollback/containment: revert PR
- Observability: CI
- Review map: single PR
- Split decision: N/A_SINGLE_BOUNDARY
- Coherent scope/refactor boundary: extraction + injection only; macOS bodies verbatim
- Risk/reversibility: medium — characterization tests are the guard
- Verification evidence: green CI
- Design dependencies: design/02, 05 Flow 1–2, 07 step 3
- Required authority subset: as S0-CI
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY

### S1-RS-PLATFORM

- Repository/target/base: same, develop
- Outcome: `frontend/src-tauri/src/platform/{mod.rs,macos.rs,windows.rs}`: move LaunchAgent (launch_agent.rs), osascript notify, killpg/process_group, `open` path/URL, real-home, unix lock, tray title/Dock badge, sidecar executable name behind cfg modules with macOS bodies verbatim; Windows module = compile-only stubs returning errors/no-ops; `libc` → `[target.'cfg(unix)'.dependencies]`; shell passes `SSU_APP_DATA_DIR` to the sidecar (same macOS value); CI adds a `windows-latest` `cargo check` (and `cargo test --lib` where tests are platform-neutral) job.
- Acceptance subset: AC1, AC4
- Dependencies / next-unit condition: S0-CI MERGED / MERGED → sprint boundary (with S1-PY-PLATFORMS)
- Owned paths: `frontend/src-tauri/**`, `.github/workflows/ci.yml` (append Windows cargo job only)
- Shared contracts/resources: env var `SSU_APP_DATA_DIR` (see above)
- Affected boundaries: Rust platform seam
- Deployment compatibility: macOS behavior identical (Rust invariant tests from S0)
- Rollback/containment: revert PR
- Observability: CI
- Review map: single PR
- Split decision: N/A_SINGLE_BOUNDARY
- Coherent scope/refactor boundary: moves behind cfg + stubs; no behavior change on macOS
- Risk/reversibility: medium — Rust invariant tests + tauri build guard
- Verification evidence: green CI (macOS + Windows cargo check)
- Design dependencies: design/02 PlatformShell, 05 Flow 1/3, 07 step 4
- Required authority subset: as S0-CI
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY
