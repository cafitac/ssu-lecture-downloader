# AU-UPDATE-UI-BASIC

- Record schema: 4
- Status: IN_PROGRESS
- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-auto-update/feature.md
- Sprint manifest: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-auto-update/sprints/sprint-1-v2.md (section "AU-UPDATE-UI-BASIC")
- Repository: cafitac/ssu-lecture-downloader (public)
- Target/base: develop
- Base ref: origin/develop
- Base decision: repository default integration branch
- Initialization base SHA: 6e1ab43 (includes PR #8, AU-UPDATER-SHELL)
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/au-update-ui-basic (feature/au-update-ui-basic)
- Canonical record: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/au-update-ui-basic/.dev/features/20261029-auto-update/units/AU-UPDATE-UI-BASIC/feature.md
- Design status: APPROVED (parent, revision 3)
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Worktree cleanup status: ACTIVE
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT

## Outcome

See the sprint manifest section "AU-UPDATE-UI-BASIC" (sprint-1-v2.md in the parent feature folder). Acceptance subset: AC4, AC8 (version N part). Design: 05 update-check-and-install, 06 update-ui-state-basic, 09 notice principle.

## Workflow cursor

- Workflow stage: FEATURE_DEVELOP
- Next eligible action: CODE_REVIEW
- Next action class: LOCAL_CONTINUE
- Gate state: NONE

## Progress and evidence

- 2026-10-30: worktree created from origin/develop 6e1ab43 (PR #8 merged). No Rust or Python change was needed or made.
- 2026-10-30: implemented locally (not pushed).
  - `frontend/src/lib/appUpdate.ts`: framework-free controller (in memory, no DOM access). Check at launch and every 6 h; only from Idle, so one check or install is in flight; "나중에" hides the chip 24 h and the 6 h ticks inside the window are skipped, then it checks again. 지금 업데이트: busy check (UI-tracked work, then `update_install_guard`) -> download with progress -> busy check + guard again -> `prepare_for_update` -> install -> relaunch. A busy or refused step shows "작업이 끝난 뒤 다시 시도해 주세요" in the panel and starts nothing. Install failure after prepare Ok calls `backend_restart` and shows a Korean error with "다시 시도". No restart control exists in the UI; `backend_restart` is called only after an install failure (or after a `prepare_for_update` failure of unknown shape, where it is not certain that nothing was stopped). `backend_starting` and `backend_unavailable` mean nothing was stopped, so no restart. A relaunch failure after a successful install shows a final message with no retry and no restart.
  - `frontend/src/lib/updater.ts`: the only file that uses `@tauri-apps/plugin-updater`, `@tauri-apps/plugin-process` and the two update commands (adapter behind the `UpdateApi` interface).
  - `frontend/src/lib/trackedWork.ts`: small helper for "tracked jobs" (download/convert/subtitle jobs via `useDownloads.active`, media component install, page attachment saves). Kept out of the files PL-UI-STATES edits.
  - `frontend/src/hooks/useAppUpdate.ts`, `frontend/src/components/UpdateChip.tsx` (chip + panel), `frontend/src/styles/update.css`. `TopBar.tsx` mounts the chip and takes one new prop (originally `workBusy`; SUPERSEDED by repair 1: it now takes the App-level `update` controller state, and `workBusy` is held in App); `App.tsx` passes it (one import, one prop expression).
  - The notice: a chip "새 버전 x.y.z" in the top bar (superseded by repair 1 below: the controller now lives in the App root). Chip opens a non-modal `section` panel anchored under it: release notes, [지금 업데이트], [나중에]. No dialog/overlay/portal, no `autoFocus`, no focus trap, no key capture (no `preventDefault`/`stopPropagation`), no `Notification`/sound. Outside `mousedown` and Esc close it; the listeners exist only while it is open. While an install runs the chip carries the step ("· 받는 중 42%", "· 설치 중", "· 다시 시도 필요").
  - The primary button of the panel is accent-tinted (`update-go`), not `btn-primary`, because an existing contract rule allows exactly one accent-filled button next to the download button.
- Contract test: `frontend/scripts/contract-test.mjs` appended (controller with a fake clock and mocked plugin API: launch/6 h check, failed check logged, found -> chip only with no DOM/Notification/Audio access, 24 h hide and tick skipping, busy by UI work and by guard, happy path order and progress, guard re-check refusal, download failure and retry, `prepare_for_update` refused/backend_starting/unknown, no restart while the install is pending then restart after its failure, restart failure copy, relaunch failure; the adapter against a mocked Tauri bridge; the component rendered with real React SSR: no dialog/overlay/modal/autofocus markup, panel states, no buttons while installing; source rules). One existing assertion at the end of the file had to change (not append): "the updater must stay inert until AU-UPDATE-UI" now says that only `src/lib/updater.ts` may use the update plugins and commands (it pinned exactly what this unit adds).
- Local checks: `npm ci`, `npm run typecheck` ok, `npm run test:contract` ok.
- Screenshots (untracked, `.local/update-ui/shots/`, produced with a vite dev server whose updater adapter is replaced by a mock, and a headless Chrome driven over CDP; the harness renders the real `TopBar`, hook and controller): 1-chip-while-typing, 2-panel-open, 3-busy-message, 4-download-progress, 5-install-error. Measured in the same run: while typing in another field the chip appeared and `document.activeElement` stayed the input with the text intact, no dialog/overlay element, no disabled control, another button still clickable; Esc and an outside click closed the panel; Tab moved on to the next control (no trap); "나중에" removed the chip; the install error run called `backend_restart` once.
- Not covered (needs the owner's Mac, CP-DOGFOOD): a real signed update through the installed app (AC6), a real `tauri dev` run of the panel.
- Formal review 1 repair batch (second commit):
  - P2 stale connection: the adapter's `restartBackend` is now `recoverBackend`: `backend_restart`, then waits (bounded, 30 s, `startPolling` every 500 ms) until `backendSupervisionStatus()` is no longer `starting`, then `resetBackendConnection()`; it rejects unless the backend came back `ready`. The "지금 쓰던 버전은 그대로 쓸 수 있어요" copy shows only after that succeeded; otherwise the copy asks to close and reopen the app. Contract test: after recovery the next `backendApiConnection()` invokes `backend_api_connection` again (and nothing new is cached while `starting`).
  - P2 launch check: the single `useAppUpdate` controller moved to the App root above the BackendScreen and SessionGate branches (needs neither backend nor session); the workspace reports tracked work up through `onWorkBusyChange`. `UpdateChip` takes the controller as a prop: in the TopBar when signed in, as a small fixed corner chip (`update-corner`, top right, no overlay) on the login and backend-failure screens. "나중에" survives sign-out and sign-in because the controller outlives the workspace.
  - P4 discard bytes: a guard refusal after the download, and a refusing `prepare_for_update`, `close()` the update, re-check, and return to Available with the waiting message; when the re-check finds nothing, the next click asks again before it downloads.
  - Failed states with the "close and reopen" copy (install failed and backend not restored, relaunch failed, unknown prepare failure with a dead backend) offer no "다시 시도"; the chip says "새 버전 x.y.z · 앱을 다시 열어 주세요".
  - Tests added: `backend_unavailable` prepare, recovery/connection reset, bounded wait, hook lifecycle (React hooks replaced by a recording fake through a vite plugin: no check outside the Tauri runtime, check at mount without any session or backend, 6 h timer set and cleared on unmount), App-level source rules (one controller, created before the early returns, corner chips on both screens).
  - Screenshots added under `.local/update-ui/shots/`: 6-chip-on-login(.png, -panel.png), 7-chip-on-backend-failure(.png, -panel.png).
- Formal review 2 repair batch (third commit):
  - P2 backend retry during install: while `isInstalling(update.state.phase)` (downloading, guarding, installing, relaunching) the BackendScreen's "다시 시도" is replaced by the status line "업데이트하는 중이에요" (`retryBlockedText`), so its `backend_restart` cannot clear the update latch under the installer. Asserted by SSR (with and without the prop) and by source rules on App.tsx.
  - P2 test sleeps: both added fixed sleeps removed (count back to the base 9). "Polling stops" is now observed by tracking live timers (none survive the end of the wait); the hook test flushes microtasks instead.
  - P3 busy kept on unmount: the workspace no longer reports `false` on unmount; the last value stays until a workspace reports again. Sign-out in the settings dialog also waits for tracked work (`workBusy`: component install, attachment save, download), next to the existing download gate.
  - P3 recovery bound: one documented constant, `BACKEND_RECOVERY_TIMEOUT_MS` = Rust's 90 s `BACKEND_STARTUP_TIMEOUT` (`BACKEND_STARTUP_TIMEOUT_MS`, pinned against backend.rs) + 15 s. A timeout while still `starting` rejects without resetting the cached connection (tested).
  - P4 stale backend screen: `useBackendStatus` exposes `refresh`; the update controller calls `onBackendRestored` after a successful recovery and App re-reads the backend status (not called when the restart fails).
- Formal review 3 repair batch (fourth commit, final):
  - Stale busy: the App holds `workBusy` across workspace unmounts (a component install may run behind the login screen) but drops it whenever the backend is not ready (`busyAfterBackendStatus`: no job survives a dead sidecar). On the download auth-error path `useDownloads.applyJob` now also stores the terminal job before returning false, so `downloads.active` and the reported busy flag drop. Sign-out stays blocked while a component install or attachment save runs. The login-screen chip panel shows "앞에서 하던 작업이 끝났는지 확인하려면 다시 로그인해 주세요" while a busy flag is held; the next sign-in re-reads live state because the new workspace reports it. Known limit: when a session expiry unmounts the workspace in the same render batch as the job update, the last reported value can stay true until the next workspace reports; the hint line covers that.
  - Recovery refresh on both outcomes: the controller calls `onRecoveryAttempted` after every attempt to bring the backend back, successful or not, so App re-reads the real backend status.
  - Tests: hook-level (React hooks replaced by a recording fake) for the download auth path (running job, poll returns "session expired" -> `active` false; verified to fail without the fix), SSR for the hint, unit for the busy helper and for the controller callback on both outcomes.

## Paths outside the owned list

The sprint manifest lists `useAppUpdate.ts`, `UpdateChip.tsx`, `TopBar.tsx` (mount only), `lib/` helpers, tests, `contract-test.mjs` (append). The unit also touches, each for a reason found in review:
- `frontend/src/App.tsx`: the update controller must live above the backend and session branches (check at launch on every screen, "나중에" survives sign-out); it passes `update` to the top bar, reports tracked work up from the workspace, and blocks the backend retry while an update installs.
- `frontend/src/components/Screens.tsx`: `BackendScreen` shows "업데이트하는 중이에요" instead of its retry while an update installs (`backend_restart` would clear the update latch).
- `frontend/src/components/SettingsDialog.tsx`: sign-out waits for component installs and attachment saves (`workBusy`), so a busy flag is not lost behind the login screen.
- `frontend/src/hooks/useBackendStatus.ts`: `refresh`, so App re-reads the backend status after the update recovery.
- `frontend/src/hooks/useDownloads.ts`: two lines on the auth-error path so a job that ended with "session expired" is terminal in state (`active` false). Edited after PL-UI-STATES (#11) merged; the hunk is in `applyJob`, disjoint from #11's hunks (row holding and summary).
- `frontend/src/styles.css` (one import line) and `frontend/src/styles/update.css` (new): chip, panel and corner-chip styles.
- `frontend/scripts/contract-test.mjs`: appended blocks, plus the one existing "inert updater" assertion narrowed to the single adapter file `src/lib/updater.ts`; later repair batches also adjusted assertions inside this unit's own appended blocks.

PL-UI-STATES disjointness: this branch is based on 6e1ab43 and does not contain #11 (d0591ca). Of PL-UI-STATES's files (`jobItems.ts`, `DownloadDrawer.tsx`, `useDownloads.ts`, `errors.ts`) only `useDownloads.ts` is touched here, in a hunk that does not overlap #11's changes (verified against origin/develop d0591ca: #11 changes the imports, `rows`/`summary` and `start`; this unit changes only the auth branch of `applyJob`). A rebase onto develop is expected to be clean.
