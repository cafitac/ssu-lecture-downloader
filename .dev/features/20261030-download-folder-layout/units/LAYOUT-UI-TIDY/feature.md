# LAYOUT-UI-TIDY

- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261030-download-folder-layout/feature.md
- Sprint manifest: ../../sprints/sprint-1.md (sha256 f18c3c72…)
- Target/base: develop (origin/develop d697c33)
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/layout-ui-tidy (feature/layout-ui-tidy)
- Design status: APPROVED (parent 63b53a26…)
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Merge gate: LAYOUT-PY-MIGRATION MERGED, then rebase

## Progress and evidence

- 2026-10-30: worktree created. Built against design/09 § 내부 API 계약 (frozen), not against the unfinished LAYOUT-PY-MIGRATION backend.
- 2026-10-30: implemented (local commit, not pushed). Frontend only (`frontend/**`), no Python, no Rust.
  - `lib/tidy.ts` (pure): dialog phases, design/09 copy, skip-reason table, course › week › lecture grouping, row cap ("n개 더 있어요", 300), job outcome mapping (expired plan → re-preview, stopped move still reports, stopped preview closes), undo-able run.
  - `hooks/useLibraryTidy.ts`: plan / apply / undo jobs polled with `usePolledJob`; `tidy_plan_expired` (HTTP 409 at start or as a failed job) re-previews by itself with the note "미리보기가 오래돼서 다시 확인했어요."; run list from `GET /api/library/tidy/runs` (HTTP 404 hides the Settings entries); closing is ignored while moving.
  - `components/TidyDialog.tsx`: 확인 중, 미리보기, 정리할 파일 없음, 옮기는 중 ({done}/{total}, 멈추기), 결과 (폴더 열기, 되돌리기, 닫기), 되돌린 결과, 오류. Focus moves to the new state's lead line after each step.
  - Course-pane notice (`LecturePane`, from `legacy_file_count`; 나중에 is kept in memory until restart); Settings › 저장 폴더 entries (`SettingsDialog`); both disabled with "다운로드가 끝나면 정리할 수 있어요" while a download or an attachment save runs (`downloads.active`, which includes native jobs).
  - `errors.ts`: `library_busy`, `library_tidy_running`, `tidy_plan_expired`, `library_conflict`, `path_too_long`.
  - Attachment (Canvas) downloads send the page item's `module_name` / `week_position` (`usePageAttachments` → `downloadCanvasFile`, appended optional parameter).
  - Additive touches outside the owned list, each a few lines: `Dialog.tsx` (`wide`, `hideClose`), `lib/trackedWork.ts` (`libraryTidying`, so an app update waits for a running tidy-up), `hooks/useCourseContent.ts` (`legacyFileCount` from the course detail).
- Tests: one appended block in `frontend/scripts/contract-test.mjs` (append-only): design/09 copy and reason table, job outcome mapping, grouping/cap/undo-able run, every TidyDialog state rendered with real React, request shapes of the frozen contract incl. the Canvas week fields, and the expired-preview re-plan through the hook.
- Verification: `npm run typecheck`, `npm run test:contract` green. Screenshots of every state, light and dark, from a mocked backend are under `.local/ui-tidy/shots/` (untracked).

## Contract assumptions the backend must match

- Plan, apply and undo jobs use the usual job shape `{job_id, status, cancel_requested, progress, result, error_code}`; polled at `GET /api/library/tidy/{plan|apply|undo}/jobs/{job_id}`, cancelled at `POST …/{job_id}/cancel`.
- Apply progress is the newest event `{stage: "tidy_moving", done, total}` in `progress`.
- `moves[].from` / `to` and `skips[].path` end in the file name (the dialog shows the last path segment only); `open_files_note` is not rendered (the frozen copy is used).
- A stopped (cancelled) apply job still carries `result`; `undo_available` there decides the 되돌리기 button.
- `tidy_plan_expired` arrives as HTTP 409 `error_code` when apply starts (or as a failed job `error_code`); plan/apply/undo start errors `library_busy` / `library_tidy_running` as HTTP 409.
- Course detail adds `legacy_file_count`; absent means no notice. `GET /api/library/tidy/runs` answering HTTP 404 hides the Settings entries.
- Undo from Settings is immediate (no extra confirmation); undo shows no per-file progress.
