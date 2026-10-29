# S1-PY-MOVE

- Record schema: 4
- Status: IN_PROGRESS
- Record kind: PR_UNIT
- Execution mode: AUTONOMOUS_SPRINT
- Parent control record: ~/Project/cafitac/tools/ssu-lecture-downloader/.dev/features/20261029-windows-platform-split/feature.md
- Sprint ID: windows-platform-split-sprint-1
- Sprint manifest: ../../sprints/sprint-1.md
- Sprint manifest SHA-256: 1388772957c092e529131b0b0a23f7813b818e25551613490033ee4c56d67c04
- Repository: cafitac/ssu-lecture-downloader
- Target/base: develop
- Base ref: origin/develop
- Base decision: repository default integration branch
- Initialization base SHA: 0dfab558429420b0e48c73d59a652cc5632c5ae0
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/s1-py-move (feature/s1-py-move)
- Canonical record: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/s1-py-move/.dev/features/20261029-windows-platform-split/units/S1-PY-MOVE/feature.md
- Design status: APPROVED (parent, sha256 f0c28b02634270935586ad670eee0e755c932220f196f6a229604b932557c8a6)
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Worktree cleanup status: ACTIVE
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT

## Outcome

See sprint-1.md § "S1-PY-MOVE".

## Workflow cursor

- Workflow stage: FEATURE_DEVELOP
- Next eligible action: FEATURE_DEVELOP
- Next action class: LOCAL_CONTINUE
- Gate state: NONE

## Progress and evidence

- 2026-10-29: worktree created from origin/develop 0dfab55 (S0-CI merged).
- 2026-10-29: FEATURE_DEVELOP complete locally (not pushed). `git mv` of 24 modules to `core/` (service.py → `core/lms_service.py`), 9 modules + `routers/` to `service/`; imports (absolute, string monkeypatch paths, PyInstaller hidden import `ssu_lecture_downloader.core.stt_remote`) rewritten. Logger names (`ssu_lecture_downloader.chrome_watch`, `ssu_lecture_downloader.api`) deliberately unchanged.
- Placement notes: `artifacts.py` (not named in design/02) goes to `service/` because it imports `api_models`, `app_context`, `payloads`; `chrome_watch`, `stt_remote_config`, `credentials` moved whole into `core/` (split into `platforms/` is S1-PY-PLATFORMS).
- Compatibility shim: `src/ssu_lecture_downloader/api.py` re-exports `app` from `service.api`, because the Tauri shell pins the uvicorn string `ssu_lecture_downloader.api:app` (debug launch, stale-backend detection, `invariant_tests.rs`). Removable once the Rust side switches to `service.api:app`.
- import-linter: dev dependency + `[tool.importlinter]` contract (core ↛ service, cli); `tests/test_import_boundaries.py` (contract run, AST scan, deliberate-violation fixture); CI step `uv run lint-imports` after pytest.
- P5 carry-over: `default_manager` in `tests/test_macos_invariants.py` is discarded from `cw._LIVE` in a `finally`; assertion kept. No asserted value in the characterization tests changed (imports only).
- Local verification: `UV_LOCKED=1 uv run pytest` 498 passed (495 + 3 new); `uv run lint-imports` 1 kept / 0 broken; sidecar build OK and smoke `GET /api/health` on 127.0.0.1:8765 returned ok.
