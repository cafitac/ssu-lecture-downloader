# S1-PY-PLATFORMS

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
- Initialization base SHA: 8a9af96dce2813e547758d61731399004261f5fa
- Worktree/branch: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/s1-py-platforms (feature/s1-py-platforms)
- Canonical record: ~/Project/cafitac/tools/ssu-lecture-downloader-wt/s1-py-platforms/.dev/features/20261029-windows-platform-split/units/S1-PY-PLATFORMS/feature.md
- Design status: APPROVED (parent, sha256 f0c28b02634270935586ad670eee0e755c932220f196f6a229604b932557c8a6)
- Worktree cleanup policy: AUTO_AFTER_SAFE_TERMINAL
- Worktree cleanup status: ACTIVE
- Unit review topology: INHERIT
- Unit review cycle budget: INHERIT

## Outcome

See sprint-1.md § "S1-PY-PLATFORMS".

## Workflow cursor

- Workflow stage: FEATURE_DEVELOP
- Next eligible action: FEATURE_DEVELOP
- Next action class: LOCAL_CONTINUE
- Gate state: NONE

## Progress and evidence

- 2026-10-29: worktree created from origin/develop (S1-PY-MOVE merged as 8a9af96).
- 2026-10-29: implemented. `platforms/{__init__,base,macos,windows}.py` added; macOS bodies moved from core/chrome_watch.py, core/stt_remote_config.py, core/stt.py, service/app_context.py, service/credentials.py. `CredentialPair`/`CredentialStore` moved to `core/credentials.py` (re-exported by `service/credentials.py`) so `platforms` need not import `service`. Core receives `chrome_locator`/`process_tree`/`app_paths` (ChromeWatchManager), `writer`/`path_provider` (RemoteSTTSettingsStore) and `stt_capability` (check_stt_provider, transcribe_audio); `AppContext.platform` defaults to `select_platform()`. import-linter: 4 contracts kept. Local: `UV_LOCKED=1 uv run pytest` 517 passed; `uv run lint-imports` 4 kept; sidecar build + 127.0.0.1:8765 `/api/health` ok. No test_macos_invariants assertion value changed (only import paths / construction fixtures).
- 2026-10-29 (review cycle 1 repair): (1) `stt_capability` is now a required keyword-only parameter of `check_stt_provider`/`transcribe_audio`/`dependency_installer.missing_components`/`install_missing_media_components`; the media router passes `ctx.platform`; test fakes declare the kwarg explicitly; API test covers `create_app(platform=<unsupported>)` -> `platform_supported is False` + Intel-Mac hint. (2) Platform selection lives only in `service/app_context.py` (`build_app_context`); `service/api.py` no longer imports `select_platform`/`Platform`; the AST guard in tests/test_import_boundaries.py also fails on those names (and MacOSPlatform/WindowsPlatform) imported through any module, with a deliberate-violation fixture. (3) Relative `SSU_APP_DATA_DIR` is ignored with a one-time warning; `SSU_CHROME_WATCH_PROFILE_DIR`/`SSU_STT_CONFIG_PATH` are read from the same environ mapping captured by `select_platform` (precedence unchanged). Local: `UV_LOCKED=1 uv run pytest` 522 passed; `uv run lint-imports` 4 kept; sidecar build + 127.0.0.1:8765 `/api/health` ok (build output deleted). Characterization assertion values unchanged.
- Deferred to S2: `ProcessTree` still carries the macOS launcher/background/main-pid model (`spawn(..., background=)`, `find_main_pid`, `chrome_pid` tracking in `ChromeWatchManager`); S2 should let the platform own that model.
