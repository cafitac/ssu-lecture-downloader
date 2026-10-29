# Consistency and migration

## Scope of state

No database and no server. The feature adds one local file (`grades-consent.json`) and in-process
memory owned by `GradesService`. All external calls are reads (u-SAINT, and Canvas in G2), so every
call is safe to repeat; there is no external effect to order against a commit, no idempotency key,
and no result-unknown state. A separate operation/retry table is deliberately not introduced
(finding F6): bounded retry + error codes + logs cover every failure.

## Aggregate and transaction boundary

- `GradesService` (one per backend process) is the single owner of: consent check, snapshot,
  u-SAINT session, single-flight lock. Routers call it; nothing else touches the gateway.
- `GradesConsentStore` is the only writer/reader of `grades-consent.json`. Write = temp file in
  the same directory, flush + fsync, `os.replace` (atomic on macOS and Windows). Read tolerates a
  missing, empty, or malformed file as "no consent" (fail closed: ask again, never fetch).
- Consent check is part of every fetch (invariant I1). `consent_version` in the file must equal the
  app's current version (1); a future copy change bumps it and re-asks.

## Credential handling

- Source: the existing `CredentialStore` (default `InMemoryCredentialStore`). The grades feature
  reads the pair at fetch time and never stores, copies, hashes, or logs it.
- u-SAINT login: `USaintSessionBuilder().with_password(id, pw)`. `with_token(id, sso_token)` is not
  used in G1 (it would need an SSO token extracted from the LMS login; revisit only if u-SAINT
  password login starts requiring extra steps).
- The returned `USaintSession` is kept in memory next to the snapshot for reuse (fewer logins) and
  dropped with it. It is never serialized.
- Account switch safety: `snapshot.account_key` and the session are bound to the username; a
  mismatch is treated as absent, and the credentials router calls `GradesService.clear()` on logout
  and on a changed pair (finding F4).

## Cache, TTL, freshness

- Default (D5, recommended): memory only. Overview snapshot TTL 30 minutes; classes per semester
  share the snapshot lifetime. `refresh=true` re-fetches (still single-flight). Process exit loses
  everything; after an app restart the tab fetches again (u-SAINT login takes a few seconds; the
  pane shows "u-SAINT에서 성적을 불러오는 중…").
- A failed refresh does not discard a still-valid snapshot for the same account; the pane keeps the
  old data and shows "업데이트하지 못했어요 · 다시 시도". TTL expiry never manufactures data: an expired
  snapshot is not shown after a failed fetch.
- Alternative (O1, only if the user picks it): `grades-cache.json` in the app-data root, 0o600,
  same atomic write, deleted with consent revoke and logout; marked with `account_key` digest; shown
  with "마지막으로 가져온 성적 · 날짜". Not designed further unless chosen; choosing it reopens this
  design (new file, retention, privacy).

## Concurrency

- `asyncio.Lock` per process: at most one u-SAINT login/fetch in flight (I5). A request waiting on
  the lock re-checks the snapshot after acquiring it, so a double click or two windows produce one
  login.
- Routes are `async def` and await rusaint (UniFFI async) directly, wrapped in
  `asyncio.wait_for` (overview 25 s, classes 20 s). Other routes are sync `def` handlers on the
  thread pool, so a slow u-SAINT never blocks LMS routes. O4 verifies that rusaint does not block the
  event loop; fallback = gateway runs its coroutines on a dedicated thread with its own loop (same
  interface, no design change).
- Timeout cancellation: a cancelled rusaint future leaves no state; the session is dropped on any
  failure so the next attempt logs in fresh.

## Failure isolation

| Failure | Detection | Result | Scope |
|---|---|---|---|
| rusaint not installed / native lib missing | `ImportError`/`OSError` on first gateway use | `available: false`; fetch → 503 `grades_unavailable` | grades tab only |
| network down / DNS / TLS | rusaint exception | one fresh-login retry, then 503 `grades_unavailable` | grades tab only |
| u-SAINT slow | `asyncio.wait_for` timeout | 503 `grades_unavailable` | grades tab only |
| WebDynpro page changed (parser break) | rusaint exception or mapper type check | 503 `grades_unavailable` | grades tab only |
| login rejected (password changed, u-SAINT block) | rusaint auth error on a fresh login | 403 `grades_login_rejected`; LMS login untouched | grades tab only |
| old backend without routes | 404 | frontend maps to `grades_unavailable` | grades tab only |

- Error codes are disjoint from `AUTHENTICATION_ERROR_CODES` (backend `api_errors.py`, frontend
  `api.ts`): no grades failure can sign the user out (finding F1).
- App start never imports rusaint; `/api/health` does not depend on it.

## Privacy, logging, retention

- Grade values live only in `GradesService` memory and in the HTTP response to the local webview.
- Logging: logger `ssu_lecture_downloader.grades` emits `grades_fetch` / `grades_classes` events with
  `result=ok|<error_code>` and `elapsed_ms` only. No username, no values, no exception messages.
- Exceptions: the gateway catches every rusaint exception and raises `GradesError(code) from None`,
  so `UnhandledErrorMiddleware` and uvicorn never log a rusaint message or traceback that could carry
  page text (finding F2). Mapper errors carry the field name only.
- Trace: rusaint uses its own Rust HTTP client; nothing enters `NetworkTrace`. G2 Canvas reads go
  through the existing traced client, whose `body_preview` would include score JSON — G2 must call
  those three endpoints with tracing disabled for the body (record method/url/status only) or skip
  the trace entry; covered by AC8 privacy check.
- HTTP: all grades responses set `Cache-Control: no-store` (finding F3). Query strings carry only
  year/semester/term_id/refresh (no values) — acceptable in uvicorn access lines.
- Frontend: grades kept in React state only; never in localStorage, console, toasts, or Tauri
  commands; cleared on sign-out and on revoke.
- Retention: consent file until revoked or the app-data folder is deleted; grade data until the
  process exits or `clear()`.
- Records: the owner's real values never go into `.dev` records, PR bodies, or test fixtures;
  fixtures use invented values (finding F5).

## Packaging

- `pyproject.toml`: `rusaint==0.16.3` (exact pin; upgrade only by PR with a re-run of the owner
  probe, O6).
- `scripts/build_backend_sidecar.py`: add `rusaint` to `HIDDEN_IMPORTS` and collect its binaries
  (`--collect-binaries rusaint`, or `--collect-all rusaint` if the module layout needs data files).
  CI smoke: start the built sidecar, `GET /api/grades/status` → `available: true`.
- macOS build stays x86_64 (wheel `macosx_10_12_x86_64`). Windows `.dll` collection goes with sibling
  S2-PR7 (R10); both edits touch the same file, so the later PR rebases (ALLOW_IF_DISJOINT).

## Migration and rollout

1. GR-PY-USAINT (after sibling S1-PY-MOVE + S1-PY-PLATFORMS MERGED): backend, additive routes.
   Without GR-PKG-SIDECAR the packaged sidecar reports `available: false` — safe.
2. GR-PKG-SIDECAR: native library in the sidecar.
3. GR-UI-TAB (developed in parallel against the frozen API contract, merged after step 1).
4. Owner checkpoint AC7 on a real account (installing the Mac app is user-reserved; never while an
   auto-watch runs).
5. G2 GR-LMS-SCORES after AC7.

Compatibility: no data migration. Older builds ignore `grades-consent.json`; newer builds treat a
missing file as no consent. Switching builds in either direction is safe.

## Rollback

- Any unit: revert its PR on develop. Reverting GR-PKG-SIDECAR leaves the tab in
  "지금 가져올 수 없어요"; reverting GR-UI-TAB hides the tab; reverting GR-PY-USAINT removes routes
  (the UI maps 404 to unavailable).
- Per user: 설정 > 성적 보기 > 끄기.
- Installed app: reinstall the previous build; app-data stays compatible.
