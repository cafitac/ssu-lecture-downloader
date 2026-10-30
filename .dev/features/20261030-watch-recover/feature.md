# Watch recovery (WATCH-RECOVER)

- Status: IN_PROGRESS
- Target/base: develop 05ac3ae (v0.2.1)
- Worktree: ssu-lecture-downloader-wt/watch-recover, branch feature/watch-recover

## Evidence (2026-10-30, v0.2.1, owner's machine)

- `backend.log` showed this sequence on one item:
  - `item_start` 18:44:13.
  - `progress` 25% at 18:54, then 50% at 19:04.
  - `cdp_disconnected reason=CdpClosed expected=false` at 19:07:50.
  - `chrome_stop`.
  - `run_end state=failed error_code=chrome_closed`.
- The Chrome profile's `Breadcrumbs` shows `0:23:40 Tab1 RenderProcessGone` at the same moment. The lecture tab's renderer died, and then the browser DevTools websocket closed.
- No crash dump (the profile has no Crashpad dir) and no macOS sleep. The system had heavy pageouts that day.
- Chrome auto-updated 156.0.8078.48 → .76 at 08:31 that day. The Chrome-watch code and the Chrome flags have been unchanged since 0.1.0.

## Owner (2026-10-30)

"이게 영상 한번 끊기면 이어서 볼 수 있게해야 되고 안 끊기게 하는게 제일 좋아"

"프로세스가 죽는 이유도 찾아봐주면 좋을 것 같은데"

## Scope

- **WR-CAUSE:**
  - Log why a renderer died: `Target.targetCrashed` / `Inspector.targetCrashed`, with status and errorCode when provided.
  - Log the Chrome version on `cdp_connected` (from `Browser.getVersion`).
  - Log LMS request failures (`lms_request_failed` step/status) at WARNING, without a path, URL, or query.
- **WR-RESUME:** if the renderer dies, or the DevTools connection drops while Chrome is still alive, recover instead of failing the run.
  - Reconnect if needed.
  - Reopen the same lecture.
  - Resume.
  - Allow at most 3 recoveries per item. After that, fall back to the existing failure path.
  - Log each recovery.
- **Prevention:** decide after WR-CAUSE data, e.g. Chrome flags against background renderer reclaim. Out of scope here.

## Acceptance

- AC1: a renderer crash event logs `renderer_gone` with status/errorCode, then a `recover` line. Playback continues on the same item without failing the run.
- AC2: a dropped CDP connection while the Chrome process is alive reconnects and resumes. A mid-playback Chrome process exit reports `chrome_quit_unexpectedly`; launch-time exits retain `chrome_exited`.
- AC3: after 3 recoveries on one item, the run fails with `chrome_recover_exhausted`.
- AC4: a user stop during recovery stops cleanly.
- AC5: LMS failures appear in `backend.log` without query strings or tokens.

## Implementation notes

- Renderer crash events record status/errorCode; browser connection logs include Browser.getVersion product.
- Each item permits three recovery attempts. A closed CDP transport re-reads DevToolsActivePort;
  page sessions are rebuilt and the original launch URL is reopened, creating a tab if its target disappeared.
- Playback restarts the existing front/resume-click, minimize and progress flow. There is no safe seek
  contract in the current helper, so position recovery relies on the LMS player resume prompt.
- Recovery uses the existing opening phase and a Korean informational notice. Stop retains its existing terminal state.
- LMS failure warnings contain step/status only, with no URL or query.
- Real Chrome renderer crashes and real LMS resume persistence have not been exercised in this worktree.

## Local verification

- `UV_LOCKED=1 uv run pytest -q`: 893 passed, 2 existing dependency deprecation warnings (49.51s).
- Focused Chrome watch suite: 54 passed, including seven added cases and the updated exited-Chrome case.
- `UV_LOCKED=1 uv run lint-imports`: 4 contracts kept, 0 broken.
- Commands used `UV_CACHE_DIR=/tmp/watch-recover-uv-cache` because the default cache is outside sandbox access.
- `git diff --check`: clean. No commit, push, or external writes performed.

## Review cycle 1 repairs

- Recovery tests join the worker before reading terminal status; no new sleep polling.
- Closed transports wait through `terminate_grace` for a pending process exit before consuming
  a recovery attempt. Background launches validate the dedicated main pid through the process tree.
  Subsequent attempts use the injectable `reconnect_backoff` delay and remain stoppable.
- Page and OOPIF sessions enable Inspector; attached iframe target ids also identify renderer crashes.
- Recovery-time version/attach CDP errors and timeouts consume the bounded recovery budget.
- Crash events are collected before the alive check logs one incident, retaining Target status/errorCode.
- Exhaustion reports `chrome_recover_exhausted`; an observed process exit without a page-destroy event
  originally reported `chrome_exited` (superseded by the cycle 2 repair below).
- No frontend changes. Real Chrome crash/reconnect and LMS resume persistence remain unverified.

### Review cycle 1 verification

- Final `UV_LOCKED=1 uv run pytest -q`: 903 passed, 2 dependency deprecation warnings (45.45s).
  The STT fake server printed a BrokenPipeError during shutdown; pytest exited successfully.
- Chrome watch tests: 64 passed (10 additional cases); Chrome watch plus API tests: 74 passed.
- `UV_LOCKED=1 uv run lint-imports`: 4 contracts kept, 0 broken.
- `git diff --check`: clean. Used `UV_CACHE_DIR=/tmp/watch-recover-uv-cache`.
- No commit, push, external write, or real Chrome/LMS execution.

## Review cycle 2 repairs

- Playback process exits consistently report `chrome_quit_unexpectedly`, including exits observed
  during the disconnect grace period or a failed reconnect. Launch-time `chrome_exited` is unchanged.
- Pending events are drained before classifying click/navigation protocol errors and before recovery
  attachment. A closed client also triggers the exit grace wait. User tab/window close takes priority
  over renderer recovery, including closes arriving during teardown.
- Tests pin `current.phase=opening` and informational `recover` notices during recovery, no
  failed/skipped notices for successful recovery, and exactly one failed notice on exhaustion.
- Error codes: `chrome_exited` (immediate launch exit), `chrome_quit_unexpectedly` (playback process
  exit), `chrome_window_closed` (page/window closed), `chrome_closed` (unrecoverable connection loss
  without an observed process exit), `chrome_recover_exhausted` (three recovery attempts exhausted).

### Real Chrome probe: blocked before CDP attachment

- Throwaway probe lives outside this repository under the assigned scratchpad `crash-probe/`
  directory as `probe.py`; `result.txt` stores the latest attempt.
- Launched the installed Google Chrome executable with a fresh temporary profile,
  `--remote-debugging-port=0`, and `--headless=new`. Repeated with the profile under `/private/tmp`,
  then with diagnostic `--no-sandbox --disable-crash-reporter` flags (probe only).
- Each attempt waited 200 x 50 ms (nominal 10 s) for DevToolsActivePort; none produced the file.
  The final baseline attempt measured 10.834 s, observed process return code -6 (SIGABRT),
  and captured 0 bytes of stderr.
- Events: none observed because no browser websocket could be opened. Websocket survival,
  `Page.crash`, domain-enable timings/results, and navigate/reload recovery remain **UNVERIFIED**.
  The script contains those calls using the repository's CdpClient/WebSocketTransport for rerun.
- Every probe profile was removed in `finally`, and the launched process was waited/reaped.
- Item 1 is blocked: no speculative domain-order change or fake claiming real Chrome behavior was
  made without the requested evidence. The existing renderer recovery order still needs validation
  and repair on a host where this Chrome invocation can start.

### Review cycle 2 verification

- `UV_LOCKED=1 uv run pytest -q`: 908 passed, 2 dependency deprecation warnings (44.52 s, final code snapshot).
  The STT fake server emitted a BrokenPipeError during shutdown; pytest exited 0.
- Focused Chrome watch suite: 69 passed (five additional cases, 1.77 s); final full-suite run also
  covers the explicit process-alive recheck after the exit wait.
- `UV_LOCKED=1 uv run lint-imports`: 4 contracts kept, 0 broken.
- Used `UV_CACHE_DIR=/tmp/watch-recover-uv-cache`. No frontend changes, commits, pushes, or external
  writes. Real Chrome recovery remains blocked as detailed above; real LMS persistence is unverified.
- `git diff --check`: clean.


## Real Chrome evidence applied (item 1)

The coordinator reran the probe outside the sandbox with headless=new, Chrome
156.0.8078.76 and flattened page attachment. Source: scratchpad
`crash-probe/result-real.txt` (coordinator-provided evidence; supersedes the blocked probe above).

- `Page.crash` received no reply: CdpTimeout after 5.005 s.
- Events: `Inspector.targetCrashed` on the page session with empty params;
  `Target.targetCrashed` on the browser session with the same targetId,
  status `crashed`, errorCode 5.
- Browser websocket stayed OPEN, Chrome stayed alive, and the target retained its id and attachment.
- On that same crashed session, `Page.enable` and `Runtime.enable` each timed out
  after 5.005 s; `Inspector.enable` returned in 0.001 s.
- Same-session `Page.navigate` revived the tab in 0.021 s. Events included
  `Inspector.targetReloadedAfterCrash`, frameNavigated, DOMContentLoaded and load.
- Afterwards Page.enable (0.055 s), Runtime.enable (0.063 s), Page.reload (0.012 s),
  and Runtime.evaluate (0.051 s) succeeded. Detach (0.002 s), flattened reattach
  (0.004 s), new-session navigate (0.016 s) and evaluate (0.010 s) also succeeded.
- Recovery now navigates to the lecture launch URL on the existing page session
  before reattachment/domain setup. Old child-session state is discarded and
  auto-attach rebuilds it. Playback reuses that navigation rather than loading twice.
- A closed browser connection still rereads DevToolsActivePort and reconnects;
  its log cause is `cdp_closed`, including when a crash event was also observed.
- FakeBrowser withholds replies to Page.enable/Runtime.enable on a crashed page
  until navigation revives it. Two focused timeout cases pin this behavior, and
  recovery tests assert navigate precedes enable/reattach on the same session.
- Real LMS resume persistence remains unverified; the probe used a throwaway page.

### Item 1 verification

- Final `UV_LOCKED=1 uv run pytest -q`: 910 passed, 2 dependency deprecation
  warnings (45.07 s). The STT fake server printed its shutdown BrokenPipeError;
  pytest exited 0.
- Focused Chrome watch suite: 71 passed (1.79 s), including two new domain-timeout cases.
- `UV_LOCKED=1 uv run lint-imports`: 4 contracts kept, 0 broken.
- `git diff --check`: clean. Used `UV_CACHE_DIR=/tmp/watch-recover-uv-cache`.
- Preserved prior uncommitted repairs. No commit, push, or external writes.


## Review fixes after head 9642616

- Ignore replayed Inspector crash signals from the session being revived; clear
  `renderer_gone` on `Inspector.targetReloadedAfterCrash`. Failed recovery discards
  the page session and session state, so the next attempt attaches again. A newly
  attached crashed target is navigated before domain setup.
- Share incident logging across alive checks, playback exceptions and recovery,
  before deciding whether to recover or exhaust the budget. The repeated-crash
  test requires four `renderer_gone` lines for four crashes and three recoveries.
- FakeBrowser replays `Inspector.targetCrashed` when Inspector.enable targets a
  crashed session and emits targetReloadedAfterCrash on revival. Tests cover
  successful revival despite replay and retry after reattachment failure.
- Coordinator evidence: `crash-probe/result-real-order.txt`, Chrome 156.0.8078.76,
  ran crash → same-session navigate → detach → attach → domain enables → evaluate.
  Exactly one Inspector.targetCrashed arrived, at the crash. Navigate emitted
  Inspector.targetReloadedAfterCrash on the old session; new-session enables and
  evaluate succeeded. The earlier `result-real.txt` sent Inspector.enable before
  navigation: that call replays targetCrashed, explaining its second crash event.
  This is coordinator-supplied real Chrome evidence; real LMS resume persistence
  remains unverified.
- Follow-up after merge: ERR-PROMPT's known limitation that a Chrome exit is
  reported as `chrome_window_closed` becomes obsolete.

### Verification

- Focused Chrome watch suite: 74 passed.
- `UV_LOCKED=1 uv run pytest -q`: 913 passed, 2 existing dependency
  deprecation warnings (43.35 s).
- `UV_LOCKED=1 uv run lint-imports`: 4 contracts kept, 0 broken.
- Used `UV_CACHE_DIR=/tmp/watch-recover-uv-cache`. `git diff --check`: clean.
- No commit, push, or external writes.
