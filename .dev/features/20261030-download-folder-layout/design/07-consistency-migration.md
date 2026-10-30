# Consistency, migration and recovery

## Boundaries

- No database and no transaction. Units of consistency:
  - one lecture folder: `.ssu/item.json` + its visible files; outputs keep the CURRENT atomic rules (tmp + `os.replace`; `<stem> 자막.txt` committed last marks a complete transcript set);
  - one week folder's `.ssu/materials.json` + the material files it names;
  - one tidy-up run: its journal file.
- Identity on disk moves from the file-name marker `[content_id]` to `.ssu/item.json.content_id` (lectures) and `materials.json` keys (materials). The marker stays a **read-only** legacy signal.
- Ownership: `LibraryLayout` decides names; `LibraryIndex` answers "what exists for this content id"; `LecturePipeline` (PL-PY-PIPELINE) writes outputs of one lecture; `MigrationRunner` is the only component that moves existing files; `LibraryLock` serialises tidy-up against Python download jobs.

## Name mapping (CURRENT → PROPOSED)

`T` = title in the old name, `id` = content id, `(n)` = optional rename-duplicate marker, `S` = new stem (LMS title, Q3), `W` = week folder, `C` = course folder.

| CURRENT file (in `C/`) | Role | PROPOSED path |
|---|---|---|
| `T [id].mp4` (or other video ext) | video | `C/W/S/S.mp4` |
| `T [id] (n).mp4` | duplicate video (Q5) | `C/W/S/S (n).mp4` |
| `T [id].srt` | srt | `C/W/S/S.srt` |
| `T [id].mp3` (m4a, wav, aac, ogg, flac) | audio | `C/W/S/S 음성.mp3` |
| `T [id].transcript.txt` | text | `C/W/S/S 자막.txt` |
| `T [id].txt` (legacy, no `.transcript.txt` of the same id and `(n)`) | text | `C/W/S/S 자막.txt` |
| `T [id].txt` when `T [id].transcript.txt` also exists | — | left in place, `skip(duplicate_transcript)` |
| `T [id].transcript.md` | md | `C/W/S/.ssu/transcript.md` |
| `T [id].transcript.json` | json | `C/W/S/.ssu/transcript.json` |
| `T [id].pdf` / other non-media ext, id is a material | material | `C/W/<material title>.pdf` (Q2) + `C/W/.ssu/materials.json` |
| `T [id].file` | material | `C/W/<title>.<ext from LMS file_name>` when known, else `.file` kept |
| `T [canvas-<fid>].<ext>` | canvas_file | `C/<week from title "n주차" or 기타>/T.<ext>` |
| `T [id].<x>` whose id is not in the course list (Q6) | by suffix | `C/기타/T/…` (lecture roles) or `C/기타/T.<x>` (material) |
| any `.part` | — | left, `skip(partial_download)` |
| `.tmp` siblings of atomic writes, hidden files, `.DS_Store` | — | ignored silently (not listed) |
| no `[id]` marker / unknown suffix / subfolder / symlink | — | left, `skip(unknown_file \| not_a_regular_file)` |
| files in a course folder not matching a current course | — | left, `skip(course_not_matched)` (one line per folder in the preview) |

New downloads (no legacy file) follow the same right-hand column. Audio for the transcript is derived from the lecture stem, not from the audio file stem, so every role of a lecture shares `S`.

## Legacy name parser

```
^(?P<title>.+?) \[(?P<id>[^\[\]/]+)\](?P<dup> \((?P<n>[0-9]{1,3})\))?(?P<suffix>\.transcript\.(?:txt|md|json)|\.[A-Za-z0-9]{1,10})$
```

- Applied to NFC-normalised names of regular files (lstat) directly inside a course folder.
- `id == "content"` (CURRENT fallback when the LMS gave no id) is not attributable → `unknown_file`.
- The last `[...]` group wins (titles may contain brackets).

## Week folder and stem rules

1. `week_position` integer-like (int or digit string, 0–99) → `f"{n:02d}주차"`; ≥ 100 → `f"{n}주차"`.
2. Else a number from `module_name` by `(\d{1,2})\s*주\s*차` or `(?i)\bweek\s*(\d{1,2})\b` → same format.
3. Else (Q1, recommended) `safe_name(module_name)` when non-empty; otherwise `기타`.
4. Canvas attachments: the page item's week via the request fields; else rule 2 applied to the file title; else `기타`.
- `safe_name`: CURRENT unsafe-character replacement + whitespace collapse + strip ` ._`, then NFC, then byte cap (stem 150 bytes; week/course 120 bytes) on a character boundary; Windows rules add reserved-name suffix `_` and the 240-char budget (stem shortened first).
- Course folder name is unchanged (`safe_filename(course_name)`), so existing course folders stay where they are.

## Collisions and determinism

- Candidates `S`, `S (2)`, `S (3)`, … (no upper bound; the id is never used as a suffix, I1): usable when absent, or present with `item.json.content_id == id`.
- A folder without valid `.ssu/item.json`, or with another id, is never written into.
- Assignment happens in one thread per planning pass, ordered by (`lesson_position` numeric, `content_id`); the lecture directory is created under a per-course lock right after assignment, so parallel downloads (PL-PY-PIPELINE) cannot race for the same suffix.
- A lecture found in the index stays in its folder even if week/title changed in the LMS (R12: no automatic renames).
- Materials: same scheme on file names inside the week folder (`title.pdf`, `title (2).pdf`), identity in `materials.json`.

## Dedup and the legacy fallback

- `LibraryIndex.scan(course_dir)`: `course_dir/*/.ssu/materials.json`, `course_dir/*/*/.ssu/item.json`, and one listing of `course_dir` files parsed by the legacy parser. No deeper recursion, no symlink following, parse bounds (item 64 KiB, materials 256 KiB), invalid → ignored + debug log.
- Role resolution for a content id: new-layout target if it exists, else the legacy file of that role (`.transcript.txt` preferred over legacy `.txt`). `skip` strategy treats an existing role as done. `overwrite` writes the new-layout target and never touches the legacy file.
- `downloaded_path` (course detail, page attachments): video → audio → `자막.txt` → material file → srt, from either layout.
- The fallback is permanent (cheap: one listing); it is what keeps "no re-download" true whether or not the student ever runs the tidy-up.

## Migration run

### Scope and attribution

- Root = the download folder of the request (`output_dir` or the default). Only `root/<course folder>/<file>` entries are candidates. Nothing outside the root, nothing deeper than depth 2, never a symlink target.
- A file is attributable only when the legacy parser matches, the role is known, and its course folder matches a current LMS course (`safe_filename(course.name) == folder`). Everything else stays untouched and is listed with a reason.
- Planning is read-only (lstat, listdir, LMS reads). It records `size`, `mtime_ns`, `inode` per move.

### Lifecycle (owned by `MigrationRunner`, one run at a time)

`PLANNED` (in memory, TTL 30 min, single use) → `APPLYING` (journal open) → `APPLIED` (all moves done) | `PARTIAL` (some skips, or cancelled, or crashed) → optional `UNDONE` | `PARTIALLY_UNDONE`. A run's state is derived from its journal; no other state file.

### Exclusive move

1. `os.link(src, dst)` — atomic, fails with `EEXIST` when `dst` exists; then `os.unlink(src)`.
2. If link is not supported (`EXDEV`, `EPERM`, `ENOTSUP`, `EMLINK`), fall back to: `dst` absent check → `os.rename(src, dst)` while holding the LibraryLock. Residual TOCTOU only against another app creating exactly `dst` in that instant — documented, not reachable by this app.
3. Source and destination are always inside the same course folder → same volume → no copy fallback is ever used (a cross-device rename is reported as `skip`, never copied).
- Crash recovery: `intent` without `done` → the next plan re-reads the disk: source gone + destination with the journaled inode → treated as moved (a `done` line is appended by the next apply's recovery step); both names with the same inode → unlink the source name only; source present and destination absent → planned again normally.

### Journal

- `<root>/.ssu/migrations/<run_id>.jsonl`, created with `O_EXCL`, one JSON object per line, `fsync` after `intent`, `done`, `meta_written` and at close.
- Holds relative paths, sizes, mtimes, inodes, content ids — no file contents. Local only; never sent anywhere; not included in debug traces.
- Retained indefinitely (a few KB per run); the Settings "마지막 정리 되돌리기" targets the newest run that still has restorable `done` lines.

### Undo

- Reverse order of `done` lines; precondition per file: the moved file still has the journaled inode + size + mtime_ns, and the original path is free. Otherwise `undo_skip` with a reason; the run continues.
- `meta_written`: restore previous bytes (merge case) or remove the metadata file the run created.
- `mkdir`: `rmdir` only if empty, never recursive — a lecture folder that received new downloads after the run stays.
- Idempotent: lines with `undo_done` are skipped on a second undo.

### Idempotency of re-runs

- A completed run leaves no attributable file in the course root except skipped ones; the next plan lists only those skips (and no moves). Applying the same `plan_id` twice → `tidy_plan_expired`. Undo twice → no-op.

## Concurrency

| Actor | Guard | Behaviour |
|---|---|---|
| Plan/apply/undo vs Python download, artifact, canvas-file jobs | `LibraryLock.begin_*` checks every download-type `JobRegistry` for an unfinished job | 409 `library_busy` |
| Download/artifact/canvas-file job start during apply/undo | `LibraryLock.check_download_start()` in the start endpoints | 409 `library_tidy_running` |
| Native (Tauri) downloads | invisible to Python; the UI disables "정리하기" while `busy` (same flag that disables "저장 폴더 변경") | UI gate |
| Two tidy-ups | one `tidy_running` flag | 409 `library_busy` |
| PL-PY-PIPELINE lecture chains | suffix assignment single-threaded + per-course mkdir lock; each chain writes only inside its own lecture folder | no cross-lecture writes |
| Plan job | read-only; allowed while nothing else runs (same busy rule, so the preview is not instantly stale) | — |

## Files open in other apps

- macOS: renaming/moving an open file is allowed; players keep playing (the open descriptor follows the inode). Apps that reopen by path (recent-files lists, some editors) will look in the old place. The preview and result say: "열려 있는 파일도 옮겨져요. 재생 중인 영상은 계속 재생되지만, 다시 열 때는 새 폴더에서 열어 주세요."
- No open-file detection on macOS (no reliable cheap API; `lsof` is slow and not needed for safety).
- Windows (later): a move of an open file fails with a sharing violation → `skip(in_use)`; the student can re-run after closing it.

## Compatibility

- Upgrade: legacy files are found by the fallback → no re-download; new downloads go to the new layout; the course notice offers the tidy-up.
- Downgrade to a build without this feature: that build finds only `[id]` names, so lectures stored in the new layout would be downloaded again into the old flat layout (no data loss, only duplicates). Documented for the owner; not mitigated (personal desktop app, auto-update forward only).
- API: additive only (`legacy_file_count`, optional canvas request fields, new `/api/library/*` endpoints, new error codes). Old UI + new backend: works, no tidy-up entry. New UI + old backend: notice hidden (field missing), Settings entry hidden when `/api/library/tidy/runs` is 404.
- Standalone endpoints `/api/stt/jobs`, `/api/media/audio/jobs`, `/api/media/video/ensure-playable` keep today's naming (input path chosen by the caller).
- `MediaPathPolicy` roots unchanged (lecture folders are below the download root).

## Windows (designed now, activated with the Windows platform work)

- Reserved names (`CON PRN AUX NUL COM1–9 LPT1–9`, case-insensitive, with any extension) → `_` suffix; trailing dot/space stripped (already by `safe_name`).
- Total path budget 240 characters: shorten the stem first (min 20 chars), never the course or week folder; if still too long → `skip(path_too_long)` in migration, `path_too_long` error for downloads.
- `.ssu` is not hidden by the dot on Windows → set `FILE_ATTRIBUTE_HIDDEN` on creation (Windows platform hook).
- Hard links on NTFS work (`os.link`); FAT/exFAT use the fallback.

## Failure and cleanup owner map

| Effect / failure | Owner | Recovery | Cleanup |
|---|---|---|---|
| Lecture folder + `item.json` creation | LibraryLayout + Planner (PL-PY-PIPELINE batch plan) | idempotent: same id → same folder next run | none (empty lecture folder after a failed download is reused next time) |
| Visible output files | LecturePipeline (CURRENT atomic writes) | next run reuses complete outputs | CURRENT: intermediate audio, source video written by this job |
| Corrupt/foreign metadata | LibraryIndex | ignored; folder treated as foreign; new lecture gets a suffix | none (user data, never removed) |
| Legacy files | LibraryIndex (read) / MigrationRunner (move) | never modified by downloads | never deleted |
| Tidy-up move | MigrationRunner + ExclusiveMove | revalidation; journal; crash → re-plan finishes | undo per run |
| Directories created by a run | MigrationRunner | — | undo `rmdir` if empty |
| Metadata written by a run | MigrationRunner | — | undo restores previous bytes / removes |
| In-memory plans | MigrationRunner | expired → `tidy_plan_expired`, UI re-plans | TTL drop |
| Mutual exclusion | LibraryLock (Python jobs), UI busy gate (native) | 409 → UI explains and waits | flag reset in `finally` |
| Journal files | MigrationRunner | append-only | kept (owner may delete `.ssu/migrations` by hand) |

## Rollback

- LAYOUT-PY-PATHS revert: new downloads go back to the flat layout; files already in the new layout are not recognised by the reverted build (duplicates possible, no loss).
- LAYOUT-PY-MIGRATION revert: moved files remain valid for LAYOUT-PY-PATHS; journals stay on disk; undo is unavailable until re-applied (the owner can undo before reverting).
- LAYOUT-UI-TIDY revert: no entry point to the tidy-up; backend untouched.
