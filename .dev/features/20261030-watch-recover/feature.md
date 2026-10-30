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
  - Log the Chrome version at `run_start` (from `Browser.getVersion`).
  - Log LMS request failures (`lms_request_failed` step/status) at WARNING, with the path only and no query.
- **WR-RESUME:** if the renderer dies, or the DevTools connection drops while Chrome is still alive, recover instead of failing the run.
  - Reconnect if needed.
  - Reopen the same lecture.
  - Resume.
  - Allow at most 3 recoveries per item. After that, fall back to the existing failure path.
  - Log each recovery.
- **Prevention:** decide after WR-CAUSE data, e.g. Chrome flags against background renderer reclaim. Out of scope here.

## Acceptance

- AC1: a renderer crash event logs `renderer_gone` with status/errorCode, then a `recover` line. Playback continues on the same item without failing the run.
- AC2: a dropped CDP connection while the Chrome process is alive reconnects and resumes. If Chrome itself exited, the run behaves as today.
- AC3: after 3 recoveries on one item, the run fails as today with a clear error_code.
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
