# Ask once to tidy after the update (TIDY-PROMPT)

- Status: IN_PROGRESS
- Target/base: develop fe22583
- Worktree: ssu-lecture-downloader-wt/tidy-prompt, branch feature/tidy-prompt
- Lane provider: the frontend lane runs on Claude by owner decision. The router wiring is still pending in agent-skills.

## Owner (2026-10-30)

The owner never saw the [정리하기] button ("아 정리하기 버튼이 있었구나 이거를 못 봤어"). Asked how tidying should start, they chose: "업데이트 후 한 번 물어보기 (Recommended)".

## Scope

- **TP-ASK:** after login, call `GET /api/library/legacy-summary` for the current download folder. If it reports `legacy_file_count > 0`, show a dialog once.
  - The dialog says: "예전 방식으로 저장된 파일이 n개 있어요. 새 폴더 구조(과목/주차/강의)로 정리할까요?" with the buttons [미리보기] and [나중에].
  - [미리보기] opens the existing tidy dialog (preview → 정리하기). Nothing moves without that confirmation.
  - [나중에] dismisses the dialog. Settings → 다운로드 폴더 정리 stays available.
  - "Once" means once per download folder per app version: it asks again after a later update or a folder change, while files remain.
  - The dialog never opens while another dialog is open (the problem prompt or the update panel), or while a download or tidy is running. In those cases it waits until they are done.
- **TP-COPY:** add `errors.ts` copy for the new backend codes from WATCH-RECOVER:
  - `chrome_quit_unexpectedly`: "Chrome이 갑자기 종료되어 자동 재생을 멈췄어요."
  - `chrome_recover_exhausted`: "Chrome 연결이 반복해서 끊겨 자동 재생을 멈췄어요."

## Acceptance

- AC1: with legacy files present, the first launch after login shows the dialog once. [나중에] does not show it again in this version for this folder.
- AC2: [미리보기] opens the existing preview, and no file moves before 정리하기.
- AC3: no dialog appears when the count is 0 or the summary API is unsupported or fails.
- AC4: the dialog never stacks with another dialog and never opens while a download or tidy is running.

## Implementation (2026-10-30)

- **TP-ASK:**
  - `lib/tidyPrompt.ts` (pure): the decision `shouldShowTidyPrompt` (count > 0, not asked for this version + folder, no other dialog, not busy) and the remembered "asked" list. It is stored in localStorage under `ssu.tidyPromptAsked` as `{version, folders}`, the same kind of per-app store as the download folder and the watch mode. A new version starts the list over.
  - `hooks/useTidyPrompt.ts`:
    - Reads `GET /api/library/legacy-summary?output_dir=` (`fetchLegacySummary` in `lib/api.ts`) once the download folder is final (`useDownloadDir().ready`).
    - A failed or unsupported summary or a count of 0 means no prompt.
    - The prompt counts as asked the moment it shows. While another dialog is open or work is running it is hidden but not used up, and it returns afterwards.
  - The app version comes from `@tauri-apps/api/app` (`appVersion()` in `lib/desktop.ts`; `'web'` outside the desktop app).
  - `components/TidyPromptDialog.tsx`: [나중에] and [미리보기] (btn-primary). [미리보기] calls the existing `tidy.open()`, so nothing moves before 정리하기 in the existing dialog. The settings entry is unchanged.
  - One-dialog-at-a-time:
    - The existing problem controller already gates the problem prompt among its own kinds.
    - The tidy prompt is gated in the Workspace by `otherDialogOpen` (problem prompt, update panel, settings, watch-all, tidy dialog) and `busy` (tracked work or an update install).
    - The update panel reports its open state through `UpdateChip.onOpenChange` (via TopBar).
- **TP-COPY:** `errors.ts` entries `chrome_quit_unexpectedly` and `chrome_recover_exhausted`.
- **Verification:** `npm --prefix frontend run typecheck` and `npm --prefix frontend run test:contract` pass. Screenshots of the dialog (light and dark) come from a temporary harness and live outside the repo. No Rust was touched.
