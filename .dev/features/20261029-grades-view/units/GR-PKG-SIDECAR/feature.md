# GR-PKG-SIDECAR

- Record schema: 4
- Status: IN_PROGRESS
- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-grades-view/feature.md
- Sprint manifest: ../../sprints/sprint-1.md
- Repository: cafitac/ssu-lecture-downloader (public)
- Target/base: develop
- Base ref: origin/develop
- Initialization base SHA: ca7f167a15dcf9675e7a246c4d263163c17e728d
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/gr-pkg-sidecar (feature/gr-pkg-sidecar)
- Design status: APPROVED (parent)
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT

## Outcome

See ../../sprints/sprint-1.md § "GR-PKG-SIDECAR".

## Progress and evidence

- 2026-10-30: worktree created from origin/develop ca7f167 (GR-PY-USAINT #5 and GR-UI-TAB #6 merged).
- 2026-10-30: implemented. `scripts/build_backend_sidecar.py`: HIDDEN_IMPORTS += `ssu_lecture_downloader.core.grades.gateway`, `rusaint`; new `COLLECT_BINARIES = ["rusaint"]` passed as `--collect-binaries` (rusaint loads `librusaint_ffi.dylib` from `os.path.dirname(__file__)`, so only the native library, kept at `_internal/rusaint/`, is needed; no data files, so `--collect-all` is unnecessary).
- 2026-10-30: AC6 smoke = `scripts/smoke_backend_sidecar.py` (starts the built sidecar on a free 127.0.0.1 port with a throwaway `SSU_APP_DATA_DIR`, requires `GET /api/grades/status` -> `available: true`, terminates it). `available` comes from `RusaintGateway.available()` -> `import rusaint`, which loads the native library, so the HTTP check proves the load without a u-SAINT call. CI: smoke step after "Build backend sidecar" in the macos job; bundle check also asserts `_internal/rusaint/librusaint_ffi.dylib` inside the .app.
- 2026-10-30: local evidence (macOS x86_64): baseline sidecar (before) reports `available: false`; rebuilt sidecar reports `available: true`; sidecar dir 129272 KB -> 141184 KB (+11912 KB, the 12194368-byte dylib). `UV_LOCKED=1 uv run pytest -q` 684 passed; `uv run lint-imports` 4 kept. Build outputs removed. `tauri build` is not run locally (CI only).
