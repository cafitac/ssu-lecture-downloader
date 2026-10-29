# Official viewer QA boundary

Browser/Vite QA and Tauri app QA are intentionally different for lecture watching:

- Browser/Vite runs in the user's normal browser, so it can verify React rendering, FastAPI calls, course/detail loading, and official watch URL planning.
- Tauri opens a separate macOS WKWebView, so it must separately verify native `invoke` wiring, WebKit cookie injection, iframe/LTI initialization, and WebKit media/autoplay policy.

Do not treat Chrome browser success as proof that the Tauri official viewer or autoplay works. The row-level `시청` button is deliberately the minimal official flow: create one `official-viewer` window, set the Python-authenticated LMS cookies in WebKit, then navigate to the validated Canvas module-item launch URL. It must not inject autoplay helpers, debug custom-scheme navigation, `on_navigation` intercepts, or WebKit autoplay policy overrides; those previously caused a blank white official-player window. Autoplay and ended-event capture belong only to the experimental `표시된 영상 순차 시청` assisted path.

Tauri official-viewer regression checks:

```bash
cd frontend/src-tauri
cargo fmt --check
cargo check
cargo test manual_official_viewer_stays_minimal_without_assist_or_debug_hooks
cargo test
cd ..
npm run test:contract
npm run build
```

Manual runtime QA after those gates: restart the Tauri app, use a row-level `시청` button to confirm the official player opens without a white screen, then use `표시된 영상 순차 시청` if testing best-effort autoplay. If autoplay still fails there, inspect the in-app `공식 플레이어 재생 진단 로그`; common causes are no detected video/iframe yet, WebKit `NotAllowedError`, or Commons intro-video-to-main-video handoff.
