# Sprint manifest: grades-view sprint-1 (G1: u-SAINT grades tab)

- Sprint ID: grades-view-sprint-1
- Sprint version: 1
- Created: 2026-10-29
- Expiry: 2026-11-12 (2 weeks; replan after expiry)
- Planning base: origin/develop @ 30c43a9 (S0-CI, S1-PY-MOVE, S1-RS-PLATFORM merged; read-only snapshot 2026-10-29)
- Base-drift policy: ALLOW_IF_DISJOINT — foreign commits (sibling windows-platform-split units, notably S1-PY-PLATFORMS #3 and later S2-PR7) may land on develop; before each reservation run `git diff <planning-base>..origin/develop --name-only` against the unit's owned paths; overlap → rebase and re-verify, never overwrite
- Outcome: opt-in "성적" tab that reads u-SAINT grades read-only through rusaint, keeps grade data in memory only, isolates failures to the tab, and ships inside the packaged sidecar. Ends at the owner's real-account check (AC7).
- Target user: the owner (Soongsil student, daily macOS user).
- Hypothesis: a read-only, consent-gated grades view can be added to the lecture companion without touching lecture features or leaking grade data, and the owner will actually use it.
- Non-goals: LMS in-progress scores (GR-LMS-SCORES, sprint-2), 학사 정보 (timetable, graduation, class_detail), Windows `.dll` packaging (sibling S2-PR7), any u-SAINT write, disk cache of grades.
- Requirements: R1, R2, R3, R4, R5, R6, R8 (feature.md)
- Acceptance IDs: AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC9 (design/08-acceptance-trace.md)
- Design profile: STANDARD
- Canonical design root: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-grades-view/design
- Design approval evidence: user 2026-10-29 AskUserQuestion "둘 다 승인 (추천)"; D5 memory-only "메모리만 (추천)"
- Design artifact-set SHA-256: dc1f211bdc850c656a2d0898c410976e668823763e49ef76cd47635080728c22

## Metrics

- Primary success metric: owner opens the grades tab on the real account and the summary + graded semesters match the u-SAINT web screen — evidence: AC7 owner report.
- Guardrail metric: zero lecture-feature regressions and zero grade values in backend.log / NetworkTrace / app-data files — evidence: CI suites + privacy suite (AC4) + owner grep of backend.log (AC7).
- Observation window: from the install of the first develop build containing all three units until the owner's AC7 answer.
- Comparison/decision threshold: any grade value found in a log/file or any lecture feature broken by a grades failure → stop, fix or revert before sprint-2.

## Eligible unit queue

- Eligible unit IDs: GR-PY-USAINT, GR-UI-TAB, GR-PKG-SIDECAR
- Dependency graph: [sibling S1-PY-PLATFORMS MERGED] → GR-PY-USAINT → GR-PKG-SIDECAR; GR-UI-TAB (no start dependency) merges only after GR-PY-USAINT MERGED; all three MERGED → AC7 owner checkpoint
- Selection/rejection rule: select the lowest-ordered READY unit; GR-UI-TAB runs in parallel with the backend lane against the frozen API contract (design/09 § API 계약); reject a merge of GR-UI-TAB while GR-PY-USAINT is not MERGED.
- Ownership / shared-resource exclusivity: backend lane owns `src/ssu_lecture_downloader/core/grades/**`, `src/ssu_lecture_downloader/service/routers/grades.py`, edits to `service/api.py`, `service/app_context.py`, `service/routers/credentials.py`, `service/api_errors.py`, `pyproject.toml`, `uv.lock`, `tests/grades/**`; GR-PKG-SIDECAR owns `scripts/build_backend_sidecar.py` and an appended smoke step in `.github/workflows/`; frontend lane owns `frontend/src/**` grades files and edits plus `frontend/scripts/contract-test.mjs`. The lanes share no file.
- Approved initial queue state: GR-UI-TAB = READY; GR-PY-USAINT = WAITING_DEPENDENCY (S1-PY-PLATFORMS #3); GR-PKG-SIDECAR = WAITING_DEPENDENCY.

## Delivery bundle

- N/A — every unit is a STANDALONE PR to develop, independently revertible.

## Review topology

- Review topology: CODE_POLISH_QUORUM
- Review quorum: 2
- Review scheduling: PARALLEL
- Review cycle budget: 3
- Unit review topology map: GR-PY-USAINT=INHERIT, GR-UI-TAB=INHERIT, GR-PKG-SIDECAR=INHERIT
- Unit review cycle budget map: GR-PY-USAINT=INHERIT, GR-UI-TAB=INHERIT, GR-PKG-SIDECAR=INHERIT
- Single-reviewer exception scope: N/A
- Single-reviewer exception rationale: N/A
- Single-reviewer exception approval: N/A

## Budgets

- Time budget: 2 weeks (expiry)
- Maximum-unit budget: 3
- WIP budget: 2 implementation lanes (backend lane + frontend lane), 3 lanes total including review
- Repair-pass budget: 3 per unit
- Same-failure budget: 2
- Retry budget: 3 (CI infra flakes; transient connection errors retried without asking)
- External-cost budget: GitHub Actions minutes within the free allowance (repo is now public); stop and ask if usage would exceed it.
- Consumed / remaining: tracked in the control record.

## Authority matrix

- LOCAL_READ: ALLOW
- LOCAL_RECORD_WRITE: ALLOW
- GIT_WORKTREE: ALLOW (dedicated worktrees only)
- LOCAL_IMPLEMENT_TEST: ALLOW (no real u-SAINT/LMS calls from tests; fakes and value-scrubbed fixtures only)
- COMMIT_PUSH: ALLOW (cafitac identity, feature branches only; committed text uses `~` instead of `/Users/<name>`; no real grade values, usernames, or credentials in commits, PRs, fixtures, or records)
- DRAFT_PR: ALLOW (cafitac/ssu-lecture-downloader → develop)
- TRACKER_SYNC: DENY (GitHub PRs are the tracker projection)
- REMOTE_TEST_RUNNER: DENY
- WORKTREE_CLEANUP: ALLOW (AUTO_AFTER_SAFE_TERMINAL only)
- Owner-only (DENY to agents): reinstalling or replacing the installed Mac app, the AC7 real-account check, any login to u-SAINT with the owner's account, releases or sharing builds.

## Finalization

- Delivery finalization: AUTO_AFTER_GATES
- Exact PR/bundle scope: the three unit PRs above, target develop
- Required gate set: review quorum 2 converged on the exact head; GitHub Actions green on the exact head (macos-15-intel: pytest, lint-imports, typecheck, contract test, cargo test, sidecar + tauri build with bundle check; windows cargo check); GR-UI-TAB additionally needs rendered screenshots of every grades state (light and dark) reviewed; GR-PKG-SIDECAR additionally needs the sidecar smoke `GET /api/grades/status` → `available: true`. No Feature-QA lane per unit; the runtime gate is the owner's AC7 check at the sprint boundary.
- Approved-scope evidence: user 2026-10-29 "모두 승인할게 / 그 방향으로 진행해줘" + design approval "둘 다 승인 (추천)"; this manifest itself awaits the user's confirmation.
- Merge: main task squash-merges into develop under the standing grant `merge-ssu-develop`, only after every gate above (GR-UI-TAB only after GR-PY-USAINT MERGED).

## Stop, recovery, completion

- Stop: a needed change to an approved design artifact (schema, API contract, error codes, ownership, privacy rule) → stop and reopen the Design Gate; any test showing a grade value in logs/trace/files; a grades failure that affects lecture features; same-failure budget exhausted.
- Pause: GitHub Actions unavailable or over budget; S1-PY-PLATFORMS blocked; user interrupts.
- Recovery: repair within the cycle budget; otherwise escalate.
- Rollback: revert the unit PR on develop; per user 설정 > 성적 보기 > 끄기; installed Mac app untouched until the owner reinstalls.
- Sprint-complete: all three units MERGED, the main task builds the macOS app from develop (no install) and presents the AC7 checklist to the owner.
- Sprint-boundary: sprint-2 (GR-LMS-SCORES, R7/AC8) is planned only after AC7 passes; 학사 정보 stays DEFERRED until the owner uses the tab.
- Mandatory human checkpoints: AC7 (owner installs the build when no auto-watch is running and checks on the real account).

## Unit details

### GR-PY-USAINT

- Repository/target/base: cafitac/ssu-lecture-downloader, develop, origin/develop containing S1-PY-PLATFORMS
- Outcome: `rusaint==0.16.3`; `core/grades/{models,gateway,mapper,service,consent}.py` (lazy rusaint import in gateway only; GradesService with consent check, 30-min memory snapshot, single-flight asyncio.Lock, wait_for timeouts, one fresh-login retry, code-only GradesError); `service/routers/grades.py` (status, consent PUT/DELETE, grades, classes; `Cache-Control: no-store`); wiring in `service/app_context.py` (consent path from `platform.app_data_dir()`, injected credentials provider) and `service/api.py`; `service/routers/credentials.py` clears grades on logout/changed pair; grades codes in `service/api_errors.py` outside AUTHENTICATION_ERROR_CODES; tests (privacy, isolation, cache, mapper, read-only guard).
- Acceptance subset: AC1 (backend), AC2 (backend), AC3, AC4, AC5 (backend), AC9
- Dependencies / next-unit condition: sibling S1-PY-PLATFORMS MERGED / MERGED unlocks GR-PKG-SIDECAR and the GR-UI-TAB merge
- Owned paths: `src/ssu_lecture_downloader/core/grades/**`, `src/ssu_lecture_downloader/service/routers/grades.py`, `src/ssu_lecture_downloader/service/{api,app_context,api_errors}.py`, `src/ssu_lecture_downloader/service/routers/credentials.py`, `pyproject.toml`, `uv.lock`, `tests/grades/**`
- Shared contracts/resources: HTTP API contract (design/09 § API 계약, frozen); import-linter contracts (core ↛ service/cli/platforms) must stay green
- Affected boundaries: new external authority (u-SAINT), one new app-data file, new routes, credentials router side effect
- Deployment compatibility: additive; sidecar without the native lib reports `available: false` and starts normally
- Rollback/containment: revert PR; leftover `grades-consent.json` ignored by older builds
- Observability: logger `ssu_lecture_downloader.grades` code-only events with elapsed_ms
- Review map: single PR; reviewers check privacy (no values in logs/errors/trace), read-only guard, error-code disjointness, async/timeout behavior, import contracts
- Split decision: STANDALONE (external-authority boundary; packaging split out)
- Coherent scope/refactor boundary: new package + minimal wiring; no edits to existing core modules
- Risk/reversibility: medium (third-party parser, async inside uvicorn — O3/O4 verified here)
- Verification evidence: green CI on exact head incl. the grades suite
- Design dependencies: design/02, 04, 05 Flows 1–5, 07
- Required authority subset: GIT_WORKTREE, LOCAL_IMPLEMENT_TEST, COMMIT_PUSH, DRAFT_PR, WORKTREE_CLEANUP
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY

### GR-UI-TAB

- Repository/target/base: same, develop
- Outcome: TopBar "성적" ghost toggle (visible from the start, same look as "기한"); App `view: 'course' | 'deadlines' | 'grades'`; `GradesPane` (consent, loading, ready with summary + semester groups + lazily loaded class table, empty, error "지금 가져올 수 없어요", refresh-failed line); `useGrades` hook (never calls onAuthError; 404 → unavailable; state reset on sign-out/revoke); api.ts/types.ts/errors.ts additions; Settings "성적 보기" section with "끄기"; contract-test entries (required files, consent sentence, label, identifier unchanged). Existing tokens/classes only; no grades in localStorage/console/toasts.
- Acceptance subset: AC1 (UI), AC2 (UI), AC5 (UI)
- Dependencies / next-unit condition: none to start (API contract frozen by the approved design) / merge only after GR-PY-USAINT MERGED; MERGED + GR-PKG-SIDECAR MERGED → AC7
- Owned paths: `frontend/src/components/GradesPane.tsx`, `frontend/src/hooks/useGrades.ts`, `frontend/src/lib/grades.ts`, edits to `frontend/src/App.tsx`, `frontend/src/components/TopBar.tsx`, `frontend/src/components/SettingsDialog.tsx`, `frontend/src/lib/{api,types,errors}.ts`, `frontend/src/styles/*.css`, `frontend/scripts/contract-test.mjs`
- Shared contracts/resources: design/09 § API 계약 (consumer side)
- Affected boundaries: React UI only
- Deployment compatibility: against an older backend the tab shows "지금 가져올 수 없어요"; lecture views untouched
- Rollback/containment: revert PR
- Observability: none new by design (no telemetry of grades)
- Review map: single PR; UI reviewer checks token/pane-pattern reuse, all states, keyboard/aria, Korean copy, screenshots light/dark
- Split decision: STANDALONE (separate lane after the API contract)
- Coherent scope/refactor boundary: generalize `deadlines-toggle` styling to a shared view-toggle class only if needed; no other refactor
- Risk/reversibility: low
- Verification evidence: green CI (typecheck, contract test, tauri build) + reviewed screenshots of each state against a fake backend
- Design dependencies: design/09 § 화면 구성, § API 계약; 05 Flows 1, 4, 5
- Required authority subset: as GR-PY-USAINT
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: READY

### GR-PKG-SIDECAR

- Repository/target/base: same, develop
- Outcome: `scripts/build_backend_sidecar.py` adds `rusaint` and the lazily imported `ssu_lecture_downloader.core.grades.gateway` to HIDDEN_IMPORTS and collects rusaint's native library (`--collect-binaries rusaint`, or `--collect-all rusaint` if required); CI appends a smoke step: start the built sidecar, `GET /api/grades/status` → `available: true` (no network, no consent); record the sidecar size delta in the PR.
- Acceptance subset: AC6
- Dependencies / next-unit condition: GR-PY-USAINT MERGED / MERGED + GR-UI-TAB MERGED → AC7 owner checkpoint
- Owned paths: `scripts/build_backend_sidecar.py`, one appended smoke step in `.github/workflows/ci.yml`
- Shared contracts/resources: build script also edited later by sibling S2-PR7 (Windows) — whichever lands second rebases (ALLOW_IF_DISJOINT)
- Affected boundaries: runtime artifact (sidecar contents and size)
- Deployment compatibility: sidecar only grows; no behavior change outside grades
- Rollback/containment: revert PR; grades tab degrades to "지금 가져올 수 없어요"
- Observability: CI artifact listing + smoke log
- Review map: single PR; reviewer checks the collected file list, ad-hoc signing of the dylib inside the .app (bundle check), size delta
- Split decision: STANDALONE (runtime-artifact boundary)
- Coherent scope/refactor boundary: build script + CI step only
- Risk/reversibility: low-medium (O5: PyInstaller collection, signing)
- Verification evidence: green CI incl. smoke on exact head
- Design dependencies: design/07 § Packaging
- Required authority subset: as GR-PY-USAINT
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Initial queue status: WAITING_DEPENDENCY

### AC7 owner checkpoint (not a unit)

- Trigger: GR-PY-USAINT, GR-UI-TAB, GR-PKG-SIDECAR all MERGED; main task builds the macOS app from develop (no install).
- Owner steps: install when no auto-watch runs; open 성적 → consent → compare summary and graded semesters with the u-SAINT web; expand one semester; refresh; Wi-Fi off → only the grades tab shows "지금 가져올 수 없어요" while lecture views work; grep backend.log for own GPA/class names (expect none); 설정 > 성적 보기 > 끄기 removes `grades-consent.json`.
- Evidence: owner report + screenshots with values blurred; real values never recorded.
- Outcome: pass → sprint-2 (GR-LMS-SCORES) planning; fail → fix/revert within this sprint.
