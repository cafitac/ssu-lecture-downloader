# GR-PY-USAINT

- Record schema: 4
- Status: IN_PROGRESS
- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-grades-view/feature.md
- Sprint manifest: ../../sprints/sprint-1.md
- Repository: cafitac/ssu-lecture-downloader (public)
- Target/base: develop
- Base ref: origin/develop
- Base decision: repository default integration branch
- Initialization base SHA: de384cedd14eb20426742c32a4b135eec58a35d1
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/gr-py-usaint (feature/gr-py-usaint)
- Canonical record: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/gr-py-usaint/.dev/features/20261029-grades-view/units/GR-PY-USAINT/feature.md
- Design status: APPROVED (parent)
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Worktree cleanup status: ACTIVE
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT

## Outcome

See ../../sprints/sprint-1.md § "GR-PY-USAINT".

## Workflow cursor

- Workflow stage: FEATURE_DEVELOP
- Next eligible action: FEATURE_DEVELOP
- Next action class: LOCAL_CONTINUE
- Gate state: NONE

## Progress and evidence

- 2026-10-29: worktree created from origin/develop de384ce.
- 2026-10-29: implemented in 0cd62ff (core/grades, routes, wiring, 111 grades tests); pytest 640 passed, lint-imports 4 kept.
- 2026-10-30: review cycle 1 repair (new commit on 0cd62ff): generation captured at request entry and re-checked after the flight lock and before every store/retry (account switch, logout, revoke); credentials router clears again after save; revoke unlinks before clear; single-flight futures share success and failure; `grades_login_rejected` remembered per (account, generation); gateway work runs on a dedicated worker thread (O4 fallback); classes share the snapshot lifetime; one fresh-login retry on any transient failure (never on a rejection); `invalid_year` / malformed consent body return the standard 400 shape. pytest 674 passed, lint-imports 4 kept.
- Open: O3 field semantics (credits/grade/rank order) and O4 (real rusaint loop behavior) verified at AC7. O7 OPEN: rusaint 0.16.3 has one error type (`RusaintError.General`); its library strings ("Failed to login with ssu sso: ", "Token is not included in response: ", "login failed: session ID not assigned, got " and "OZ login failed: " from the OZ report engine, ...) do not identify a wrong password, so every real login failure maps to `grades_unavailable`; the rejection path and its memory exist for a future confirmed signal. Account-lock risk from repeated retries with a wrong u-SAINT password is therefore not mitigated by the memory until O7 is closed on the owner's real account (AC7).
- 2026-10-30: review cycle 2 repair (new commit on a39d02c): a failed login step is never retried (retry only for an operation that failed on a reused session); 2 consecutive failed logins per (account, generation) pause all logins for 5 minutes or until clear(), reset on a successful login (injected clock); API-level event-loop test now asserts completion before t0+0.8 s with a ticker and a non-test thread (verified to FAIL when GatewayWorker.run is replaced by `return await coroutine`); lifespan closes the grades worker via run_in_threadpool, and GatewayWorker.close cancels pending tasks and shuts down async generators before stopping. pytest 684 passed, lint-imports 4 kept; repro/probe/probe2: one wrong password = at most 2 logins per window (3 clicks -> 2 logins). O7 still OPEN.
- 2026-10-30: review quorum 2 CLEAN on b961e63 (cycle 3/3). O7 open (rejection undetectable; login backoff bounds SSO attempts to 2 per 5 min); O3/O4 to confirm at AC7.
