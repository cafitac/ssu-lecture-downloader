# Error prompt and log caps (ERR-PROMPT)

- Status: IN_PROGRESS
- Target/base: develop (origin/develop 05ac3ae, v0.2.1)
- Worktree: ssu-lecture-downloader-wt/error-prompt, branch feature/error-prompt
- Lane provider: frontend lane on Claude per owner decision; router wiring pending (agent-skills)

## Owner decisions (2026-10-30)

- When something goes wrong, the app should not wait for the user to open Settings. It should
  pop up on its own: "오류가 감지되었다, 로그를 추출하여 레이즈하겠냐".
- Where the report goes: "파일로 저장 후 Finder". Save the diagnostics zip to a fixed folder and
  reveal it in Finder; nothing is uploaded. The repository is public, so no GitHub issue.
- Log size must not grow without bound on the user's machine. Which agent owns reviewing this
  question was sent to the agent-skills session.

## Scope

- **EP-CAP:** cap log growth.
  - `backend.log`: the shell writes the sidecar's stdout/stderr itself and rotates at 5 MB,
    keeping one previous file. This replaces the raw fd redirect.
  - `watch.log`: rotate at 2 MB, keeping one previous file.
  - Worst case for the log folder: about 15 MB.
- **EP-PROMPT:** an in-app "문제가 생겼어요" dialog when an error is detected. It offers
  [진단 파일 저장] (saves to `~/Downloads/SSU 진단/` and reveals the file in Finder) and [닫기].
  - Triggers:
    - a Chrome watch notice of kind `failed` (not a login loss, not `chrome_window_closed`, which
      the user causes) or `skipped` (the stall that ends in a skip; a first `stalled` notice
      usually recovers and does not ask). A Chrome skip and a Chrome failure are separate
      prompt kinds, each asked once per session;
    - the in-app watch runtime reporting a failed or skipped item;
    - the backend failure screen;
    - an abnormal exit of the previous app run, asked at the next launch through a session
      marker file.
  - Ask at most once per trigger kind per app session. While the dialog is open, nothing else
    stacks.

## Acceptance

- AC1: neither log file grows past its cap. Rotation keeps exactly one previous file. Stdout
  and stderr lines from the sidecar still reach `backend.log`, in order per stream (two writer
  threads; no ordering between stdout and stderr, no stream prefixes). A crash during startup
  stays diagnosable.
- AC2: each trigger opens the dialog once per session. 닫기 dismisses it. Saving writes a zip
  through the existing `save_diagnostics` and reveals it in Finder.
- AC3: after a clean quit, the next launch shows no prompt. After a crash or force-quit, the
  next launch shows the prompt once.
- AC4: the copy is plain Korean. No upload happens.

## Known limitations

- An unexpected exit of the Chrome process is reported by the backend as `chrome_window_closed`
  (chrome_watch.py `check_alive`), the same code as the user closing the window, so it is not
  prompted. The WATCH-RECOVER follow-up will split the two.
