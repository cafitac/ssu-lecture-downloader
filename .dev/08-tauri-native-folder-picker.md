# D11 Tauri shell and native download folder UX

Status: completed

User feedback:

- Download folder should not be typed manually in the final app.
- The app should use a Finder/native folder picker.
- Even better: pressing download should work with an automatic safe default path, and folder selection should be optional.
- It is time to QA inside a Tauri desktop app instead of continuing browser-only UX work.

Product decisions:

1. Add a minimal Tauri shell around the existing React/Vite UI.
2. Keep browser prototype support, but treat it as fallback/dev only.
3. In Tauri:
   - initialize the download folder automatically to the macOS login user's `~/Downloads` folder, even when the app is launched from Hermes with `HOME` pointing at `.hermes/profiles/.../home`,
   - expose a `Finder에서 선택` button using Tauri native folder picker,
   - let `다운로드 시작` work without the user manually selecting a path.
4. Keep the local FastAPI backend on `127.0.0.1:8000` for this slice.
   - Tauri process supervision for Python remains the next slice unless this becomes necessary for first QA.
5. The visible text field may remain as read/edit fallback during development, but primary UX must be automatic default + native picker.

Acceptance criteria:

- `frontend` has Tauri config and scripts.
- App source uses Tauri APIs only behind a runtime check so browser dev still works.
- Tauri mode initializes default download directory from the real macOS login user's Downloads folder, not from a Hermes profile HOME.
- UI has a native folder picker button labelled `Finder에서 선택`.
- Download still sends `output_dir` for single/background/batch flows.
- Contract/build tests pass.
- Tauri dev/build configuration is syntactically valid enough to start QA from the app shell.

Verification completed:

- `uv run pytest -q && cd frontend && npm run test:contract && npm run build` => 88 passed, frontend contract ok, Vite build succeeded.
- `cd frontend/src-tauri && cargo check` => passed.
- `cd frontend && npm run tauri -- info` => Tauri 2.11 app detected; rustup warning only because Rust is installed through Homebrew.
- `cd frontend && npm run tauri -- build --debug --no-bundle` => built debug app at `frontend/src-tauri/target/debug/ssu-lecture-downloader`.
- `cd frontend && npm run tauri:dev` => launched macOS process `ssu-lecture-downloader`; current dev setup reuses an existing Vite server on `127.0.0.1:5173` instead of starting a duplicate port.

Deferred after D11/D12:

1. Tauri should start/monitor the Python FastAPI process itself.
2. Replace remaining text-field fallback with a polished folder display/control if desired.
3. D12 download action UX polish changed the default path from `~/Downloads/ssu-lectures` to `~/Downloads`, collapsed start/cancel into one primary button, and moved developer diagnostics behind `고급 정보`.
4. Batch background jobs/cancel parity.
5. Production packaging/signing/notarization.
