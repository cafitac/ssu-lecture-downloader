# Design index

- Feature record: ../feature.md
- Feature ID: 20261029-grades-view
- Current slice: G1 (u-SAINT grades tab); G2 (LMS in-progress scores, SHOULD) designed coarsely
- Current PR unit: NONE (proposed GR-PY-USAINT, GR-PKG-SIDECAR, GR-UI-TAB, then GR-LMS-SCORES)
- Design profile: STANDARD
- Design status: AWAITING_APPROVAL
- Target/base: develop (planning base 048c35f5684c3463d80e75ae62ff7b1082743330; implementation base must contain merged sibling S1-PY-MOVE + S1-PY-PLATFORMS)

## Source evidence

- Code survey 2026-10-29 (develop @ 048c35f, read-only):
  - `src/ssu_lecture_downloader/credentials.py:155-161` — `default_credential_store()` returns `InMemoryCredentialStore`; credentials never touch disk today.
  - `routers/credentials.py:37-57` — `POST /api/credentials` clears service caches when the pair changes, `DELETE` clears them on logout (the hook point for clearing grades).
  - `trace.py`, `redaction.py` — `NetworkTrace` records only the app's own curl_cffi LMS requests; rusaint traffic (Rust HTTP client) never reaches it.
  - `api.py:56-108` — routers are attached from `build_router(ctx)` modules; `UnhandledErrorMiddleware` (`api_errors.py:196`) logs escaped exceptions through `logging` (traceback text can contain exception messages).
  - `frontend/src-tauri/src/backend.rs:30, 299-317` — sidecar stdout/stderr go to `backend.log` in the app log dir: anything Python logs or prints persists on disk.
  - `scripts/build_backend_sidecar.py:34-50` — PyInstaller onedir with an explicit `HIDDEN_IMPORTS` list; native libs of new packages must be collected.
  - `frontend/src/App.tsx:51, 301-345` — `view: 'course' | 'deadlines'`; `TopBar.tsx` ghost toggle `deadlines-toggle` with `aria-pressed`; `DeadlinesPane.tsx` pane pattern (pane-head/pane-title/pane-sub, note line, loading/error/empty `pane-empty`, groups with `week-count`).
  - `frontend/src/lib/api.ts:40-45` — `isAuthenticationError` by code set; `useDeadlines` calls `onAuthError` (signs the user out) for those codes — grades codes must stay outside that set.
  - `frontend/src/styles/tokens.css` — the only design system (neutral grays, one blue accent, success/warning/danger tints, 4px spacing, fs 12–20). No DESIGN.md in the repo.
  - `frontend/src-tauri/tauri.conf.json:3-5` — productName "SSU Lecture Downloader", identifier `com.local.ssu-lecture-downloader`.
- Sibling design `../../20261029-windows-platform-split/design/` (APPROVED): Python moves to `core/` (no OS calls), `platforms/` (AppPaths etc.), `service/` (FastAPI, `app_context.py` composition root); Rust passes `SSU_APP_DATA_DIR`; macOS app-data root `~/Library/Application Support/com.local.ssu-lecture-downloader`.
- rusaint 0.16.3 (PyPI, MIT, UniFFI; wheels macosx_10_12_x86_64, win_amd64; unofficial, single maintainer). API and the owner's read-only probe as summarized in the feature brief (login ok; 3 graded semesters; current term absent until posted; summaries ok; 9 classes per semester with grade_points/score/rank filled).
- LMS Canvas in-progress scores: reachable with the existing cookie session, not the `xn_api_token` bearer (401). Data sparse, no letter grades, `final_score` counts ungraded as 0.

## Artifact ledger

| Artifact | Status | Evidence / reason |
|---|---|---|
| Context map | N/A | One new external system (u-SAINT) behind one gateway; no bounded-context ownership changes. The external boundary is drawn in the review-pack target overview. |
| Domain model | REQUIRED | `02-domain-model.puml`: grades package, gateway port, consent, snapshot, DTOs, invariants. |
| Current schema | N/A | No database. Current app-data files are listed in the sibling design (`20261029-windows-platform-split/design/04-target-schema.dbml`); this feature adds one file and changes none. |
| Target schema | REQUIRED | `04-target-schema.dbml`: `grades-consent.json` (disk) plus the memory-only snapshot/session records and the API DTO shapes. |
| Transaction flows | REQUIRED | `05-transaction-flows.puml`: consent, fetch (cache/single-flight/login/retry/timeout), classes, failure path, clear on revoke/logout, LMS scores (G2). |
| State machines | N/A | The only lifecycle is the tab UI state (consent → loading → ready/error), which is not persisted; it is shown as a UI view in the review pack and owned by `useGrades`. |
| Consistency and migration | REQUIRED | `07-consistency-migration.md`: privacy, credentials, TTL, concurrency, failure isolation, packaging, rollout/rollback. |
| Acceptance trace | REQUIRED | `08-acceptance-trace.md`. |
| Visual review pack | REQUIRED | `09-design-review.md` (Korean, target overview first). |

## Confirmed decisions

- D1 separate opt-in tab; app identity and bundle id `com.local.ssu-lecture-downloader` unchanged.
- D2 consent copy "u-SAINT에 같은 계정으로 로그인해서 성적을 읽어 와요"; read-only.
- D3 grade data stays on the machine, never in logs/traces; failure shows "지금 가져올 수 없어요" only in the grades tab.
- D4 STANDARD profile under the blanket approval; autonomous within approved scope; Claude merges reviewed + CI-green PRs to develop. LMS part SHOULD, u-SAINT MUST; 학사 정보 later.

## Proposed decisions

- P1 Placement (after sibling S1): `core/grades/` = `models.py` (DTOs), `gateway.py` (`UsaintGateway` protocol + `RusaintGateway` adapter, the only module importing `rusaint`, imported lazily), `mapper.py` (rusaint objects → DTOs), `service.py` (`GradesService`), `consent.py` (`GradesConsentStore`, path injected); `service/routers/grades.py`. Wired in `service/app_context.py` with `AppPaths.app_data_dir()` from `platforms`. Core stays OS-free (rusaint is a network library like curl_cffi).
- P2 API contract (frozen at approval, see `09-design-review.md` § API 계약): `GET /api/grades/status`, `PUT /api/grades/consent`, `DELETE /api/grades/consent`, `GET /api/grades?refresh=`, `GET /api/grades/classes?year=&semester=&refresh=`; G2 `GET /api/grades/lms?term_id=`. Error codes `grades_consent_required` (409), `grades_login_rejected` (403), `grades_unavailable` (503, also for timeout and missing native lib). None of them is in the LMS `AUTHENTICATION_ERROR_CODES` set.
- P3 Consent: `grades-consent.json` `{schema: 1, consent_version: 1, granted_at}` in the app-data root, atomic write (temp + rename), mode 0o600 on POSIX. No username, no grade data. Bumping `consent_version` (copy change) re-asks.
- P4 Grade data memory-only (D5 pending, O1): `GradesSnapshot` held by `GradesService` for 30 minutes keyed by the current username; classes cached per (year, semester) inside the snapshot; `refresh=true` bypasses. Cleared on consent revoke, logout, changed credentials; gone on process exit.
- P5 Credentials: read from the existing `CredentialStore` at fetch time; `USaintSessionBuilder().with_password(id, pw)`; the resulting `USaintSession` kept in memory with the snapshot for reuse; no token/cookie persisted. A reused-session failure triggers exactly one fresh login (bounded retry); a fresh-login rejection maps to `grades_login_rejected` with no retry.
- P6 Async: routes are `async def` and await rusaint directly under `asyncio.wait_for` (summary fetch 25 s, classes 20 s); `asyncio.Lock` single-flight per process. If O4 shows event-loop blocking, switch the gateway to a dedicated worker thread with its own loop (same interface).
- P7 Privacy guardrails: `GradesError(code)` raised `from None` (no chained message), log events carry only code + elapsed ms; responses set `Cache-Control: no-store`; no grades in `NetworkTrace`, localStorage, console, toasts; mapper rejects unexpected types instead of echoing them.
- P8 Failure isolation: rusaint import happens only inside `RusaintGateway` on first use; `ImportError`/`OSError` → `available: false`; nothing at app start imports it. Frontend `useGrades` keeps its own error state and never calls `onAuthError`; 404 from an old backend maps to `grades_unavailable`.
- P9 Read-only guard: a test parses `core/grades/gateway.py` and fails if any rusaint name other than `USaintSessionBuilder`, `CourseGradesApplicationBuilder`, `CourseType`, `SemesterType` is referenced.
- P10 UI: TopBar ghost toggle "성적" right of "기한" (same `deadlines-toggle` look, generalized to `view-toggle`), `view: 'course' | 'deadlines' | 'grades'`, `GradesPane` in `main` with the DeadlinesPane structure; Settings section "성적 보기" with "끄기". Only existing tokens; no new colors.
- P11 Packaging: `HIDDEN_IMPORTS += ["rusaint"]` and `--collect-binaries rusaint` (or `--collect-all rusaint` if the wheel layout needs it); CI smoke asserts `available: true`. Windows `.dll` handled by sibling S2-PR7 with the same rule.
- P12 G2 LMS scores: reuse the service's logged-in curl_cffi session (cookies) for three Canvas reads; mapper keeps only assignments whose submission has a non-null `score` and is not `hidden`/muted; DTO has no `final_score`/`current_score` field at all. Those three Canvas calls record method/url/status only in `NetworkTrace` (no body preview), since score JSON would otherwise enter the trace.

## Open questions

- O1 (user) memory-only vs. disk cache for grade data (recommended memory-only).
- O2 (user) toggle always visible vs. enabled from 설정 first (recommended always visible).
- O3 rusaint field semantics (`ClassGrade.grade_points`, `score`, `rank`) — confirm from a value-scrubbed fixture of the owner probe in GR-PY-USAINT.
- O4 rusaint async inside uvicorn does not block the loop — verify with a slow-fetch test against the real library offline (connection to a closed port) in GR-PY-USAINT.
- O5 PyInstaller collection, ad-hoc signing and size delta of `librusaint_ffi.dylib` — GR-PKG-SIDECAR.
- O6 Does rusaint itself log to stderr (Rust `log`/`tracing`)? Verify no output appears in backend.log during the privacy test with the real library; if it does, silence its logger.

## Review findings

Self-checklist review pass (STANDARD; no independent agent review was run for this draft — disclosed limitation):

- F1 (accepted, fixed in P2/P8): u-SAINT login rejection reused as an LMS auth code would trigger `onAuthError` and sign the user out of the whole app, violating D3. Grades codes are now disjoint from `AUTHENTICATION_ERROR_CODES` and the hook never calls `onAuthError`.
- F2 (accepted, fixed in P7): `UnhandledErrorMiddleware` logs tracebacks into backend.log; a rusaint exception message could contain parsed page text. `GradesError ... from None` and a catch-all in the gateway keep messages out.
- F3 (accepted, fixed in P7): WebKit may keep HTTP responses in its disk cache; `Cache-Control: no-store` added on all grades responses.
- F4 (accepted): changed credentials must clear the grades snapshot and session or a second account could see the first account's grades for 30 minutes — hook in the credentials router, test in AC9.
- F5 (accepted): AC7 manual `grep` must use the owner's own values; never paste them into this record (record rule: no private data).
- F6 (rejected): a separate durable operation/retry table — reads are idempotent and bounded retry + error codes suffice.

## Validation evidence

- 2026-10-29 `python3 <agent-skills>/skills/feature-design/scripts/validate_design.py --record ../feature.md` → valid (artifact-set digest stored in ../feature.md).
- PlantUML and DBML renderers are not installed on this Mac (`plantuml`, `dbml2sql` not found): `.puml`/`.dbml` were not rendered or parsed — named evidence limitation, not a semantic pass. The Mermaid review pack is the rendered view.
