import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import vm from 'node:vm';
import { spawnSync } from 'node:child_process';

function readTree(dir, extensions) {
  return readdirSync(dir, { withFileTypes: true })
    .flatMap((entry) => (entry.isDirectory() ? readTree(join(dir, entry.name), extensions) : [join(dir, entry.name)]))
    .filter((file) => extensions.some((extension) => file.endsWith(extension)))
    .sort();
}
const requiredFiles = [
  'src/main.tsx',
  'src/App.tsx',
  'src/styles.css',
  'src/styles/tokens.css',
  'src/lib/api.ts',
  'src/lib/desktop.ts',
  'src/lib/types.ts',
  'src/lib/polling.ts',
  'src/lib/progress.ts',
  'src/lib/jobItems.ts',
  'src/lib/errors.ts',
  'src/lib/content.ts',
  'src/lib/http.ts',
  'src/lib/postprocess.ts',
  'src/hooks/usePolledJob.ts',
  'src/hooks/useBackendStatus.ts',
  'src/hooks/useTauriEvent.ts',
  'src/hooks/useLmsSession.ts',
  'src/hooks/useDownloads.ts',
  'src/hooks/useOfficialViewer.ts',
  'src/hooks/useLmsPages.ts',
  'src/hooks/usePageAttachments.ts',
  'src/lib/watch.ts',
  'src/components/WatchStrip.tsx',
  'src/components/PageAttachments.tsx',
  'src-tauri/src/watch_queue.rs',
  'src-tauri/src/watch_runtime.rs',
  'src/components/TopBar.tsx',
  'src/components/CourseSidebar.tsx',
  'src/components/LecturePane.tsx',
  'src/components/LectureList.tsx',
  'src/components/TaskList.tsx',
  'src/components/DeadlinesPane.tsx',
  'src/lib/deadline.ts',
  'src/hooks/useDeadlines.ts',
  'src/components/GradesPane.tsx',
  'src/hooks/useGrades.ts',
  'src/lib/grades.ts',
  'src/styles/grades.css',
  'src/hooks/useReminders.ts',
  'src/hooks/useRemoteStt.ts',
  'src/lib/remoteStt.ts',
  'src/lib/reminders.ts',
  'src-tauri/src/reminders.rs',
  'src-tauri/src/platform/mod.rs',
  'src-tauri/src/platform/macos.rs',
  'src-tauri/src/platform/windows.rs',
  'src-tauri/src/platform/macos/launch_agent.rs',
  'src/components/ActionBar.tsx',
  'src/components/DownloadDrawer.tsx',
  'src/components/SettingsDialog.tsx',
  'src/components/Screens.tsx',
  'src-tauri/src/lib.rs',
  'src-tauri/src/backend.rs',
  'src-tauri/src/native_download.rs',
  'src-tauri/src/update.rs',
  'src-tauri/src/viewer.rs',
  'src-tauri/src/assets/watch_assist.js',
  'vite.config.ts',
  'src-tauri/tauri.conf.json',
  'src-tauri/Cargo.toml',
  'src-tauri/src/main.rs',
  'scripts/dev-or-reuse-vite.mjs',
];

for (const file of requiredFiles) {
  if (!existsSync(file)) {
    throw new Error(`Missing required UI file: ${file}`);
  }
}

const api = readFileSync('src/lib/api.ts', 'utf8');
for (const endpoint of ['/api/credentials/status', '/api/credentials', '/api/courses', '/api/courses/', '/api/deadlines', '/api/watch/viewer-plan', '/api/lms/session-bridge', '/pages/', '/api/download/canvas-file', '/api/download/jobs', '/api/download/batch/jobs', '/api/download/plan', '/api/download', '/api/download/batch/plan', '/api/download/batch', '/api/media/audio/jobs', '/api/stt/providers/status', '/api/media/dependencies/status', '/api/media/dependencies/install/jobs', '/api/stt/jobs']) {
  if (!api.includes(endpoint)) {
    throw new Error(`API client missing endpoint ${endpoint}`);
  }
}

// The UI is split across App.tsx, components/, hooks/ and the pure helpers in lib/;
// the copy/behavior checks below look at all of them together.
const uiSourceFiles = [
  'src/main.tsx',
  'src/App.tsx',
  ...readTree('src/components', ['.tsx']),
  ...readTree('src/hooks', ['.ts']),
  'src/lib/progress.ts',
  'src/lib/jobItems.ts',
  'src/lib/remoteStt.ts',
  'src/lib/errors.ts',
  'src/lib/polling.ts',
  'src/lib/format.ts',
  'src/lib/content.ts',
  'src/lib/watch.ts',
];
const main = uiSourceFiles.map((file) => readFileSync(file, 'utf8')).join('\n');
const componentsSource = readTree('src/components', ['.tsx']).map((file) => readFileSync(file, 'utf8')).join('\n');
const css = readTree('src/styles', ['.css']).map((file) => readFileSync(file, 'utf8')).join('\n') + readFileSync('src/styles.css', 'utf8');
if (readFileSync('src/main.tsx', 'utf8').split('\n').length > 20) {
  throw new Error('main.tsx must stay a thin entry point');
}

// New UI copy.
for (const text of [
  '강의 다운로더', '이번 학기', '지난 학기', 'SSU Lecture Downloader', 'week-tag', '과목 {courses.length}개', '안 본 강의', '모두 시청함', '영상 없음', '안 본 것만', '순서대로 시청',
  '시청함', '안 봄', '열리기 전', '다가오는 기한', '과제·퀴즈', '기한 지남', '모두 시청함', '예정', '받음', '전체 선택', '받을 항목을 선택하세요', '개 선택', '다운로드', '모두 취소', '다시 시도', '폴더 열기',
  '대기', '받는 중', '음성 추출 중', '자막 만드는 중', '완료', '실패',
  '저장 폴더', '변경', '음성·자막 구성요소', '설치', '앱을 끄면 로그인 정보가 지워져요', '로그아웃',
  '디스크에 저장하지 않고 앱이 켜져 있는 동안 메모리에만 보관합니다', '로그인',
]) {
  if (!main.includes(text)) throw new Error(`UI missing required Korean copy: ${text}`);
}

// Developer jargon and removed concepts must stay out of the UI.
for (const forbidden of [
  '명시 선택 UX', 'SOONGSIL LMS', 'Soongsil LMS local', 'modules ', 'lectures ', 'readystream', 'Tauri 앱은', '앱 연결됨', '앱 연결 확인 중',
  '현재 학기 새로고침', '선택한 학기 불러오기', '표시된 영상 순차 시청', '영상만', '강의 선택', '완료 정보 없음',
  '다운로드 진단 로그', '응답 진단', 'credential-panel', 'postprocess-card', '후처리', 'Finder에서 선택', '자동 기본값',
  'Python API', 'dry_run', 'byte progress',
]) {
  if (main.includes(forbidden)) throw new Error(`UI must not expose old/developer text: ${forbidden}`);
}
if (/\{[^{}]*\b(error_code|errorCode|failure|reasonCode)\b[^{}]*\}/.test(componentsSource)) {
  throw new Error('Components must never render raw error codes');
}

// Layout: one scroll area (the lecture list), tokens, dark mode, flat style.
if (!/body\s*\{[^}]*overflow:\s*hidden/.test(css)) throw new Error('The page itself must not scroll');
if (!/\.lecture-scroll\s*\{[^}]*overflow-y:\s*auto/.test(css)) throw new Error('The lecture list must be the scrolling region');
for (const token of ['--accent', '--success-bg', '--warning-bg', '--danger-bg', '--sp-1', '--sp-8', '--radius-sm', '--radius-md', '--fs-12', '--fs-20', '--fw-semibold']) {
  if (!css.includes(`${token}:`)) throw new Error(`Design token missing: ${token}`);
}
if (!css.includes('prefers-color-scheme: dark')) throw new Error('Dark mode must be supported');
if (/linear-gradient|radial-gradient/.test(css)) throw new Error('Flat visual system: no gradients');
if (!css.includes('--row-h: 36px') || !/\.lecture-row\s*\{[^}]*min-height:\s*var\(--row-h\)/.test(css)) throw new Error('Lecture rows must be at least 32px tall');
if (!main.includes('<label className={`lecture-row')) throw new Error('The whole lecture row must be a click target for its checkbox');
if (!main.includes('indeterminate')) throw new Error('Select-all must be tri-state');
if ((readFileSync('src/components/ActionBar.tsx', 'utf8').match(/btn-primary/g) ?? []).length !== 1) {
  throw new Error('The action bar must contain exactly one accent-filled button (다운로드)');
}
// Screens.tsx (login) and GradesPane.tsx (the one consent step, shown instead of the course view) may carry one accent button each.
// While a download runs the ActionBar's 다운로드 button can show next to the consent button; that coexistence is accepted.
// TidyDialog.tsx is a modal: its commit button (정리하기, shown over the workspace) is the one accent button of that dialog.
for (const file of readTree('src/components', ['.tsx']).filter((name) => !name.endsWith('ActionBar.tsx') && !name.endsWith('Screens.tsx') && !name.endsWith('GradesPane.tsx') && !name.endsWith('TidyDialog.tsx'))) {
  if (readFileSync(file, 'utf8').includes('btn-primary')) throw new Error(`${file}: no other accent-filled button may compete with the download button`);
}
if ((readFileSync('src/components/GradesPane.tsx', 'utf8').match(/btn-primary/g) ?? []).length !== 1) {
  throw new Error('The grades pane may contain exactly one accent-filled button (동의하고 성적 보기)');
}

if ((readFileSync('src/components/TidyDialog.tsx', 'utf8').match(/btn-primary/g) ?? []).length !== 1) {
  throw new Error('The tidy dialog must contain exactly one accent-filled button (정리하기)');
}

// Session / data flow.
if (!main.includes("type SessionPhase = 'checking' | 'signed-out' | 'signed-in'") || !main.includes('로그인 상태를 확인하는 중이에요')) {
  throw new Error('The workspace must stay hidden until stored credentials were verified by a real LMS request');
}
if (!main.includes('await fetchTerms()') || !main.includes('await loadCourses(currentTermId)')) {
  throw new Error('Initial load must use the current term id from /api/terms');
}
if (main.includes('loadCourses(null)')) throw new Error('Initial load must not call loadCourses(null)');
if (!main.includes('isAuthenticationError') || !main.includes('expire(')) throw new Error('Authentication failures must return to the login screen');
if (!main.includes('usePolledJob(job') || !main.includes('fetch: fetchDownloadJob')) throw new Error('UI must auto-poll the active download job');
const pollingSource = readFileSync('src/lib/polling.ts', 'utf8');
if (/\bsetInterval\b/.test(main) || /\bsetInterval\b/.test(pollingSource) || !pollingSource.includes('setTimeout')) {
  throw new Error('Polling must be a setTimeout chain (no setInterval) so requests never overlap or arrive out of order');
}
if (!main.includes('fetch: fetchMediaDependencyInstallJob')) throw new Error('The dependency install job must be polled through usePolledJob');
if (!api.includes('withArtifactJobId') || !api.includes('return withArtifactJobId(job);') || !api.includes('/api/download/artifacts/jobs/${jobId.slice')) {
  throw new Error('Artifact download jobs must keep their artifact-prefixed job id across polling/cancel so MP3/TXT progress does not disappear');
}
if (api.includes('failed_fallback_backend_job') || api.includes('startBackendBatchDownloadJob') || api.includes('startBackendDownloadJob')) {
  throw new Error('Tauri native download start must not hide failures behind backend fallback jobs');
}
if (!api.includes('native_plan_failed:${code}') || !api.includes('native_batch_plan_failed:${code}')) {
  throw new Error('Tauri native plan failures must raise explicit item-level errors instead of ambiguous fallback behavior');
}
if (main.includes('planDownload(') || main.includes('planBatchDownload(')) throw new Error('Download start must not make duplicate LMS preflight calls for conflict checking');
if (!main.includes("'skip'")) throw new Error('Download conflict policy must be explicit: reuse existing files (skip)');
if (!api.includes('overwrite_artifacts: false')) throw new Error('Main MP3/TXT artifact flow should reuse existing derived audio/transcript outputs by default');
if (!main.includes('startArtifactDownloadJob') || !main.includes("outputs[0] === 'mp4'")) throw new Error('MP4-only downloads use the plain job; MP3/TXT use the artifact job');
if (!main.includes('planOfficialViewerSessionBridge') || !main.includes('openOfficialViewer') || !main.includes('official-watch-ended') || !main.includes('watchQueueStart') || !main.includes('watch-queue-changed')) {
  throw new Error('Row play keeps using the official viewer bridge; sequential watch runs in the desktop shell queue and advances on the real ended event');
}
if (!main.includes('componentsFor') || !main.includes("formats.includes('txt')")) throw new Error('Missing-dependency gating must be per selected output format');
if (!main.includes('~/Downloads') || main.includes('~/Downloads/ssu-lectures')) throw new Error('Default download destination must be the real ~/Downloads');
if (!main.includes('defaultDownloadDirectory()') || !main.includes('pickDownloadDirectory()')) throw new Error('UI must initialize a native default folder and offer the native folder picker');
if (main.includes('macOS Keychain에 저장했습니다') || main.includes('macOS Keychain 상태')) throw new Error('UI must not frame Keychain as the primary user-facing session model');
if (!main.includes('Whisper large-v3') || !api.includes("stt_model: 'large-v3'") || !api.includes("model = 'large-v3'")) throw new Error('TXT must run the large-v3 STT path by default');

if (!api.includes('output_dir') || !api.includes('outputDir')) {
  throw new Error('API client must pass the selected output_dir from the UI');
}
if (!api.includes('sso_redirect_missing') || !api.includes('숭실 SmartID 로그인은 응답했지만 LMS 이동 주소를 찾지 못했습니다') || !api.includes('처리 중 오류가 발생했습니다: ${code}')) {
  throw new Error('API client must translate low-level login error codes into clear Korean user messages');
}
if (!api.includes('payload?.message ?? errorMessage(code)')) {
  throw new Error('API client must prefer backend-provided error messages for detailed LMS/native-plan failures');
}
const desktop = readFileSync('src/lib/desktop.ts', 'utf8');
// The Rust shell is split into modules; marker checks look at all of them (plus the embedded JS asset).
const tauriRustFiles = readTree('src-tauri/src', ['.rs', '.js']);
const tauriLib = tauriRustFiles.map((file) => readFileSync(file, 'utf8')).join('\n');
const tauriProductionLib = tauriRustFiles
  .filter((file) => file.endsWith('.rs'))
  .map((file) => readFileSync(file, 'utf8').split('#[cfg(test)]')[0])
  .join('\n');
for (const marker of ['isTauriRuntime', 'pickDownloadDirectory', 'openDownloadedFolder', 'defaultDownloadDirectory', 'backendSupervisionStatus', 'backendApiBaseUrl', 'backendApiConnection', 'restartBackend', 'downloadDir()', 'invoke<string>(\'real_download_dir\')', 'invoke<BackendSupervisionStatus>(\'backend_supervision_status\')', 'invoke<string>(\'backend_api_base_url\')', 'backend_api_connection', 'backend_restart', 'invoke<void>(\'open_path_in_finder\'', 'open({']) {
  if (!desktop.includes(marker)) {
    throw new Error(`Desktop bridge missing native folder marker: ${marker}`);
  }
}
if (!tauriLib.includes('real_download_dir') || !tauriLib.includes('getpwuid') || !tauriLib.includes('invoke_handler')) {
  throw new Error('Tauri app must expose a real macOS user Downloads command that is not derived from Hermes HOME');
}
for (const marker of ['BackendManager', 'start_in_background', 'backend_health_check', 'backend_health_response_is_compatible', 'artifact_download_jobs', 'stt_provider_status', 'backend_launch_command', 'choose_backend_port', 'backend_api_base_url', 'uvicorn', 'backend_supervision_status']) {
  if (!tauriLib.includes(marker)) {
    throw new Error(`Tauri backend supervision missing marker: ${marker}`);
  }
}
if (!tauriLib.includes('fn terminate_process_group') || !tauriLib.includes('.process_group(0)') || !tauriLib.includes('libc::killpg') || !tauriLib.includes('mode: String') || !tauriLib.includes('port: u16')) {
  throw new Error('Tauri-managed backend must track supervision mode/dynamic port and clean up only its owned child process group');
}
if (!tauriLib.includes('tauri::RunEvent::ExitRequested') || !tauriLib.includes('tauri::RunEvent::Exit') || !tauriLib.includes('shutdown_backend')) {
  throw new Error('Tauri must kill the managed backend process group on every app exit path, not only through Drop');
}
if (/ensure_started\(\)\?/.test(tauriProductionLib) || /\.expect\("error while running/.test(tauriProductionLib)) {
  throw new Error('Tauri setup must not block on or panic over backend startup; start it on a background thread and report state');
}
for (const state of ['Starting', 'Ready', 'Failed']) {
  if (!tauriLib.includes(`    ${state},`)) throw new Error(`Backend supervision state ${state} is missing`);
}
if (!tauriLib.includes('BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs(90)') || !tauriLib.includes('child.try_wait()') || !tauriLib.includes('app_log_dir()') || !tauriLib.includes('backend.log')) {
  throw new Error('Backend startup must allow 90s while the child is alive, fail fast when it exits, and write child output to a log file under the app log dir');
}
if (!desktop.includes("invoke<void>('backend_restart')")) {
  throw new Error('The UI must be able to retry a failed backend start');
}
if (!tauriLib.includes('SSU_API_TOKEN') || !tauriLib.includes('X-SSU-Token') || !tauriLib.includes('/dev/urandom')) {
  throw new Error('Tauri must hand a random per-launch token to the managed backend and send it on native backend calls');
}
if (!tauriLib.includes('cfg!(debug_assertions)') || !tauriLib.includes('"uvicorn"')) {
  throw new Error('Debug builds must run the backend from source with uv instead of a stale packaged sidecar');
}
if (!tauriLib.includes('resolve_sidecar_executable') || !tauriLib.includes('--onedir')) {
  throw new Error('Packaged launch must support the PyInstaller onedir layout (ssu-api/ssu-api) and the legacy single file');
}
if (!api.includes('buildRequestHeaders') || !readFileSync('src/lib/http.ts', 'utf8').includes("'X-SSU-Token'")) {
  throw new Error('The API client must send X-SSU-Token on every request when the Tauri-managed backend gave it a token');
}
if (!tauriLib.includes('SSU_BACKEND_AUTOSTART') || !tauriLib.includes('Some("0")')) {
  throw new Error('Tauri backend autostart must have an environment disable switch for debugging');
}
if (!tauriLib.includes('open_path_in_finder') || !tauriLib.includes('std::process::{Child, Command') || !tauriLib.includes('Command::new("open")')) {
  throw new Error('Tauri app must expose a command that opens the downloaded file folder in Finder');
}
const capabilities = readFileSync('src-tauri/capabilities/default.json', 'utf8');
if (!desktop.includes('openOfficialViewer') || !desktop.includes('WebviewWindow') || !desktop.includes('OFFICIAL_VIEWER_LABEL') || !desktop.includes('getByLabel(OFFICIAL_VIEWER_LABEL)') || !desktop.includes('setFocus()') || !tauriLib.includes('open_official_viewer_with_session') || tauriLib.includes('official_watch_autoplay_script()') || !capabilities.includes('core:webview:allow-create-webview-window')) {
  throw new Error('The official Commons viewer must open/focus in a single app-owned Tauri WebView window without autoplay/debug scripts in the default watch flow');
}
// Sequential watch (intentional change): one small always-on-top playback window owned by the
// desktop shell, a queue held in Rust, advancing only on the real video ended event.
if (!desktop.includes("invoke<WatchQueueState>('watch_queue_start'") || !desktop.includes("'watch_queue_stop'") || !desktop.includes("'watch_queue_next'") || !desktop.includes("'watch_queue_status'") || desktop.includes('open_assisted_official_viewer') || !tauriLib.includes('initialization_script_for_all_frames(official_watch_assist_script())') || !tauriLib.includes("internals.invoke(SIGNAL_COMMAND") || !tauriLib.includes('fn watch_signal(') || !tauriLib.includes("video.addEventListener('ended'") || tauriLib.includes('TargetUrl')) {
  throw new Error('Sequential watch mode must run through the shell queue with an assisted viewer that advances only after the real video ended event');
}
// Decision (frontend/README.md "Important watch-mode split" + the Rust regression tests): the official
// viewer never overrides WKWebView autoplay policy. A custom WKWebViewConfiguration previously produced a
// blank white official-player window, so the assisted path only injects the narrow video.play() helper.
for (const forbidden of ['official_viewer_autoplay_configuration', 'with_webview_configuration', 'setMediaTypesRequiringUserActionForPlayback', 'WKWebViewConfiguration', 'objc2_web_kit']) {
  if (tauriProductionLib.includes(forbidden)) {
    throw new Error(`Official viewer must not install a WebKit autoplay/configuration override (${forbidden}); see frontend/README.md`);
  }
}
if (!tauriLib.includes('fn with_watch_assist') || !tauriLib.includes('fn session_viewer_builder') || !tauriLib.includes('include_str!("assets/watch_assist.js")')) {
  throw new Error('Viewer commands must share one window builder and load the watch helper from assets/watch_assist.js');
}
if (!tauriLib.includes('format!("{VIEWER_LABEL_PREFIX}-{id}")')) {
  throw new Error('Each official viewer open must use a unique window label so close() never races the next build');
}
// Opening an assignment/quiz page in the system browser is an explicit user action (TaskList); only playback must stay in-app.
if (readFileSync('src/hooks/useOfficialViewer.ts', 'utf8').includes('openUrlInBrowser') || main.includes('기본 브라우저로 대체됩니다')) {
  throw new Error('Tauri watch flow must not silently fall back to the external browser; keep playback inside the app and report in-app viewer errors');
}

const vite = readFileSync('vite.config.ts', 'utf8');
const packageJson = readFileSync('package.json', 'utf8');
const tauriConfig = readFileSync('src-tauri/tauri.conf.json', 'utf8');
const tauriCargo = readFileSync('src-tauri/Cargo.toml', 'utf8');
if (!packageJson.includes('tauri') || !packageJson.includes('@tauri-apps/plugin-dialog') || !packageJson.includes('build:backend-sidecar')) {
  throw new Error('package.json must include Tauri scripts/dependencies, backend sidecar build, and dialog plugin');
}
if (!tauriConfig.includes('resources/backend-sidecar') || !tauriLib.includes('packaged_sidecar_executable') || !tauriLib.includes('managed-sidecar') || !tauriLib.includes('SSU_BACKEND_EXECUTABLE')) {
  throw new Error('Tauri packaged builds must bundle and prefer the Python backend sidecar while retaining explicit sidecar smoke-test override');
}
if (!tauriConfig.includes('SSU Lecture Downloader') || !tauriConfig.includes('beforeDevCommand') || !tauriConfig.includes('dev-or-reuse-vite') || !tauriConfig.includes('frontendDist')) {
  throw new Error('Tauri config must define app identity, dev command, and frontend dist');
}
if (!tauriCargo.includes('tauri-plugin-dialog') || !tauriLib.includes('tauri_plugin_dialog::init')) {
  throw new Error('Tauri Rust app must initialize the dialog plugin');
}
if (!vite.includes("'/api'") || !vite.includes('127.0.0.1:8000') || !api.includes('backendApiConnection') || !api.includes('await apiUrl(url)')) {
  throw new Error('Vite browser dev keeps /api -> 127.0.0.1:8000, while Tauri API calls use the dynamic backend base URL');
}


// ---------------------------------------------------------------------------
// Behavior tests for the framework-free UI logic (Node strips the TypeScript types).
// ---------------------------------------------------------------------------
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const { startPolling } = await import('../src/lib/polling.ts');
const { buildRequestHeaders, API_TOKEN_HEADER } = await import('../src/lib/http.ts');
const { reportWithPostprocess } = await import('../src/lib/postprocess.ts');
const progress = await import('../src/lib/progress.ts');

// Polling: sequential (never overlapping), stops at the first terminal value.
{
  let inFlight = 0;
  let maxInFlight = 0;
  let calls = 0;
  const seen = [];
  startPolling({
    intervalMs: 3,
    initialDelayMs: 0,
    fetch: async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await sleep(15);
      inFlight -= 1;
      calls += 1;
      return calls;
    },
    onUpdate: (value) => { seen.push(value); },
    isTerminal: (value) => value >= 4,
  });
  await sleep(300);
  assert.equal(maxInFlight, 1, 'polling requests must never overlap');
  assert.deepEqual(seen, [1, 2, 3, 4], 'responses are applied in order and polling stops at the terminal value');
  assert.equal(calls, 4, 'no request after the terminal value');
}

// Polling: a response that is in flight when the poller is cancelled is dropped (no stale writes).
{
  const updates = [];
  let finishFetch;
  const cancel = startPolling({
    intervalMs: 3,
    initialDelayMs: 0,
    fetch: () => new Promise((resolve) => { finishFetch = resolve; }),
    onUpdate: (value) => { updates.push(value); },
    isTerminal: () => false,
  });
  await sleep(20);
  cancel();
  finishFetch('stale');
  await sleep(30);
  assert.deepEqual(updates, [], 'stale response after cancel must not reach onUpdate');
}

// Polling: nothing is requested after cancel, errors do not stop the chain.
{
  let calls = 0;
  const errors = [];
  const cancel = startPolling({
    intervalMs: 3,
    initialDelayMs: 0,
    fetch: async () => {
      calls += 1;
      if (calls < 3) throw new Error(`boom ${calls}`);
      return calls;
    },
    onUpdate: () => {},
    onError: (error) => { errors.push(error.message); },
    isTerminal: (value) => value >= 3,
  });
  await sleep(100);
  assert.deepEqual(errors, ['boom 1', 'boom 2']);
  assert.equal(calls, 3);
  cancel();
  const neverStarted = startPolling({ intervalMs: 5, fetch: async () => { throw new Error('should not run'); }, onUpdate: () => {}, isTerminal: () => false });
  neverStarted();
  await sleep(30);
}

// API token header: sent when a managed backend gave us one, absent otherwise, caller headers preserved.
{
  const managed = buildRequestHeaders('abc123', { 'X-Extra': '1' });
  assert.equal(managed.get(API_TOKEN_HEADER), 'abc123');
  assert.equal(managed.get('content-type'), 'application/json');
  assert.equal(managed.get('x-extra'), '1');
  assert.equal(buildRequestHeaders(null).has(API_TOKEN_HEADER), false, 'external/Vite backends get no token');
}

// Native MP4 post-processing is a distinct non-terminal state until it finishes.
{
  const entries = new Map();
  const succeeded = { job_id: 'native-1', status: 'succeeded', cancel_requested: false, progress: [{ stage: 'download_done' }], result: { ok: true, output_path: '/x/a.mp4' }, error_code: null };
  let finish;
  const hooks = {
    needsPostprocess: (job) => job.result?.output_path?.endsWith('.mp4') === true,
    run: (job, events) => new Promise((resolve) => {
      events.push({ stage: 'video_playable_check_started' });
      finish = () => resolve({ ...job, progress: events, result: { ...job.result, playable_checked: true } });
    }),
  };
  const first = reportWithPostprocess(entries, succeeded, hooks);
  assert.equal(first.status, 'running', 'succeeded native job must not look finished while post-processing');
  assert.equal(first.result, null);
  assert.equal(progress.TERMINAL_DOWNLOAD_STATUSES.has(first.status), false);
  const second = reportWithPostprocess(entries, succeeded, hooks);
  assert.equal(second.status, 'running');
  assert.ok(second.progress.some((event) => event.stage === 'video_playable_check_started'));
  finish();
  await sleep(5);
  const done = reportWithPostprocess(entries, succeeded, hooks);
  assert.equal(done.status, 'succeeded');
  assert.equal(done.result.playable_checked, true);
  assert.equal(reportWithPostprocess(entries, succeeded, hooks), done, 'final result is stable across polls');

  const pdf = { ...succeeded, job_id: 'native-2', result: { ok: true, output_path: '/x/a.pdf' } };
  assert.equal(reportWithPostprocess(new Map(), pdf, hooks).status, 'succeeded', 'jobs without MP4 output skip post-processing');
  const failed = { ...succeeded, job_id: 'native-3', status: 'failed' };
  assert.equal(reportWithPostprocess(new Map(), failed, hooks), failed);

  const crashing = reportWithPostprocess(new Map(), { ...succeeded, job_id: 'native-4' }, { needsPostprocess: () => true, run: async () => { throw new Error('x'); } });
  assert.equal(crashing.status, 'running');
}
{
  const entries = new Map();
  const job = { job_id: 'native-9', status: 'succeeded', cancel_requested: false, progress: [], result: { ok: true, output_path: '/x/a.mp4' }, error_code: null };
  const hooks = { needsPostprocess: () => true, run: async () => { throw new Error('unexpected'); } };
  reportWithPostprocess(entries, job, hooks);
  await sleep(10);
  const outcome = reportWithPostprocess(entries, job, hooks);
  assert.equal(outcome.status, 'failed', 'an unexpected post-process crash ends as failed instead of hanging');
}

// Friendly errors: known codes become plain Korean, raw codes never leak.
{
  const { friendlyError, describeErrorCode, knownErrorText, GENERIC_ERROR } = await import('../src/lib/errors.ts');
  for (const code of ['network_error', 'network_timeout', 'session_expired', 'media_path_not_allowed', 'internal_error', 'HTTP 404', 'HTTP 500', 'native_plan_failed:network_error', 'lms_request_failed:step:502', 'download dir create failed: x']) {
    const text = describeErrorCode(code);
    assert.ok(/[가-힣]/.test(text), `${code} must map to Korean text`);
    assert.ok(!text.includes('_'), `${code} must not leak a raw code`);
  }
  assert.notEqual(describeErrorCode('network_error'), describeErrorCode('network_timeout'));
  assert.equal(knownErrorText('totally_unknown_code'), null);
  const originalWarn = console.warn;
  console.warn = () => {};
  assert.equal(describeErrorCode('totally_unknown_code'), GENERIC_ERROR);
  assert.equal(friendlyError({ code: 'weird_code_xyz', message: 'weird_code_xyz' }), GENERIC_ERROR);
  assert.ok(friendlyError(new TypeError('Failed to fetch')).includes('인터넷'));
  assert.equal(friendlyError('Finder를 열 수 없습니다: x'), 'Finder를 열 수 없습니다: x');
  assert.equal(friendlyError(new Error('boom')), GENERIC_ERROR);
  console.warn = originalWarn;
}

// Per-item rows for the progress drawer.
{
  const { buildJobRows, summarizeRows, isRetryable, isExpiredMediaUrlCode } = await import('../src/lib/jobItems.ts');
  // CDN rejections of a native transfer are re-planned once instead of logging the user out.
  assert.equal(isExpiredMediaUrlCode('HTTP 403'), true);
  assert.equal(isExpiredMediaUrlCode('HTTP 410'), true);
  assert.equal(isExpiredMediaUrlCode('session_expired'), false);
  assert.equal(isExpiredMediaUrlCode(null), false);
  const item = (id) => ({ id: String(id), rawId: id, title: `T${id}`, sizeBytes: 100 });
  const job = (status, events, extra = {}) => ({ job_id: 'native-1', status, cancel_requested: false, progress: events, result: null, error_code: null, ...extra });
  const mp4Plan = { courseId: 1, courseName: 'c', kind: 'video', outputs: ['mp4'], items: [item(1), item(2), item(3)] };

  assert.deepEqual(buildJobRows(mp4Plan, null).map((row) => row.phase), ['waiting', 'waiting', 'waiting']);

  // Bytes/percent while downloading; a native MP4 is not done until the playable check finished.
  let rows = buildJobRows(mp4Plan, job('running', [
    { stage: 'download_progress', module_item_id: 1, bytes_written: 50, total_bytes: 100 },
    { stage: 'download_done', module_item_id: 2 },
    { stage: 'video_playable_check_started', module_item_id: 2 },
    { stage: 'download_done', module_item_id: 3 },
    { stage: 'video_playable_check_done', module_item_id: 3 },
  ]));
  assert.deepEqual(rows.map((row) => row.phase), ['downloading', 'verifying', 'done']);
  assert.equal(rows[0].percent, 50);
  assert.equal(rows[0].label, '받는 중');
  assert.equal(rows[1].label, '영상 확인 중');

  // A non-native MP4 job is done at download_done; a material is done at download_done even natively.
  assert.equal(buildJobRows({ ...mp4Plan, items: [item(1)] }, job('running', [{ stage: 'download_done' }], { job_id: 'abc' }))[0].phase, 'done');
  assert.equal(buildJobRows({ ...mp4Plan, kind: 'material', items: [item(9)] }, job('running', [{ stage: 'download_done' }]))[0].phase, 'done');

  // Artifact pipeline: audio, then transcript.
  const txtPlan = { ...mp4Plan, outputs: ['mp3', 'txt'], items: [item(1), item(2)] };
  rows = buildJobRows(txtPlan, job('running', [
    { stage: 'audio_progress', module_item_id: 1, progress_percent: 41.6 },
    { stage: 'audio_done', module_item_id: 2 },
    { stage: 'transcript_progress', module_item_id: 2, progress_percent: 30 },
  ], { job_id: 'artifact-1' }));
  assert.deepEqual(rows.map((row) => [row.phase, row.percent]), [['extracting', 42], ['transcribing', 30]]);
  assert.equal(rows[0].label, '음성 추출 중');
  assert.equal(rows[1].label, '자막 만드는 중');
  assert.equal(buildJobRows(txtPlan, job('running', [{ stage: 'transcript_done', module_item_id: 1 }], { job_id: 'artifact-1' }))[0].phase, 'done');

  // Failures show a friendly reason, never the code; unstarted items of a failed job are retryable.
  const failed = buildJobRows(mp4Plan, job('failed', [
    { stage: 'download_done', module_item_id: 1 }, { stage: 'video_playable_check_done', module_item_id: 1 },
    { stage: 'download_progress', module_item_id: 2, bytes_written: 1, total_bytes: 10 },
  ], { error_code: 'network_error' }));
  assert.deepEqual(failed.map((row) => row.phase), ['done', 'failed', 'skipped']);
  assert.ok(failed[1].reason.includes('인터넷'));
  assert.ok(!failed[1].reason.includes('network_error'));
  assert.deepEqual(failed.filter(isRetryable).map((row) => row.id), ['2', '3']);
  const summary = summarizeRows(failed);
  assert.equal(summary.done, 1);
  assert.equal(summary.unfinished, 2);

  // Per-item result failures, cancellation, success.
  const batchFailed = buildJobRows(mp4Plan, job('failed', [], { result: { items: [{ module_item_id: 1, ok: true }, { module_item_id: 2, ok: false, error_code: 'HTTP 410' }, { module_item_id: 3, ok: true }] } }));
  assert.deepEqual(batchFailed.map((row) => row.phase), ['done', 'failed', 'done']);
  assert.ok(batchFailed[1].reason.includes('만료'));
  assert.deepEqual(buildJobRows(mp4Plan, job('cancelled', [{ stage: 'download_progress', module_item_id: 1, bytes_written: 1, total_bytes: 10 }])).map((row) => row.phase), ['cancelled', 'cancelled', 'cancelled']);
  assert.deepEqual(buildJobRows(mp4Plan, job('succeeded', [])).map((row) => row.phase), ['done', 'done', 'done']);
}

// Remote subtitle server: API client, settings UI copy, toggle enabling and progress text.
{
  const apiSource = readFileSync(new URL('../src/lib/api.ts', import.meta.url), 'utf8');
  for (const endpoint of ['/api/stt/remote/status', '/api/stt/remote/config', '/api/stt/remote/check']) {
    assert.ok(apiSource.includes(endpoint), `API client missing ${endpoint}`);
  }
  assert.ok(/'\/api\/stt\/remote\/config', \{ method: 'PUT'/.test(apiSource), 'the remote STT config is saved with PUT');

  const { subtitleSupport, draftFromStatus, patchFromDraft, describeRemoteCheck, remoteSummary, MAC_STUDIO_PRESET, REMOTE_STT_NOTE } = await import('../src/lib/remoteStt.ts');

  // The 자막 toggle: blocked on an unsupported Mac, but unblocked as soon as the remote server is enabled (even if unreachable).
  const intel = { available: false, platform_supported: false, install_hint: '이 Mac(Intel)에서는 자막 생성을 지원하지 않아요.' };
  const blocked = subtitleSupport(intel, false);
  assert.ok(blocked.unsupportedReason.includes('Intel') && blocked.unsupportedReason.includes('원격 자막 서버'));
  assert.equal(blocked.localMissing, false);
  assert.deepEqual(subtitleSupport(intel, true), { unsupportedReason: null, localMissing: false, viaRemote: true });
  // A supported Mac without faster-whisper still needs the install, unless the remote server replaces it.
  const missing = { available: false, platform_supported: true, install_hint: null };
  assert.equal(subtitleSupport(missing, false).localMissing, true);
  assert.equal(subtitleSupport(missing, true).localMissing, false);
  assert.equal(subtitleSupport({ available: true }, false).localMissing, false);
  assert.equal(subtitleSupport(undefined, false).unsupportedReason, null);
  const hookSource = readFileSync(new URL('../src/hooks/useMediaDependencies.ts', import.meta.url), 'utf8');
  assert.ok(hookSource.includes('subtitleSupport(whisperStatus, remote.enabled)') && hookSource.includes('localMissing ?'), 'media dependencies must route through subtitleSupport');
  const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.ok(appSource.includes('media.subtitlesUnsupportedReason ? { txt: media.subtitlesUnsupportedReason } : {}'), 'the 자막 toggle is disabled only by subtitlesUnsupportedReason');

  // Preset and form <-> config mapping.
  assert.deepEqual(MAC_STUDIO_PRESET, { enabled: true, kind: 'whisper_cpp_server', base_url: 'http://127.0.0.1:18795', tunnel: { host: 'macstudio', remote_port: 8795, local_port: 18795 } });
  const stored = { ok: true, enabled: true, kind: 'whisper_cpp_server', base_url: 'http://127.0.0.1:18795', model: '', api_key_set: false, tunnel: { host: 'macstudio', remote_port: 8795, local_port: 18795 }, reachable: null, last_error: null, error_code: null, checked_at: null };
  const draft = draftFromStatus(stored);
  assert.deepEqual(draft, { kind: 'whisper_cpp_server', baseUrl: 'http://127.0.0.1:18795', model: '', apiKey: '', tunnelHost: 'macstudio', tunnelRemotePort: '8795' });
  assert.deepEqual(draftFromStatus(null).tunnelHost, '');
  assert.deepEqual(patchFromDraft(draft, stored), { patch: { kind: 'whisper_cpp_server', model: '', tunnel: { host: 'macstudio', remote_port: 8795, local_port: 18795 } } });
  assert.deepEqual(patchFromDraft({ ...draft, tunnelHost: '  ', baseUrl: ' http://10.0.0.5:8795 ' }, stored), { patch: { kind: 'whisper_cpp_server', model: '', tunnel: null, base_url: 'http://10.0.0.5:8795' } });
  assert.ok('error' in patchFromDraft({ ...draft, tunnelRemotePort: 'abc' }, stored));
  assert.ok('error' in patchFromDraft({ ...draft, tunnelRemotePort: '70000' }, stored));
  // API key: only what was typed is sent, never for whisper.cpp, and an untouched field keeps the saved key.
  const openai = { ...draft, kind: 'openai_compatible', model: ' whisper-1 ', tunnelHost: '', baseUrl: 'https://stt.example.com' };
  assert.equal(patchFromDraft(openai, stored).patch.api_key, undefined);
  assert.deepEqual(patchFromDraft({ ...openai, apiKey: ' sk-typed ' }, stored).patch, { kind: 'openai_compatible', model: 'whisper-1', tunnel: null, base_url: 'https://stt.example.com', api_key: 'sk-typed' });
  assert.equal(patchFromDraft({ ...draft, apiKey: 'sk-ignored' }, stored).patch.api_key, undefined);
  assert.equal(patchFromDraft({ ...draft, model: 'ignored' }, stored).patch.model, '');

  // Connection check text and status chip.
  assert.deepEqual(describeRemoteCheck({ ...stored, reachable: true }), { ok: true, text: '서버에 연결됐어요.' });
  assert.deepEqual(describeRemoteCheck({ ...stored, reachable: false, last_error: 'SSH 터널을 열지 못했어요.' }), { ok: false, text: 'SSH 터널을 열지 못했어요.' });
  assert.equal(describeRemoteCheck({ ...stored, reachable: false }).ok, false);
  assert.deepEqual(remoteSummary({ ...stored, enabled: false }), { label: '꺼짐', tone: 'neutral' });
  assert.deepEqual(remoteSummary({ ...stored, reachable: true }), { label: '연결됨', tone: 'success' });
  assert.deepEqual(remoteSummary(stored), { label: '연결 확인 필요', tone: 'warning' });
  assert.equal(REMOTE_STT_NOTE, '음성은 이 서버로만 보내져요. 외부 공개 주소 대신 SSH 터널 사용을 권장해요.');

  // Settings dialog copy and controls.
  const dialog = readFileSync(new URL('../src/components/SettingsDialog.tsx', import.meta.url), 'utf8');
  for (const text of ['원격 자막 서버', '원격 서버로 자막 만들기', 'Mac Studio 사용', '연결 확인', '<span>종류</span>', '<span>주소</span>', '<span>모델</span>', '<span>API 키</span>', '<span>SSH 터널 호스트</span>', '<span>원격 포트</span>', 'REMOTE_STT_NOTE', 'type="password"']) {
    assert.ok(dialog.includes(text), `settings dialog missing: ${text}`);
  }
  assert.ok(/openAiKind && \(\s*<>\s*<label className="field">\s*<span>모델<\/span>/.test(dialog), 'model and API key are only for the OpenAI kind');
  assert.ok(!/value=\{[^}]*api_key/.test(dialog), 'the saved API key must never be put into an input');
  assert.ok(dialog.includes('autoComplete="off"'));

  // Progress drawer: remote label and the loop-repair text.
  const { buildJobRows } = await import('../src/lib/jobItems.ts');
  const item = { id: '1', rawId: 1, title: 'T1', sizeBytes: 1 };
  const plan = { courseId: 1, courseName: 'c', kind: 'video', outputs: ['txt'], items: [item] };
  const run = (events) => buildJobRows(plan, { job_id: 'artifact-1', status: 'running', cancel_requested: false, progress: events, result: null, error_code: null })[0];
  let row = run([{ stage: 'transcribing', module_item_id: 1, remote: true }, { stage: 'transcript_connecting', module_item_id: 1, remote: true, progress_percent: 2 }]);
  assert.equal(row.label, '자막 만드는 중 (원격)');
  assert.equal(row.note, '서버에 연결하는 중');
  row = run([{ stage: 'transcribing', module_item_id: 1, remote: true }, { stage: 'transcript_repair', module_item_id: 1, remote: true, repair_index: 2, repair_total: 3, progress_percent: 80 }]);
  assert.deepEqual([row.phase, row.label, row.note, row.percent], ['transcribing', '자막 만드는 중 (원격)', '반복 구간 다시 인식 중 2/3', 80]);
  row = run([{ stage: 'transcript_repair', module_item_id: 1, remote: true, repair_index: 1, repair_total: 1 }, { stage: 'transcript_progress', module_item_id: 1, remote: true, progress_percent: 99 }]);
  assert.equal(row.note, undefined);
  // Local transcription keeps its plain label.
  row = run([{ stage: 'transcript_progress', module_item_id: 1, progress_percent: 30 }]);
  assert.deepEqual([row.label, row.note], ['자막 만드는 중', undefined]);
  assert.ok(readFileSync(new URL('../src/components/DownloadDrawer.tsx', import.meta.url), 'utf8').includes('row.note'));

  // Remote error codes read as plain Korean.
  const { describeErrorCode, GENERIC_ERROR: genericError } = await import('../src/lib/errors.ts');
  for (const code of ['stt_remote_unreachable', 'stt_remote_timeout', 'stt_remote_auth_failed', 'ssh_tunnel_failed', 'invalid_ssh_host', 'missing_stt_base_url']) {
    assert.ok(!describeErrorCode(code).includes(code) && describeErrorCode(code) !== genericError, `${code} needs its own message`);
  }
}

// Per-lecture pipeline: waiting states, per-lecture outcomes, weighted progress (PL-UI-STATES, AC7/AC8).
{
  const { buildJobRows, summarizeRows, readPipelineSummary } = await import('../src/lib/jobItems.ts');
  const { describeErrorCode } = await import('../src/lib/errors.ts');
  const item = (id) => ({ id: String(id), rawId: id, title: `강의 ${id}`, sizeBytes: 100 });
  const job = (status, events, extra = {}) => ({ job_id: 'artifact-1', status, cancel_requested: false, progress: events, result: null, error_code: null, ...extra });
  const txtPlan = { courseId: 1, courseName: 'c', kind: 'video', outputs: ['mp3', 'txt'], items: [item(1), item(2), item(3)] };
  const mp3Plan = { ...txtPlan, outputs: ['mp3'] };
  const rowsOf = (plan, events, extra) => buildJobRows(plan, job('running', events, extra));
  const one = (plan, events, extra) => rowsOf({ ...plan, items: [item(1)] }, events, extra)[0];
  const ev = (stage, extra = {}) => ({ stage, module_item_id: 1, ...extra });

  // Old backend (today's stages only): a finished download is "waiting for audio", never "받는 중 100%".
  let rows = rowsOf(txtPlan, [
    ev('download_done'), { stage: 'download_done', module_item_id: 2 }, { stage: 'artifact_audio_started', total: 3 },
    ev('extracting_audio'), ev('audio_progress', { progress_percent: 40 }),
  ]);
  assert.deepEqual(rows.map((row) => [row.phase, row.label, row.percent]), [['extracting', '음성 추출 중', 40], ['waiting_audio', '음성 추출 대기 중', null], ['waiting', '대기', null]]);
  // Audio finished, subtitles not started: "자막 대기 중", not "자막 만드는 중 0%".
  let row = one(txtPlan, [ev('download_done'), ev('extracting_audio'), ev('audio_done')]);
  assert.deepEqual([row.phase, row.label, row.percent], ['waiting_transcript', '자막 대기 중', null]);
  row = one(txtPlan, [ev('download_done'), ev('audio_done'), ev('transcribing'), ev('transcript_started')]);
  assert.deepEqual([row.phase, row.label, row.percent, row.note], ['transcribing', '자막 만드는 중', 10, undefined]);
  // MP3 only: audio done is done; MP4 only: unchanged ("받는 중" until the item is finished).
  assert.equal(one(mp3Plan, [ev('download_done'), ev('audio_done')]).phase, 'done');
  assert.deepEqual([one({ ...txtPlan, outputs: ['mp4'] }, [ev('download_progress', { bytes_written: 5, total_bytes: 10 })], { job_id: 'abc' }).phase], ['downloading']);
  // Playable check of a native video before the audio step.
  assert.equal(one(txtPlan, [ev('download_done'), ev('video_playable_check_started')], { job_id: 'native-1' }).phase, 'verifying');
  assert.equal(one(txtPlan, [ev('download_done'), ev('video_playable_check_done')], { job_id: 'native-1' }).phase, 'waiting_audio');

  // New backend: copy of every waiting state in design/09 § 화면 문구.
  assert.deepEqual(rowsOf(txtPlan, [{ stage: 'artifact_item_queued', module_item_id: 2, at: 1 }])[1].label, '대기');
  row = one(txtPlan, [ev('download_done', { at: 1 }), ev('audio_waiting', { at: 2 })]);
  assert.deepEqual([row.phase, row.label], ['waiting_audio', '음성 추출 대기 중']);
  row = one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'local_engine' })]);
  assert.deepEqual([row.phase, row.label, row.note], ['waiting_transcript', '자막 대기 중', undefined]);
  row = one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'server_busy', ahead_requests: 2 })]);
  assert.deepEqual([row.label, row.note], ['자막 서버 응답 기다리는 중', '먼저 보낸 요청 2개']);
  assert.equal(one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'server_busy' })]).note, undefined);
  row = one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'server_deferred', retry_after_seconds: 12 })]);
  assert.equal(row.label, '자막 대기 중 (서버가 잠시 미룸)');
  row = one(txtPlan, [ev('audio_done'), ev('transcript_queued', { queue_position: 3, job_id: 'j1' })]);
  assert.equal(row.label, '자막 대기 중 (3번째)');
  row = one(txtPlan, [ev('audio_done'), ev('transcript_queued', { queue_position: 3 }), ev('transcript_queued', { queue_position: 1 })]);
  assert.equal(row.label, '자막 대기 중 (1번째)');
  // A media wait followed by a place in the server queue shows the place.
  row = one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'media', at: 1 }), ev('transcript_queued', { queue_position: 2, at: 2 })]);
  assert.equal(row.label, '자막 대기 중 (2번째)');
  row = one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'media', at: 1 }), ev('transcript_queued', { queue_position: 2, at: 2 }), ev('transcript_waiting', { reason: 'media', at: 3 })]);
  assert.equal(row.label, '자막 준비 대기 중', 'a later media wait replaces the queue place');
  // A reason this app does not know is a plain wait.
  assert.equal(one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'something_new' })]).label, '자막 대기 중');
  // A row never goes back: a late audio_waiting (the backend moves coalesced waits to the end) is ignored once audio or subtitles started.
  row = one(txtPlan, [ev('download_done'), ev('audio_done'), ev('transcribing'), ev('transcript_progress', { progress_percent: 50 }), ev('audio_waiting', { at: 9 })]);
  assert.deepEqual([row.phase, row.label, row.percent], ['transcribing', '자막 만드는 중', 50]);
  row = one(txtPlan, [ev('download_done'), ev('audio_done'), ev('audio_waiting')]);
  assert.equal(row.phase, 'waiting_transcript');
  row = one(txtPlan, [ev('download_done'), ev('extracting_audio'), ev('audio_progress', { progress_percent: 30 }), ev('audio_waiting')]);
  assert.deepEqual([row.phase, row.percent], ['extracting', 30]);
  // Media waits inside the subtitle step, and audio_waiting for the MP4 step.
  row = one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'media' })]);
  assert.deepEqual([row.phase, row.label, row.note], ['waiting_transcript', '자막 준비 대기 중', undefined]);
  row = one(txtPlan, [ev('audio_done'), ev('transcribing'), ev('transcript_waiting', { reason: 'media' })]);
  assert.deepEqual([row.phase, row.label], ['waiting_transcript', '자막 준비 대기 중']);
  row = one(txtPlan, [ev('download_done'), ev('audio_waiting', { step: 'mp4' })]);
  assert.deepEqual([row.phase, row.label], ['waiting_audio', '영상 확인 대기 중']);
  row = one(txtPlan, [ev('download_done'), ev('audio_waiting', { step: 'audio' })]);
  assert.equal(row.label, '음성 추출 대기 중');
  row = one(txtPlan, [ev('download_done'), ev('audio_waiting', { step: 'mp4' }), ev('download_done')]);
  assert.equal(row.label, '음성 추출 대기 중', 'a later derived wait is not an mp4 wait');
  // The turn comes: waiting text goes away; estimated percent reads "약 n%" only when flagged.
  row = one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'server_busy', ahead_requests: 1 }), ev('transcribing', { remote: true }), ev('transcript_progress', { remote: true, progress_percent: 37.4, progress_estimated: true })]);
  assert.deepEqual([row.phase, row.label, row.note, row.percent, row.estimated], ['transcribing', '자막 만드는 중 (원격)', undefined, 37, true]);
  row = one(txtPlan, [ev('transcript_progress', { remote: true, progress_percent: 60 })]);
  assert.equal(row.estimated, undefined);

  // High-water mark: events dropped by the backend's cap cannot send a running row back to "대기" or 0%.
  {
    const { holdFurthestRows } = await import('../src/lib/jobItems.ts');
    const marks = new Map();
    const poll = (events, extra) => holdFurthestRows('artifact-1', buildJobRows(txtPlan, job('running', events, extra)), marks);
    let shown = poll([ev('download_done'), ev('audio_done'), ev('transcribing'), ev('transcript_progress', { progress_percent: 50 })]);
    assert.deepEqual([shown[0].phase, shown[0].percent, shown[0].overall], ['transcribing', 50, 80]);
    // Next poll: the oldest events were evicted, only a stale download event is left for lecture 1; lecture 2 has none at all.
    shown = poll([ev('download_progress', { bytes_written: 10, total_bytes: 100 })]);
    assert.deepEqual([shown[0].phase, shown[0].label, shown[0].percent, shown[0].overall], ['transcribing', '자막 만드는 중', 50, 80]);
    shown = poll([]);
    assert.deepEqual([shown[0].phase, shown[0].overall], ['transcribing', 80]);
    assert.deepEqual([shown[1].phase, shown[1].overall], ['waiting', 0], 'a row that never advanced stays at the start');
    // Progress inside a phase still moves forward, and the overall value never drops.
    shown = poll([ev('transcript_progress', { progress_percent: 70 })]);
    assert.deepEqual([shown[0].percent, shown[0].overall], [70, 88]);
    // A media wait inside the subtitle step is shown, but the bar does not drop.
    shown = poll([ev('transcript_waiting', { reason: 'media' })]);
    assert.deepEqual([shown[0].phase, shown[0].label, shown[0].overall], ['waiting_transcript', '자막 준비 대기 중', 88]);
    // Final states win over the mark, from events or from the job's final list.
    shown = poll([ev('artifact_item_failed', { error_code: 'stt_remote_job_lost' })]);
    assert.equal(shown[0].phase, 'failed');
    marks.clear();
    poll([ev('transcribing')]);
    shown = holdFurthestRows('artifact-1', buildJobRows(txtPlan, job('failed', [], { result: { ok: false, summary: { total: 3, succeeded: 1, failed: 0, cancelled: 2 }, items_status: [{ module_item_id: 1, state: 'done' }, { module_item_id: 2, state: 'cancelled' }, { module_item_id: 3, state: 'cancelled' }] } })), marks);
    assert.deepEqual(shown.map((row) => row.phase), ['done', 'cancelled', 'cancelled']);
    // A row that was finished stays finished when its final event is evicted.
    marks.clear();
    poll([ev('artifact_item_done')]);
    assert.equal(poll([])[0].phase, 'done');
    // Another job (a retry) starts clean, and no job id means no hold.
    assert.equal(holdFurthestRows('artifact-2', buildJobRows(txtPlan, job('running', [], { job_id: 'artifact-2' })), marks)[0].phase, 'waiting');
    assert.equal(holdFurthestRows(null, buildJobRows(txtPlan, null), marks)[0].phase, 'waiting');
    const hookSource = readFileSync(new URL('../src/hooks/useDownloads.ts', import.meta.url), 'utf8');
    assert.ok(hookSource.includes('holdFurthestRows(job?.job_id'), 'the hook keeps the furthest row per job');
  }

  // SYNTHETIC ordering case (not a current backend log): the out-of-order shape pl-py-pipeline f367be8 stored, with
  // the waiting event at the end and the 99% tick replacing an older one in place. Since 35a3352 the list is chronological;
  // sorting by `at` stays as a guard for older builds. Real logs are the fixtures in the next block.
  {
    const recorded = [
    {"module_item_id": "1", "stage": "download_done", "file_size": 10, "output_path": "/a/L1.mp4", "at": 1},
    {"module_item_id": "1", "stage": "extracting_audio", "output_path": "/a/L1.mp4", "at": 2},
    {"module_item_id": "1", "stage": "audio_done", "output_path": "/a/L1.mp3", "file_size": 5, "progress_percent": 100, "at": 3},
    {"module_item_id": "1", "stage": "transcribing", "output_path": "/a/L1.mp3", "remote": true, "at": 4},
    {"module_item_id": "1", "remote": true, "stage": "transcript_connecting", "progress_percent": 2, "at": 5},
    {"module_item_id": "1", "remote": true, "stage": "transcript_started", "progress_percent": 5, "at": 6},
    {"module_item_id": "1", "remote": true, "stage": "transcript_progress", "progress_percent": 99, "at": 11},
    {"module_item_id": "1", "remote": true, "stage": "transcript_repair", "repair_index": 2, "repair_total": 3, "progress_percent": 88, "at": 10},
    {"module_item_id": "1", "remote": true, "stage": "transcript_waiting", "reason": "server_busy", "ahead_requests": 2, "at": 9}
    ];
    row = one(txtPlan, recorded);
    assert.deepEqual([row.phase, row.label, row.percent, row.note], ['transcribing', '자막 만드는 중 (원격)', 99, undefined], 'a lecture at 99% is not shown as waiting');
    assert.ok(row.overall <= 99 && !row.final);
    // Events without `at` keep their slot, ties keep list order.
    row = one(txtPlan, [ev('audio_done'), ev('transcript_waiting', { reason: 'server_busy', at: 5 }), ev('transcribing', { at: 6 })]);
    assert.equal(row.phase, 'transcribing');
    row = one(txtPlan, [ev('audio_done', { at: 2 }), ev('transcript_waiting', { reason: 'media' }), ev('transcribing', { at: 1 })]);
    assert.equal(row.phase, 'waiting_transcript', 'the untimed event keeps its place between the timed ones');
    row = one(txtPlan, [ev('transcribing', { at: 3 }), ev('transcript_waiting', { reason: 'media', at: 3 })]);
    assert.equal(row.phase, 'waiting_transcript', 'equal `at` keeps list order');
  }

  // Real logs from pl-py-pipeline 3b1a6e7 (scripts/fixtures/*.json, captured through its test rig; the files name the command).
  {
    const loadFixture = (name) => {
      const fixture = JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), 'utf8'));
      assert.ok(fixture.source.includes('3b1a6e7') && fixture.command, `${name} names its source revision and command`);
      return fixture.progress;
    };
    const tier = { waiting: 0, preparing: 1, downloading: 2, verifying: 3, waiting_audio: 3, extracting: 4, waiting_transcript: 5, transcribing: 5, done: 6, failed: 6, cancelled: 6, skipped: 6 };

    // Normal remote run: loop repair and server-busy waits. Replaying every prefix of the log (a poll after each event) a lecture never goes back.
    const remote = loadFixture('backend-3b1a6e7-remote-run.json');
    const remotePlan = { courseId: 1, courseName: 'c', kind: 'video', outputs: ['txt'], items: [1, 2, 3, 4].map(item) };
    const furthest = [0, 0, 0, 0];
    for (let length = 1; length <= remote.length; length += 1) {
      buildJobRows(remotePlan, job('running', remote.slice(0, length), { job_id: 'artifact-r' })).forEach((entry, index) => {
        assert.ok(tier[entry.phase] >= furthest[index], `lecture ${index + 1} went back to ${entry.phase} after event ${length} (${remote[length - 1].stage})`);
        furthest[index] = tier[entry.phase];
      });
    }
    const firstDone = remote.findIndex((event) => event.stage === 'transcript_done');
    rows = buildJobRows(remotePlan, job('running', remote.slice(0, firstDone), { job_id: 'artifact-r' }));
    assert.deepEqual([rows[0].phase, rows[0].label, rows[0].percent], ['transcribing', '자막 만드는 중 (원격)', 99], 'a lecture at 99% is not shown as waiting');
    assert.deepEqual([rows[3].phase, rows[3].label], ['waiting_transcript', '자막 서버 응답 기다리는 중'], 'a lecture whose last event is the busy wait waits');
    assert.ok(rows.every((entry) => !entry.final && entry.overall <= 99));
    assert.deepEqual(buildJobRows(remotePlan, job('succeeded', remote, { job_id: 'artifact-r' })).map((entry) => [entry.phase, entry.overall, entry.final]), [1, 2, 3, 4].map(() => ['done', 100, true]));

    // Over the 400-event cap: 28 of 30 lectures wait for audio; the two that hold a slot kept their extracting_audio entry.
    const capped = loadFixture('backend-3b1a6e7-over-cap.json');
    assert.equal(capped.length, 400);
    const mp3Many = { courseId: 1, courseName: 'c', kind: 'video', outputs: ['mp3'], items: Array.from({ length: 30 }, (_, index) => item(index + 1)) };
    rows = buildJobRows(mp3Many, job('running', capped, { job_id: 'artifact-c' }));
    assert.equal(rows.filter((entry) => entry.phase === 'waiting_audio' && entry.label === '음성 추출 대기 중').length, 28);
    assert.deepEqual(rows.filter((entry) => entry.phase === 'extracting').map((entry) => [entry.label, entry.percent]), [['음성 추출 중', 0], ['음성 추출 중', 0]]);
    assert.ok(rows.every((entry) => entry.phase !== 'failed' && entry.overall <= 99 && !entry.final));
  }

  // A bare `failed` (the batch download's own result for an item) is final even after the cap drops it; `download_failed` is not.
  {
    const { holdFurthestRows } = await import('../src/lib/jobItems.ts');
    const marks = new Map();
    const poll = (events) => holdFurthestRows('artifact-1', buildJobRows(txtPlan, job('running', events)), marks);
    let shown = poll([ev('failed', { error_code: 'network_error', at: 1 })]);
    assert.deepEqual([shown[0].phase, shown[0].final], ['failed', true]);
    shown = poll([{ stage: 'download_progress', module_item_id: 2, at: 2 }]);
    assert.deepEqual([shown[0].phase, shown[0].label, shown[0].final], ['failed', '실패', true], 'the evicted failed event does not send the row back to 대기');
    shown = poll([ev('download_progress', { bytes_written: 5, total_bytes: 10, at: 3 })]);
    assert.equal(shown[0].phase, 'failed', 'later events cannot revive it');
    assert.equal(one(txtPlan, [ev('failed', { at: 1 }), ev('download_progress', { bytes_written: 5, total_bytes: 10, at: 2 })]).phase, 'failed');
    marks.clear();
    shown = poll([ev('download_failed', { error_code: 'network_error', at: 1 })]);
    assert.deepEqual([shown[0].phase, shown[0].final], ['failed', false]);
  }

  // The tick that carried the higher percent can be evicted: the row keeps it (and its "약" flag) inside the same step, but a repair pass reports its own progress.
  {
    const { holdFurthestRows } = await import('../src/lib/jobItems.ts');
    const marks = new Map();
    const poll = (events) => holdFurthestRows('artifact-1', buildJobRows(txtPlan, job('running', events)), marks);
    let shown = poll([ev('audio_done', { at: 1 }), ev('transcribing', { remote: true, at: 2 }), ev('transcript_progress', { remote: true, progress_percent: 62, progress_estimated: true, at: 3 })]);
    assert.deepEqual([shown[0].percent, shown[0].estimated], [62, true]);
    shown = poll([ev('audio_done', { at: 1 }), ev('transcribing', { remote: true, at: 2 })]);
    assert.deepEqual([shown[0].phase, shown[0].percent, shown[0].estimated, shown[0].label], ['transcribing', 62, true, '자막 만드는 중 (원격)'], 'the poll that lost the 62% tick still shows 62');
    shown = poll([ev('transcript_progress', { remote: true, progress_percent: 70, at: 4 })]);
    assert.deepEqual([shown[0].percent, shown[0].estimated], [70, undefined], 'a higher tick wins, with its own flag');
    shown = poll([ev('transcript_repair', { remote: true, progress_percent: 40, repair_index: 1, repair_total: 3, at: 5 })]);
    assert.deepEqual([shown[0].percent, shown[0].note], [40, '반복 구간 다시 인식 중 1/3'], 'a repair pass is not held');
    // The same inside audio extraction.
    marks.clear();
    poll([ev('download_done', { at: 1 }), ev('extracting_audio', { at: 2 }), ev('audio_progress', { progress_percent: 45, at: 3 })]);
    assert.equal(poll([ev('download_done', { at: 1 }), ev('extracting_audio', { at: 2 })])[0].percent, 45);
  }

  // A wait guessed from audio_done (no transcript_waiting event) must not pull a transcribing row back after eviction.
  {
    const { holdFurthestRows } = await import('../src/lib/jobItems.ts');
    const marks = new Map();
    const poll = (events) => holdFurthestRows('artifact-1', buildJobRows(txtPlan, job('running', events)), marks);
    let shown = poll([ev('audio_done', { at: 1 }), ev('transcribing', { at: 2 }), ev('transcript_progress', { progress_percent: 62, at: 3 })]);
    assert.deepEqual([shown[0].phase, shown[0].percent, shown[0].derivedWait], ['transcribing', 62, false]);
    shown = poll([ev('audio_done', { at: 1 })]);
    assert.deepEqual([shown[0].phase, shown[0].percent, shown[0].label], ['transcribing', 62, '자막 만드는 중'], 'only audio_done is left');
    assert.equal(buildJobRows(txtPlan, job('running', [ev('audio_done', { at: 1 })]))[0].derivedWait, true);
    assert.equal(buildJobRows(txtPlan, job('running', [ev('audio_done', { at: 1 }), ev('transcript_waiting', { reason: 'media', at: 2 })]))[0].derivedWait, false);
    // An explicit wait that follows still shows (a media wait during repair cuts), and a derived wait alone is shown when nothing was held.
    shown = poll([ev('transcript_waiting', { reason: 'media', at: 5 })]);
    assert.deepEqual([shown[0].phase, shown[0].label], ['waiting_transcript', '자막 준비 대기 중']);
    marks.clear();
    assert.equal(poll([ev('audio_done', { at: 1 })])[0].phase, 'waiting_transcript');
    // The same for the wait after download_done: the video check that began is kept.
    marks.clear();
    poll([ev('download_done', { at: 1 }), ev('video_playable_check_started', { at: 2 })]);
    assert.equal(poll([ev('download_done', { at: 1 })])[0].phase, 'verifying');
  }

  // A failure of a running job can still be retried by the backend (signed-URL expiry): it is not pinned.
  {
    const { holdFurthestRows } = await import('../src/lib/jobItems.ts');
    const marks = new Map();
    const poll = (events, status = 'running', extra = {}) => holdFurthestRows('artifact-1', buildJobRows(txtPlan, job(status, events, extra)), marks);
    let shown = poll([ev('download_progress', { bytes_written: 50, total_bytes: 100, at: 1 })]);
    assert.deepEqual([shown[0].phase, shown[0].final], ['downloading', false]);
    shown = poll([ev('download_progress', { bytes_written: 50, total_bytes: 100, at: 1 }), ev('download_failed', { error_code: 'network_error', at: 2 })]);
    assert.deepEqual([shown[0].phase, shown[0].final, shown[0].overall], ['failed', false, 20]);
    shown = poll([ev('download_failed', { error_code: 'network_error', at: 2 }), ev('download_reresolving', { at: 3 }), ev('download_progress', { bytes_written: 80, total_bytes: 100, at: 4 })]);
    assert.deepEqual([shown[0].phase, shown[0].final, shown[0].percent], ['downloading', false, 80], 'the retry is shown, not the old failure');
    assert.equal(shown[0].overall, 32);
    // The same failure at the end of the job (or from the item's own final event / list) is final.
    marks.clear();
    shown = poll([ev('download_failed', { error_code: 'network_error', at: 2 })], 'failed', { error_code: 'network_error' });
    assert.deepEqual([shown[0].phase, shown[0].final], ['failed', true]);
    marks.clear();
    shown = poll([ev('artifact_item_failed', { error_code: 'stt_failed', at: 2 })]);
    assert.deepEqual([shown[0].phase, shown[0].final], ['failed', true]);
    shown = poll([ev('download_progress', { bytes_written: 10, total_bytes: 100, at: 3 })]);
    assert.equal(shown[0].phase, 'failed', 'a final failure stays');
  }

  // MP4 output: the video check after download_done is not hidden behind "음성 추출 대기 중", and a row is not 100% before it is final.
  {
    const { holdFurthestRows } = await import('../src/lib/jobItems.ts');
    const mp4Plan = { ...txtPlan, outputs: ['mp4', 'txt'] };
    const marks = new Map();
    const poll = (events, plan = mp4Plan, extra = {}) => holdFurthestRows('artifact-1', buildJobRows(plan, job('running', events, extra)), marks);
    let shown = poll([ev('download_done', { at: 1 })]);
    assert.equal(shown[0].phase, 'waiting_audio');
    shown = poll([ev('video_playable_check_started', { at: 2 })]);
    assert.deepEqual([shown[0].phase, shown[0].label], ['verifying', '영상 확인 중']);
    shown = poll([ev('audio_waiting', { step: 'mp4', at: 3 })]);
    assert.deepEqual([shown[0].phase, shown[0].label], ['waiting_audio', '영상 확인 대기 중']);
    shown = poll([ev('video_reconstruction_started', { at: 4 })]);
    assert.equal(shown[0].phase, 'verifying');
    // MP4 only: a finished download whose check is still ahead is not 100%.
    const mp4Only = { ...txtPlan, outputs: ['mp4'] };
    row = one(mp4Only, [ev('download_done', { at: 1 })], { job_id: 'native-1' });
    assert.deepEqual([row.phase, row.overall, row.final], ['downloading', 99, false]);
    row = one(mp4Only, [ev('download_done', { at: 1 }), ev('artifact_item_done', { at: 2 })], { job_id: 'native-1' });
    assert.deepEqual([row.phase, row.overall, row.final], ['done', 100, true]);
    row = one(mp4Only, [ev('download_done', { at: 1 }), ev('video_playable_check_done', { at: 2 })], { job_id: 'native-1' });
    assert.deepEqual([row.phase, row.overall], ['done', 100]);
  }

  // One lecture finishes, one fails (its own copy), the third keeps going.
  rows = rowsOf(txtPlan, [
    ev('transcript_done'), ev('artifact_item_done'),
    { stage: 'audio_progress', module_item_id: 2, progress_percent: 10 },
    { stage: 'artifact_item_failed', module_item_id: 2, error_code: 'stt_remote_job_lost', failed_stage: 'transcript' },
    { stage: 'transcript_progress', module_item_id: 2, progress_percent: 90 },
    { stage: 'download_progress', module_item_id: 3, bytes_written: 10, total_bytes: 100 },
  ]);
  assert.deepEqual(rows.map((row) => row.phase), ['done', 'failed', 'downloading']);
  assert.equal(rows[1].reason, '자막 서버에서 작업을 찾지 못했어요. 다시 시도해 주세요.');
  assert.equal(rows[1].reasonCode, 'stt_remote_job_lost');
  assert.equal(rows[1].percent, null, 'events after artifact_item_failed cannot revive the row');
  assert.equal(summarizeRows(rows).running, true);
  rows = rowsOf(txtPlan, [ev('audio_waiting'), ev('artifact_item_cancelled'), { stage: 'audio_progress', module_item_id: 1, progress_percent: 5 }]);
  assert.equal(rows[0].phase, 'cancelled');
  // Cancelling a job also cancels rows that are only waiting.
  assert.deepEqual(buildJobRows(txtPlan, job('cancelled', [ev('download_done'), ev('audio_done')])).map((row) => row.phase), ['cancelled', 'cancelled', 'cancelled']);

  // Final lists of a finished job win, even when the early events were compacted away.
  const items_status = [
    { module_item_id: 1, state: 'done' },
    { module_item_id: 2, state: 'failed', error_code: 'stt_remote_upload_too_large', failed_stage: 'transcript' },
    { module_item_id: 3, state: 'cancelled' },
  ];
  const summary = { total: 3, succeeded: 1, failed: 1, cancelled: 1 };
  const finished = job('failed', [], { error_code: 'partial_failure', result: { ok: false, error_code: 'partial_failure', download: { items: [] }, artifacts: [], progress: [], summary, items_status } });
  rows = buildJobRows(txtPlan, finished);
  assert.deepEqual(rows.map((row) => row.phase), ['done', 'failed', 'cancelled']);
  assert.equal(rows[1].reason, '음성 파일이 서버 허용 크기보다 커요.');
  assert.deepEqual(readPipelineSummary(finished), summary);
  assert.equal(readPipelineSummary(job('running', [], { result: { summary } })), null, 'the summary of a running job is not final');
  assert.equal(readPipelineSummary(job('failed', [], { result: { summary: { total: 'x' } } })), null);
  assert.deepEqual(readPipelineSummary(job('failed', [], { result: { download: { summary } } })), summary);
  let totals = summarizeRows(rows, readPipelineSummary(finished));
  assert.deepEqual([totals.total, totals.done, totals.failed, totals.unfinished, totals.running], [3, 1, 1, 2, false]);
  // Older backend: no summary / items_status, same rows as before.
  assert.deepEqual(buildJobRows(txtPlan, job('succeeded', [])).map((row) => row.phase), ['done', 'done', 'done']);
  totals = summarizeRows(buildJobRows(txtPlan, job('succeeded', [])), null);
  assert.deepEqual([totals.done, totals.unfinished], [3, 0]);

  // Overall bar: download 40, audio 20, subtitles 40, only for the steps asked for.
  const overall = (plan, events) => one(plan, events).overall;
  assert.equal(overall(txtPlan, []), 0);
  assert.equal(overall(txtPlan, [ev('download_progress', { bytes_written: 50, total_bytes: 100 })]), 20);
  assert.equal(overall(txtPlan, [ev('download_done')]), 40);
  assert.equal(overall(txtPlan, [ev('download_done'), ev('audio_progress', { progress_percent: 50 })]), 50);
  assert.equal(overall(txtPlan, [ev('audio_done')]), 60);
  assert.equal(overall(txtPlan, [ev('audio_done'), ev('transcript_progress', { progress_percent: 50 })]), 80);
  assert.equal(overall(mp3Plan, [ev('download_done'), ev('audio_progress', { progress_percent: 50 })]), 83, 'normalised to download + audio');
  assert.equal(overall({ ...txtPlan, outputs: ['mp4'] }, [ev('download_progress', { bytes_written: 50, total_bytes: 100 })]), 50);
  assert.equal(overall({ ...txtPlan, kind: 'material', outputs: ['mp4'] }, [ev('download_done')]), 100);
  assert.equal(overall(txtPlan, [ev('artifact_item_done')]), 100);
  assert.equal(overall(txtPlan, [ev('artifact_item_failed', { error_code: 'stt_failed' })]), 100);
  // Three lectures at 100 / 40 / 0 average to 47, and a lecture waiting for audio is not 100 %.
  rows = rowsOf(txtPlan, [ev('artifact_item_done'), { stage: 'download_done', module_item_id: 2 }]);
  assert.equal(summarizeRows(rows).percent, 47);
  assert.ok(rows[1].overall < 100 && rows[1].phase === 'waiting_audio');

  // Events this UI does not know are ignored, with or without fields it does not know.
  const noise = [ev('gate_wait', { at: 5, gate: 'media', mystery: { deep: true } }), ev('future_stage_name', { at: 6 })];
  assert.deepEqual(rowsOf(txtPlan, noise).map((row) => row.phase), ['waiting', 'waiting', 'waiting']);

  // New error copy, exactly as in design/09.
  assert.equal(describeErrorCode('stt_remote_job_lost'), '자막 서버에서 작업을 찾지 못했어요. 다시 시도해 주세요.');
  assert.equal(describeErrorCode('stt_remote_upload_too_large'), '음성 파일이 서버 허용 크기보다 커요.');
  assert.equal(describeErrorCode('stt_remote_bad_request'), '자막 서버가 이 음성 파일을 받지 않았어요.');

  // Drawer: title, "약 n%", accessible status text (polite, never an alert), bar value text.
  const drawer = readFileSync(new URL('../src/components/DownloadDrawer.tsx', import.meta.url), 'utf8');
  assert.ok(drawer.includes('`진행 중 ${summary.done}/${summary.total}`'));
  assert.ok(!drawer.includes('`받는 중 ${summary.done}'), 'drawer title is "진행 중 d/t"');
  assert.ok(drawer.includes("row.estimated ? '약 ' : ''"));
  assert.ok(/role="status" aria-live="polite"/.test(drawer));
  assert.ok(!drawer.includes('role="alert"') && !drawer.includes('aria-live="assertive"'));
  assert.ok(drawer.includes('aria-valuetext'));
  // The spoken key is phase|label; the note joins it only while waiting for subtitles, so "반복 구간 다시 인식 중 i/n" is not read out for every i.
  assert.ok(drawer.includes("`${row.phase}|${row.label}${row.phase === 'waiting_transcript' ? `|${row.note ?? ''}` : ''}`"));
}

// Pure content helpers.
{
  const content = await import('../src/lib/content.ts');
  assert.deepEqual(content.splitCourseName('컴퓨터네트워크(2026123401)'), { title: '컴퓨터네트워크', code: '2026123401' });
  assert.deepEqual(content.splitCourseName('운영체제 (A반)'), { title: '운영체제 (A반)', code: null });
  assert.equal(content.triState(['a', 'b'], new Set(['a'])), 'some');
  assert.equal(content.triState(['a', 'b'], new Set(['a', 'b'])), 'all');
  assert.equal(content.triState([], new Set()), 'none');
  const groups = content.groupByWeek([
    { module_id: 1, week_position: 1, module_name: '1주차', module_item_id: 1 },
    { module_id: 2, week_position: 2, module_name: '', module_item_id: 2 },
    { module_id: 1, week_position: 1, module_name: '1주차', module_item_id: 3 },
  ]);
  assert.deepEqual(groups.map((group) => [group.title, group.items.length]), [['1주차', 2], ['2주차', 1]]);
  const { formatBytes } = await import('../src/lib/format.ts');
  assert.equal(formatBytes(7.04 * 1024 ** 3), '7.0GB');
  assert.equal(formatBytes(340 * 1024 ** 2), '340MB');
}

console.log('frontend contract ok');

// Watching stays available while a video download runs; refresh is only blocked by installs.
{
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.ok(app.includes("downloads.active && tab !== 'video'"), 'video watch must not be blocked by an active download');
  assert.ok(app.includes('const busyElsewhere = media.isInstalling;'), 'refresh must not be blocked by an active download');
  const downloads = readFileSync(new URL('../src/hooks/useDownloads.ts', import.meta.url), 'utf8');
  assert.ok(downloads.includes('autoRetryUsedRef'), 'native CDN rejection must trigger one automatic re-plan');
}

// Attendance deadlines: D-day before due_at, late window until late_at/lock_at, then missed.
{
  const { lectureDeadline, courseDeadline } = await import('../src/lib/deadline.ts');
  const now = new Date('2026-10-15T03:00:00Z');
  const base = { completed: false, unlock_at: '2026-10-12T15:00:00Z', late_at: '2026-10-26T14:59:59Z' };
  assert.equal(lectureDeadline({ ...base, completed: true, due_at: '2026-10-19T14:59:59Z' }, now), null);
  const open = lectureDeadline({ ...base, due_at: '2026-10-19T14:59:59Z' }, now);
  assert.equal(open.badge, 'D-4');
  assert.equal(open.tone, 'normal');
  assert.equal(lectureDeadline({ ...base, due_at: '2026-10-16T14:59:59Z' }, now).tone, 'soon');
  assert.equal(lectureDeadline({ ...base, due_at: '2026-10-15T14:59:59Z' }, now).badge, '오늘 마감');
  assert.equal(lectureDeadline({ ...base, due_at: '2026-10-12T14:59:59Z' }, now).tone, 'late');
  assert.equal(lectureDeadline({ ...base, due_at: '2026-10-01T14:59:59Z', late_at: '2026-10-08T14:59:59Z' }, now).badge, '기한 지남');
    assert.equal(lectureDeadline({ completed: false }, now), null);
  assert.equal(courseDeadline('2026-10-19T14:59:59Z', true, now).badge, '지각 D-4');
  assert.equal(courseDeadline(null, false, now), null);
}

// Deadlines in Korea time: unopened weeks, assignments/quizzes, grouping and the course hints.
{
  process.env.TZ = 'Asia/Seoul';
  const deadline = await import('../src/lib/deadline.ts');
  const content = await import('../src/lib/content.ts');
  const now = new Date('2026-10-15T03:00:00Z'); // 10/15(목) 12:00 KST

  // Not open yet: its own state, never "unwatched".
  const unopened = { completed: false, unlock_at: '2026-10-26T15:00:00Z', due_at: '2026-11-02T14:59:59Z' };
  assert.equal(deadline.isOpened(unopened, now), false);
  assert.equal(deadline.isOpened({ ...unopened, unlock_at: '2026-10-12T15:00:00Z' }, now), true);
  assert.equal(deadline.isOpened({ completed: false }, now), true);
  const unopenedInfo = deadline.lectureDeadline(unopened, now);
  assert.equal(unopenedInfo.badge, '10/27(화)부터');
  assert.equal(unopenedInfo.tone, 'muted');
  assert.equal(unopenedInfo.when, null);
  assert.equal(deadline.taskDeadline(unopened, now).badge, '10/27(화)부터');

  // Lectures with only a lock time count it as the deadline, not as a late window.
  assert.equal(deadline.lectureDeadline({ completed: false, lock_at: '2026-10-19T14:59:59Z' }, now).badge, 'D-4');

  // Assignments and quizzes: due first, then lock; quizzes close at 23:59:00 KST.
  const task = { completed: false, unlock_at: '2026-10-01T00:00:00Z' };
  assert.equal(deadline.taskDeadline({ ...task, completed: true, due_at: '2026-10-19T14:59:00Z' }, now), null);
  const quiz = deadline.taskDeadline({ ...task, due_at: '2026-10-19T14:59:00Z' }, now);
  assert.equal(quiz.badge, 'D-4');
  assert.equal(quiz.when, '10/19(월) 23:59');
  assert.equal(deadline.taskDeadline({ ...task, due_at: '2026-10-15T14:59:00Z' }, now).badge, '오늘 마감');
  const afterDue = deadline.taskDeadline({ ...task, due_at: '2026-10-10T14:59:00Z', lock_at: '2026-10-18T14:59:59Z' }, now);
  assert.equal(afterDue.tone, 'late');
  assert.equal(afterDue.when, '10/18(일) 23:59');
  assert.equal(deadline.taskDeadline({ ...task, due_at: '2026-10-10T14:59:00Z', lock_at: '2026-10-12T14:59:59Z' }, now).badge, '기한 지남');
  assert.equal(deadline.taskDeadline({ ...task, lock_at: '2026-10-20T14:59:59Z' }, now).badge, 'D-5');
  assert.equal(deadline.taskDeadline(task, now), null);
  assert.equal(deadline.taskStatus({ ...task, completed: true }, now), 'done');
  assert.equal(deadline.taskStatus({ ...task, due_at: '2026-10-10T14:59:00Z' }, now), 'overdue');
  assert.equal(deadline.taskStatus({ ...task, due_at: '2026-10-10T14:59:00Z', lock_at: '2026-10-20T14:59:00Z' }, now), 'pending');
  assert.equal(deadline.taskStatus(task, now), 'pending');

  // Grouping: 오늘 / 3일 이내 / 이번 주 / 이후 / 놓침, nearest first; done items and undated ones are left out.
  const entry = (title, kind, fields) => ({ title, kind, completed: false, ...fields });
  const rows = [
    entry('later quiz', 'quiz', { due_at: '2026-11-20T14:59:00Z' }),
    entry('week task', 'assignment', { due_at: '2026-10-21T14:59:59Z' }),
    entry('soon lecture', 'video', { unlock_at: '2026-10-12T15:00:00Z', due_at: '2026-10-17T14:59:59Z' }),
    entry('today task', 'assignment', { due_at: '2026-10-15T14:59:59Z' }),
    entry('tomorrow quiz', 'quiz', { due_at: '2026-10-16T14:59:00Z' }),
    entry('missed recently', 'assignment', { due_at: '2026-10-13T14:59:59Z' }),
    entry('missed earlier', 'quiz', { due_at: '2026-10-05T14:59:00Z' }),
    entry('done', 'assignment', { completed: true, due_at: '2026-10-16T14:59:59Z' }),
    entry('no dates', 'assignment', {}),
    entry('not open lecture', 'video', { unlock_at: '2026-10-22T15:00:00Z', due_at: '2026-10-29T14:59:59Z' }),
  ];
  const groups = deadline.groupDeadlines(rows, now);
  assert.deepEqual(groups.map((group) => group.key), ['today', 'soon', 'week', 'later', 'missed']);
  assert.deepEqual(groups.map((group) => group.title), ['오늘', '3일 이내', '이번 주', '이후', '놓침']);
  const titles = (key) => groups.find((group) => group.key === key).entries.map((e) => e.item.title);
  assert.deepEqual(titles('today'), ['today task']);
  assert.deepEqual(titles('soon'), ['tomorrow quiz', 'soon lecture']);
  assert.deepEqual(titles('week'), ['week task']);
  assert.deepEqual(titles('later'), ['not open lecture', 'later quiz']);
  assert.deepEqual(titles('missed'), ['missed recently', 'missed earlier']);
  const missedEntry = groups.find((group) => group.key === 'missed').entries[0];
  assert.equal(missedEntry.info.badge, '기한 지남');
  assert.equal(missedEntry.info.when, '10/13(화) 23:59');
  const notOpen = groups.find((group) => group.key === 'later').entries[0];
  assert.equal(notOpen.upcoming, true);
  assert.equal(notOpen.info.tone, 'muted');
  assert.equal(notOpen.info.badge, 'D-14');
  assert.equal(deadline.groupDeadlines([], now).length, 0);

  // The top bar badge counts what is due within three days and can be done now.
  assert.equal(deadline.dueSoonCount(rows, now), 3);
  assert.equal(deadline.dueSoonCount([entry('open soon but not open', 'video', { unlock_at: '2026-10-16T00:00:00Z', due_at: '2026-10-17T00:00:00Z' })], now), 0);

  // Sidebar hint: the nearer of lecture and task deadlines; missed = lectures + tasks.
  const hint = deadline.courseNextDeadline({ next_deadline_at: '2026-10-22T14:59:59Z', next_task_deadline_at: '2026-10-17T14:59:00Z' }, now);
  assert.equal(hint.badge, '과제 D-2');
  assert.equal(deadline.courseNextDeadline({ next_deadline_at: '2026-10-16T14:59:59Z', next_task_deadline_at: '2026-10-17T14:59:00Z' }, now).badge, 'D-1');
  assert.equal(deadline.courseNextDeadline({ next_deadline_at: '2026-10-19T14:59:59Z', next_deadline_is_late: true }, now).badge, '지각 D-4');
  assert.equal(deadline.courseNextDeadline({}, now), null);
  assert.equal(deadline.courseNextDeadline({ next_task_deadline_at: '2026-10-01T14:59:00Z' }, now), null);
  assert.equal(deadline.courseMissedCount({ missed_lecture_count: 1, overdue_task_count: 2 }), 3);
  assert.equal(deadline.courseMissedCount({}), 0);

  // Course texts: only open unwatched lectures are "안 본 강의".
  const course = (fields) => ({ id: 1, name: 'c', module_count: 1, lecture_count: 4, uncompleted_lecture_count: 0, ...fields });
  assert.equal(content.courseWatchSummary(course({ uncompleted_lecture_count: 2 })), '안 본 강의 2개');
  assert.equal(content.courseWatchSummary(course({ upcoming_lecture_count: 2 })), '모두 시청함');
  assert.equal(content.courseUpcomingText(course({ upcoming_lecture_count: 2 })), '· 예정 2');
  assert.equal(content.courseUpcomingText(course({ uncompleted_lecture_count: 1, upcoming_lecture_count: 2 })), null);
  assert.equal(content.courseWatchSummary(course({ upcoming_lecture_count: 4 })), '열린 강의 없음');
  assert.equal(content.courseWatchSummary(course({ lecture_count: 0 })), '영상 없음');
  const video = (completed, unlock_at) => ({ kind: 'video', completed, unlock_at });
  assert.deepEqual(content.countVideos([video(true, null), video(false, '2026-10-01T00:00:00Z'), video(false, '2026-10-26T15:00:00Z'), video(false, '2026-10-27T15:00:00Z'), { kind: 'material', completed: null }], now), { total: 4, watched: 1, upcoming: 2 });
  assert.equal(content.isUnwatchedNow(video(false, '2026-10-26T15:00:00Z'), now), false);
  assert.equal(content.isUnwatchedNow(video(false, '2026-10-01T00:00:00Z'), now), true);
  assert.equal(content.isUnwatchedNow(video(true, null), now), false);

  // Default course: open unwatched lectures first, then the nearest deadline (lecture or task).
  const a = course({ id: 'a', lecture_count: 2, next_deadline_at: '2026-10-25T14:59:59Z' });
  const b = course({ id: 'b', lecture_count: 2, next_task_deadline_at: '2026-10-17T14:59:00Z' });
  const c = course({ id: 'c', lecture_count: 2, uncompleted_lecture_count: 1 });
  assert.equal(content.pickDefaultCourse([a, b, c], now).id, 'c');
  assert.equal(content.pickDefaultCourse([a, b], now).id, 'b');
  assert.equal(content.pickDefaultCourse([course({ id: 'x', lecture_count: 0 }), course({ id: 'y' })], now).id, 'y');
  assert.equal(content.pickDefaultCourse([], now), null);

  // Task tab order: nearest deadline, undated, overdue, done; URLs are made absolute.
  const t = (title, fields) => ({ kind: 'assignment', title, completed: false, ...fields });
  const sorted = content.sortTasks([
    t('done', { completed: true, due_at: '2026-10-16T14:59:00Z' }),
    t('overdue', { due_at: '2026-10-01T14:59:00Z' }),
    t('undated', {}),
    t('later', { due_at: '2026-10-30T14:59:00Z' }),
    t('soon', { due_at: '2026-10-17T14:59:00Z' }),
  ], now).map((item) => item.title);
  assert.deepEqual(sorted, ['soon', 'later', 'undated', 'overdue', 'done']);
  assert.equal(content.lmsItemUrl('/courses/1/assignments/2'), 'https://canvas.ssu.ac.kr/courses/1/assignments/2');
  assert.equal(content.lmsItemUrl('https://canvas.ssu.ac.kr/x'), 'https://canvas.ssu.ac.kr/x');
  assert.equal(content.lmsItemUrl('javascript:alert(1)'), null);
  assert.equal(content.lmsItemUrl(null), null);
}

// UI wiring of the deadline features.
{
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.ok(app.includes('pickDefaultCourse') && app.includes('isUnwatchedNow'), 'default course and the unwatched filter must use the open-aware helpers');
  const toolbar = readFileSync(new URL('../src/components/LectureToolbar.tsx', import.meta.url), 'utf8');
  assert.ok(toolbar.includes('과제·퀴즈'), 'course view needs the assignments/quizzes tab');
  const topbar = readFileSync(new URL('../src/components/TopBar.tsx', import.meta.url), 'utf8');
  assert.ok(topbar.includes('count-badge') && topbar.includes('기한'), 'top bar needs the deadlines button with a count badge');
  const pane = readFileSync(new URL('../src/components/DeadlinesPane.tsx', import.meta.url), 'utf8');
  assert.ok(pane.includes('과제의 완료 표시는 LMS 기준이라 실제 제출 여부와 다를 수 있어요.'), 'deadline overview must say task completion is LMS-based');
  const tasks = readFileSync(new URL('../src/components/TaskList.tsx', import.meta.url), 'utf8');
  assert.ok(!tasks.includes('Checkbox'), 'task rows are not selectable');
}

// LMS links open only for Soongsil hosts.
{
  const { lmsItemUrl } = await import('../src/lib/content.ts');
  assert.ok(lmsItemUrl('/courses/1/assignments/2')?.startsWith('https://'));
  assert.equal(lmsItemUrl('https://canvas.ssu.ac.kr/courses/1/quizzes/3'), 'https://canvas.ssu.ac.kr/courses/1/quizzes/3');
  assert.equal(lmsItemUrl('https://evil.example/ssu.ac.kr'), null);
  assert.equal(lmsItemUrl('http://canvas.ssu.ac.kr/x'), null);
}

// Deadline reminders: the cache the UI hands to the app, the settings section and the commands.
{
  process.env.TZ = 'Asia/Seoul';
  const { buildDeadlineCache, deadlineCacheId } = await import('../src/lib/reminders.ts');
  const now = new Date('2026-10-15T03:00:00Z');
  const base = { course_id: 7, course_name: '운영체제(2026123401)', kind: 'assignment', title: '과제 1', module_item_id: 42, url: 'https://canvas.ssu.ac.kr/courses/7/assignments/42', unlock_at: null, due_at: null, late_at: null, lock_at: null, completed: false, state: 'open' };
  const cache = buildDeadlineCache([
    { ...base, due_at: '2026-10-19T14:59:00Z' },
    { ...base, module_item_id: 43, title: '완료', completed: true, due_at: '2026-10-19T14:59:00Z' },
    { ...base, module_item_id: 44, title: '놓침', due_at: '2026-10-01T14:59:00Z' },
    { ...base, module_item_id: 45, kind: 'video', title: '지각 구간', due_at: '2026-10-10T14:59:00Z', late_at: '2026-10-18T14:59:59Z' },
    { ...base, module_item_id: null, kind: 'quiz', title: '열리기 전', unlock_at: '2026-10-27T00:00:00Z', due_at: '2026-10-30T14:59:00Z' },
    { ...base, module_item_id: 46, title: '기한 없음' },
  ], now);
  assert.deepEqual(cache.map((entry) => entry.title), ['과제 1', '지각 구간', '열리기 전']);
  assert.deepEqual(cache[0], { id: '7:assignment:42', course: '운영체제', kind: 'assignment', title: '과제 1', deadline_at: Date.parse('2026-10-19T14:59:00Z') / 1000, late: false });
  assert.equal(cache[1].late, true);
  assert.equal(cache[1].deadline_at, Date.parse('2026-10-18T14:59:59Z') / 1000);
  assert.match(cache[2].id, /^7:quiz:t[0-9a-z]+$/);
  assert.equal(cache[2].id, deadlineCacheId({ ...base, module_item_id: null, kind: 'quiz', title: '열리기 전' }));
  for (const entry of cache) {
    assert.deepEqual(Object.keys(entry).sort(), ['course', 'deadline_at', 'id', 'kind', 'late', 'title']);
  }

  const hook = readFileSync(new URL('../src/hooks/useDeadlines.ts', import.meta.url), 'utf8');
  assert.ok(/setState\('ready'\);[\s\S]*writeDeadlineCache\(buildDeadlineCache\(response\.deadlines\)\)/.test(hook), 'the deadline cache must be written right after the deadlines load');
  const desktop = readFileSync(new URL('../src/lib/desktop.ts', import.meta.url), 'utf8');
  for (const command of ['deadline_cache_write', 'reminders_status', 'reminders_set_enabled', 'reminders_test']) {
    assert.ok(desktop.includes(`'${command}'`), `desktop.ts must call ${command}`);
  }
  const dialog = readFileSync(new URL('../src/components/SettingsDialog.tsx', import.meta.url), 'utf8');
  for (const text of ['기한 알림', '기한 하루 전과 3시간 전에 알림 받기', '앱이 꺼져 있어도 마지막으로 불러온 기한으로 알려 줘요. 기한이 바뀌었으면 앱을 한 번 열어 새로 불러와 주세요.', '마지막으로 불러온 시각', '알림 테스트']) {
    assert.ok(dialog.includes(text), `settings dialog missing: ${text}`);
  }
  const lib = readFileSync(new URL('../src-tauri/src/lib.rs', import.meta.url), 'utf8');
  for (const command of ['deadline_cache_write', 'reminders_status', 'reminders_set_enabled', 'reminders_test']) {
    assert.ok(lib.match(new RegExp(`^\\s+${command},$`, 'm')), `${command} must be registered in the invoke handler`);
  }
  assert.ok(lib.indexOf('is_check_invocation') < lib.indexOf('tauri::Builder'), '--check-reminders must exit before Tauri starts');
}

// ---------------------------------------------------------------------------
// Sequential watch: queue order, status strip, tray/commands wiring.
// ---------------------------------------------------------------------------

// Queue: open, unwatched videos; nearest attendance deadline first (late windows by their end),
// then week order; undated after dated; fully closed last; unopened, watched, duplicate and non-video rows out.
{
  process.env.TZ = 'Asia/Seoul';
  const { buildWatchQueue } = await import('../src/lib/content.ts');
  const now = new Date('2026-10-15T03:00:00Z'); // 10/15(목) 12:00 KST
  const open = '2026-10-01T00:00:00Z';
  const video = (id, week, fields) => ({ kind: 'video', module_item_id: id, title: `v${id}`, week_position: week, lesson_position: null, completed: false, unlock_at: open, due_at: null, late_at: null, lock_at: null, ...fields });
  const items = [
    video('a', 1, { due_at: '2026-10-19T14:59:59Z' }),
    video('c', 3, { due_at: '2026-10-16T14:59:59Z' }),
    video('b', 2, { due_at: '2026-10-16T14:59:59Z' }),
    video('d', 4, { completed: true, due_at: '2026-10-16T14:59:59Z' }),
    video('e', 5, { unlock_at: '2026-10-26T15:00:00Z', due_at: '2026-11-02T14:59:59Z' }),
    video('f', 6),
    video('g', 7, { due_at: '2026-10-10T14:59:59Z', late_at: '2026-10-15T14:59:59Z' }),
    video('h', 8, { due_at: '2026-10-01T14:59:59Z', late_at: '2026-10-05T14:59:59Z' }),
    video('a', 1, { due_at: '2026-10-19T14:59:59Z' }),
    { ...video('j', 9, { due_at: '2026-10-16T00:00:00Z' }), kind: 'material' },
    video('k', 2, { completed: null }),
    video(null, 2, { due_at: '2026-10-16T00:00:00Z' }),
  ];
  assert.deepEqual(buildWatchQueue(items, now).map((item) => item.module_item_id), ['g', 'b', 'c', 'a', 'k', 'f', 'h']);
  assert.deepEqual(buildWatchQueue([], now), []);
  assert.deepEqual(buildWatchQueue([video('x', 1, { completed: true })], now), []);
  // Same deadline, so week order decides (then lesson order, then the LMS order).
  const sameDay = buildWatchQueue([video('w2', 2, { due_at: '2026-10-20T14:59:59Z' }), video('l2', 1, { lesson_position: 2, due_at: '2026-10-20T14:59:59Z' }), video('l1', 1, { lesson_position: 1, due_at: '2026-10-20T14:59:59Z' })], now);
  assert.deepEqual(sameDay.map((item) => item.module_item_id), ['l1', 'l2', 'w2']);
}

// All-course queue ("전체 순서대로 시청"): the term's deadline entries -> one queue across courses.
{
  process.env.TZ = 'Asia/Seoul';
  const { buildAllCoursesQueue, summarizeAllCoursesQueue, formatTotalTime, watchAllLabel, watchAllHint, watchAllConfirmText } = await import('../src/lib/watch.ts');
  const now = new Date('2026-10-15T03:00:00Z'); // 10/15(목) 12:00 KST
  const entry = (course, id, fields) => ({
    course_id: course, course_name: `과목${course}(1234567${course}-01)`, kind: 'video', title: `${id}주차 강의`, module_item_id: id, url: null,
    unlock_at: '2026-10-01T00:00:00Z', due_at: '2026-10-20T14:59:59Z', late_at: null, lock_at: null, completed: false, duration: 600, state: 'open', ...fields,
  });
  const entries = [
    entry(2, 21, { due_at: '2026-10-18T14:59:59Z' }),
    entry(1, 11, { due_at: '2026-10-16T14:59:59Z', duration: 1500.5 }),
    entry(1, 12, { due_at: '2026-10-20T14:59:59Z', duration: null }),
    entry(3, 31, { due_at: '2026-10-10T14:59:59Z', late_at: '2026-10-17T14:59:59Z', state: 'late' }), // late window: counts by its end
    entry(3, 32, { unlock_at: '2026-10-26T15:00:00Z', due_at: '2026-11-02T14:59:59Z', state: 'upcoming' }), // not open yet
    entry(3, 33, { due_at: '2026-10-01T14:59:59Z', late_at: '2026-10-05T14:59:59Z', state: 'missed' }), // every window closed: cannot count any more
    entry(2, 22, { completed: true }),
    entry(2, 21, { due_at: '2026-10-18T14:59:59Z' }), // duplicate
    { ...entry(2, 23, {}), kind: 'assignment' },
    { ...entry(2, 24, {}), module_item_id: null },
    entry(4, 11, { due_at: '2026-10-16T14:59:59Z' }), // the same module item id in another course is another lecture
    entry(2, 25, { due_at: '2026-10-16T14:59:59Z', state: 'missed' }), // the stored state is stale: the times say it is open
  ];
  const queue = buildAllCoursesQueue(entries, now);
  assert.deepEqual(queue.map((item) => `${item.course_id}:${item.module_item_id}`), ['1:11', '4:11', '2:25', '3:31', '2:21', '1:12'], 'nearest deadline first (late windows by their end), ties in list order');
  assert.deepEqual(queue[0], { module_item_id: '11', course_id: '1', course_name: '과목1(12345671-01)', title: '과목1 · 11주차 강의', duration: 1500.5 }, 'course name without its code leads the title; ids are text');
  assert.equal(queue.find((item) => item.module_item_id === '12').duration, null);
  // The ids already played in this session are skipped; nothing open gives an empty queue.
  assert.deepEqual(buildAllCoursesQueue(entries, now, new Set(['11', '21'])).map((item) => `${item.course_id}:${item.module_item_id}`), ['2:25', '3:31', '1:12']);
  assert.deepEqual(buildAllCoursesQueue([], now), []);
  assert.deepEqual(buildAllCoursesQueue([entry(1, 1, { state: 'missed', due_at: '2026-10-01T00:00:00Z' })], now), []);
  // Later the same data gives a different answer: the clock decides, not the stored state.
  assert.deepEqual(buildAllCoursesQueue(entries, new Date('2026-10-19T00:00:00Z')).map((item) => `${item.course_id}:${item.module_item_id}`), ['1:12']);
  assert.deepEqual(buildAllCoursesQueue(entries, new Date('2026-10-19T00:00:00Z')).map((item) => item.module_item_id), ['31', '21', '12'].filter((id) => id !== '31' && id !== '21' || false).concat([]).length ? buildAllCoursesQueue(entries, new Date('2026-10-19T00:00:00Z')).map((item) => item.module_item_id) : []);

  const summary = summarizeAllCoursesQueue(queue);
  assert.deepEqual(summary, { count: 6, courseCount: 4, totalSeconds: 1500.5 + 600 * 4, unknownLengthCount: 1 });
  assert.equal(formatTotalTime(0), '-');
  assert.equal(formatTotalTime(30), '1분 미만');
  assert.equal(formatTotalTime(35 * 60), '약 35분');
  assert.equal(formatTotalTime(3600), '약 1시간');
  assert.equal(formatTotalTime(3600 + 12 * 60 - 5), '약 1시간 12분');
  // Copy of the entry points and the confirmation.
  assert.equal(watchAllLabel(6, false), '전체 순서대로 시청 (6)');
  assert.equal(watchAllLabel(6, true), '순서대로 시청 중');
  assert.match(watchAllHint(0, { queueActive: false, loading: false }), /안 본 강의가 없어요/);
  assert.match(watchAllHint(0, { queueActive: false, loading: true }), /불러오는 중/);
  assert.match(watchAllHint(6, { queueActive: false, loading: false }), /6개/);
  assert.match(watchAllHint(6, { queueActive: true, loading: false }), /자동 재생 중/);
  const confirm = watchAllConfirmText(summary);
  assert.equal(confirm.headline, '강의 6개 (과목 4개) · 총 약 1시간 6분 (길이를 모르는 1개는 빼고)');
  const confirmText = confirm.lines.join(' ');
  for (const part of ['출석 기한이 가까운 순서', '소리 없이', '항상 위에', '최소화하면 재생이 멈추니', '기한이 모두 지난 강의']) assert.ok(confirmText.includes(part), `confirmation mentions ${part}`);

  const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
  const topbar = read('src/components/TopBar.tsx');
  const deadlinesPane = read('src/components/DeadlinesPane.tsx');
  assert.ok(topbar.includes('watchAllLabel(watchAllCount, queueActive)') && topbar.includes('watchAllHint(') && topbar.includes('disabled={queueActive || watchAllCount === 0}'), 'the top bar has the all-course button, disabled with a reason when nothing can be played');
  assert.ok(deadlinesPane.includes('watchAllLabel(watchAllCount, queueActive)') && deadlinesPane.includes('onWatchAll'), 'the 다가오는 기한 view has the same action');
  const appSource = read('src/App.tsx');
  assert.ok(appSource.includes('buildAllCoursesQueue(deadlines.items') && appSource.includes('<WatchAllDialog') && appSource.includes('viewer.startAllQueue('), 'the all-course queue comes from the deadlines and starts after a confirmation');
  assert.ok(appSource.includes("scope === 'all'") && appSource.includes('refreshSession()'), 'after the queue ends the course list (and so the deadlines) is reloaded');
  const dialog = read('src/components/WatchAllDialog.tsx');
  assert.ok(dialog.includes('전체 순서대로 시청') && dialog.includes('시작') && dialog.includes('취소'));
  const viewerHook = read('src/hooks/useOfficialViewer.ts');
  assert.ok(viewerHook.includes('watchQueueStart({ items: queueItems })'), 'the all-course start names no single course');
}

// Status strip copy and queue inputs.
{
  const { watchStripText, formatClock, WATCH_STRIP_NOTE, IDLE_WATCH_QUEUE, toQueueInputs, watchFinishedText } = await import('../src/lib/watch.ts');
  const running = { ...IDLE_WATCH_QUEUE, active: true, phase: 'playing', index: 2, total: 7, current_title: '3주차 강의', position_secs: 69, duration_secs: 450 };
  assert.deepEqual(watchStripText(running), { summary: '자동 재생 중 3/7 · 3주차 강의 · 01:09 / 07:30', detail: null });
  assert.match(watchStripText({ ...running, rendering: false }).detail, /가려져/);
  assert.match(watchStripText({ ...running, paused: true }).detail, /일시정지/);
  assert.equal(watchStripText({ ...running, phase: 'opening', position_secs: null, duration_secs: null }).summary, '자동 재생 중 3/7 · 3주차 강의');
  assert.match(watchStripText({ ...running, phase: 'stalled' }).detail, /다시 불러오고/);
  assert.equal(watchStripText(IDLE_WATCH_QUEUE), null);
  assert.equal(watchStripText({ ...running, active: false, phase: 'finished' }), null);
  assert.equal(WATCH_STRIP_NOTE, '재생 창이 가려지거나 최소화되면 재생이 멈춰요. 화면 구석에 보이게 두세요.');
  assert.equal(formatClock(69.9), '01:09');
  assert.equal(formatClock(3725), '1:02:05');
  assert.equal(formatClock(null), '00:00');
  assert.equal(watchFinishedText({ ...IDLE_WATCH_QUEUE, done_count: 6, skipped_count: 1 }), '자동 재생을 마쳤어요. 6개 시청 · 1개 건너뜀');
  assert.deepEqual(toQueueInputs([{ module_item_id: 7, title: 'a', duration: 450.5 }, { module_item_id: null, title: 'x', duration: 1 }, { module_item_id: '8', title: 'b', duration: 0 }]), [
    { module_item_id: '7', title: 'a', duration: 450.5 },
    { module_item_id: '8', title: 'b', duration: null },
  ]);

  const strip = readFileSync(new URL('../src/components/WatchStrip.tsx', import.meta.url), 'utf8');
  for (const text of ['다음', '중지', 'WATCH_STRIP_NOTE', 'watchStripText']) assert.ok(strip.includes(text), `status strip missing ${text}`);
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.ok(app.includes('<WatchStrip queue={viewer.queue}') && app.includes('buildWatchQueue(content.items'), 'the strip shows the shell queue, started from the full course item list');
  assert.ok(app.includes('markWatched') && app.includes('refreshQuietly'), 'a finished lecture is marked watched locally, the LMS asked later');
  const toolbar = readFileSync(new URL('../src/components/LectureToolbar.tsx', import.meta.url), 'utf8');
  assert.ok(toolbar.includes('순서대로 시청') && !toolbar.includes('onStopWatch'), 'stopping lives in the status strip');
}

// Chrome window playback: mode choice, status -> strip/menu bar mapping, copy and wiring.
{
  const watch = await import('../src/lib/watch.ts');
  const { resolveWatchMode, chromeStatusToQueue, toExternalView, watchStripText, watchStripNote, newChromeNotices, toChromeInputs, toChromeAllInputs, watchAllConfirmText, summarizeAllCoursesQueue, WATCH_MODE_LABEL, WATCH_STRIP_NOTE, WATCH_STRIP_NOTE_CHROME, CHROME_RESTORE_HINT, IDLE_WATCH_QUEUE, readWatchModePreference, writeWatchModePreference } = watch;
  const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');

  // Mode selection: Chrome by default when it is installed, the saved choice otherwise.
  assert.equal(resolveWatchMode(null, true), 'chrome');
  assert.equal(resolveWatchMode(null, false), 'app');
  assert.equal(resolveWatchMode(null, null), 'app', 'unknown availability stays with the in-app window');
  assert.equal(resolveWatchMode('app', true), 'app');
  assert.equal(resolveWatchMode('chrome', true), 'chrome');
  assert.equal(resolveWatchMode('chrome', null), 'chrome');
  assert.equal(resolveWatchMode('chrome', false), 'app', 'a saved Chrome choice falls back once Chrome is known to be missing');
  assert.equal(WATCH_MODE_LABEL.chrome, 'Chrome 창 (다른 작업 중에도 재생, 추천)');
  assert.equal(WATCH_MODE_LABEL.app, '앱 안의 작은 창');
  const memory = new Map();
  globalThis.localStorage = { getItem: (key) => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) };
  assert.equal(readWatchModePreference(), null);
  writeWatchModePreference('app');
  assert.equal(readWatchModePreference(), 'app');
  memory.set('ssu.watchMode', 'bogus');
  assert.equal(readWatchModePreference(), null);
  globalThis.localStorage = { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } };
  assert.equal(readWatchModePreference(), null, 'blocked storage must not break the page');
  assert.doesNotThrow(() => writeWatchModePreference('chrome'));
  delete globalThis.localStorage;

  // Backend status -> the queue state the strip, buttons and menu bar use.
  const status = (fields) => ({
    ok: true, state: 'playing', run_id: 'r1', active: true, chrome_available: true, index: 0, total: 2, done_count: 0, skipped_count: 0,
    current: { module_item_id: '11', course_id: '1', course_name: '디지털논리회로', title: '1주차 강의', phase: 'playing', position: 61, duration: 3026, paused: false },
    items: [
      { module_item_id: '11', course_id: '1', course_name: '디지털논리회로', title: '1주차 강의', state: 'playing', reason: null, duration: 3026 },
      { module_item_id: '12', course_id: '1', course_name: '디지털논리회로', title: '2주차 강의', state: 'pending', reason: null, duration: 1800 },
    ],
    error_code: null, error_message: null, notices: [], ...fields,
  });
  const playing = chromeStatusToQueue(status({}));
  assert.equal(playing.active, true);
  assert.equal(playing.source, 'chrome');
  assert.equal(playing.phase, 'playing');
  assert.equal(playing.remaining_secs, 2965);
  assert.equal(playing.total_remaining_secs, 2965 + 1800, 'the current lecture\'s time left plus the pending lengths');
  assert.deepEqual(watchStripText(playing), { summary: '자동 재생 중 (Chrome) 1/2 · 디지털논리회로 · 1주차 강의 · 01:01 / 50:26', detail: null });
  assert.equal(watchStripNote(playing), WATCH_STRIP_NOTE_CHROME);
  assert.equal(watchStripNote({ ...IDLE_WATCH_QUEUE, active: true }), WATCH_STRIP_NOTE, 'the in-app queue keeps its own note');
  assert.equal(WATCH_STRIP_NOTE_CHROME, '재생이 시작되면 Chrome 창은 자동으로 최소화돼요. 소리는 나지 않아요.');
  // All-course titles already lead with the course: it is not repeated.
  assert.equal(watchStripText(chromeStatusToQueue(status({ current: { ...status({}).current, title: '디지털논리회로 · 1주차 강의' } }))).summary, '자동 재생 중 (Chrome) 1/2 · 디지털논리회로 · 1주차 강의 · 01:01 / 50:26');
  // A course code in the name is left out of the line.
  assert.equal(watchStripText(chromeStatusToQueue(status({ current: { ...status({}).current, course_name: '디지털논리회로(21500123-01)' } }))).summary, '자동 재생 중 (Chrome) 1/2 · 디지털논리회로 · 1주차 강의 · 01:01 / 50:26');

  // Time stopping is visible, with the way out; nothing claims that minimizing works.
  const stalled = watchStripText(chromeStatusToQueue(status({ current: { ...status({}).current, phase: 'stalled' } })));
  assert.match(stalled.detail, /멈춰/);
  assert.ok(stalled.detail.includes(CHROME_RESTORE_HINT) && CHROME_RESTORE_HINT === 'Chrome 창이 최소화돼 있으면 다시 열어 주세요.');
  assert.ok(watchStripText(chromeStatusToQueue(status({ current: { ...status({}).current, paused: true } }))).detail.includes(CHROME_RESTORE_HINT));
  const starting = chromeStatusToQueue(status({ state: 'starting', current: null, index: 0 }));
  assert.equal(starting.phase, 'opening');
  assert.deepEqual(watchStripText({ ...starting, current_title: '1주차 강의' }).detail, 'Chrome 창을 여는 중이에요…');

  // Only a starting or playing run is active; ends map to the shell's phases.
  const finished = chromeStatusToQueue(status({ state: 'finished', active: false, index: 2, done_count: 2, current: null }));
  assert.deepEqual([finished.active, finished.phase, finished.total_remaining_secs], [false, 'finished', 0]);
  assert.equal(watchStripText(finished), null);
  for (const state of ['stopped', 'failed', 'idle']) {
    const ended = chromeStatusToQueue(status({ state, active: false, current: null }));
    assert.deepEqual([ended.active, ended.phase], [false, 'idle'], state);
  }
  assert.deepEqual(chromeStatusToQueue(status({})).items.map((item) => [item.module_item_id, item.state, item.duration_secs]), [['11', 'playing', 3026], ['12', 'pending', 1800]]);

  // What the shell gets for the menu bar: the queue, the run and its notices.
  const notices = [{ id: 2, kind: 'skipped', level: 'error', text: 'b', item_title: 'x', reason: 'r' }, { id: 1, kind: 'stalled', level: 'error', text: 'a', item_title: 'x', reason: null }, { id: 3, kind: 'finished', level: 'success', text: 'c', item_title: null, reason: null }];
  const view = toExternalView(status({ notices }));
  assert.equal(view.run_id, 'r1');
  assert.equal(view.notices.length, 3);
  assert.equal(view.current_title, '1주차 강의');
  assert.equal(toExternalView(status({ run_id: null })).run_id, '');
  assert.deepEqual(newChromeNotices(status({ notices }), 1).map((notice) => notice.id), [2, 3]);
  assert.deepEqual(newChromeNotices(status({ notices }), 3), []);

  // Inputs for the backend carry each lecture's own course.
  assert.deepEqual(toChromeInputs({ id: 44070, name: 'UX' }, [{ module_item_id: 7, title: 'a', duration: 450.5 }, { module_item_id: null, title: 'x', duration: 1 }]), [
    { course_id: '44070', course_name: 'UX', module_item_id: '7', title: 'a', duration: 450.5 },
  ]);
  assert.deepEqual(toChromeAllInputs([{ module_item_id: '11', course_id: '1', course_name: 'A', title: 'A · 11', duration: null }]), [
    { course_id: '1', course_name: 'A', module_item_id: '11', title: 'A · 11', duration: null },
  ]);

  // The confirmation tells which window plays; the Chrome text makes no promise about minimizing.
  const summary = summarizeAllCoursesQueue([{ module_item_id: '1', course_id: '1', course_name: 'A', title: 'A', duration: 600 }]);
  const chromeLines = watchAllConfirmText(summary, 'chrome').lines.join(' ');
  assert.ok(chromeLines.includes('Chrome 창') && chromeLines.includes('자동으로 최소화돼요') && !chromeLines.includes('항상 위에'));
  assert.ok(watchAllConfirmText(summary).lines.join(' ').includes('항상 위에'), 'the default confirmation is still the in-app one');

  // No UI text may claim that a minimized Chrome window keeps playing.
  for (const file of ['src/lib/watch.ts', 'src/components/SettingsDialog.tsx', 'src/components/WatchStrip.tsx', 'src/components/LectureToolbar.tsx', 'src/components/WatchAllDialog.tsx', 'src/hooks/useOfficialViewer.ts']) {
    assert.ok(!/최소화해도/.test(read(file)), `${file} must not promise playback while minimized`);
  }

  // Wiring: settings choice, hook, API, shell command.
  const settings = read('src/components/SettingsDialog.tsx');
  assert.ok(settings.includes('자동 재생 방식') && settings.includes('WATCH_MODE_LABEL') && settings.includes('type="radio"') && settings.includes('onWatchModeChange'), 'settings offer the two playback modes');
  const app = read('src/App.tsx');
  assert.ok(app.includes('readWatchModePreference') && app.includes('modePreference: watchModePreference') && app.includes('watchMode={viewer.mode}') && app.includes('mode={viewer.mode}') && app.includes('onWatchModeChange={chooseWatchMode}'), 'the chosen mode reaches the viewer hook, the dialogs and the settings');
  const viewerHook = read('src/hooks/useOfficialViewer.ts');
  for (const marker of ['startChromeWatch(', 'fetchChromeWatchStatus', 'stopChromeWatch()', 'nextChromeWatch()', 'showChromeWatch()', 'watchExternalStatus(toExternalView(status))', "'watch-external-command'", 'startPolling<ChromeWatchStatus>', "mode === 'chrome'"]) {
    assert.ok(viewerHook.includes(marker), `viewer hook missing ${marker}`);
  }
  assert.ok(!/\bsetInterval\b/.test(viewerHook), 'Chrome status polling is a setTimeout chain');
  assert.ok(viewerHook.includes('watchQueueStart({ items: queueItems })'), 'the in-app all-course start is unchanged');
  const apiSource = read('src/lib/api.ts');
  for (const route of ['/api/watch/chrome/start', '/api/watch/chrome/status', '/api/watch/chrome/next', '/api/watch/chrome/stop', '/api/watch/chrome/show']) assert.ok(apiSource.includes(route), `${route} client`);
  assert.ok(read('src/lib/desktop.ts').includes("invoke<void>('watch_external_status', { view })"));
  assert.ok(/^\s+watch_runtime::watch_external_status,?$/m.test(read('src-tauri/src/lib.rs')), 'the menu bar bridge command is registered');
  assert.ok(read('src-tauri/permissions/main-window-commands.toml').includes('allow-watch-external-status'), 'and allowed for the main window only');
  assert.ok(!read('src-tauri/capabilities/watch-remote.json').includes('external'), 'the remote LMS pages never get the bridge');
  assert.ok(read('src/components/WatchStrip.tsx').includes('Chrome 창 보이기'), 'the strip can bring the Chrome window back');
}

// The shell side: commands registered, tray enabled, window rules, state held in Rust.
{
  const lib = readFileSync(new URL('../src-tauri/src/lib.rs', import.meta.url), 'utf8');
  for (const command of ['watch_runtime::watch_queue_start', 'watch_runtime::watch_queue_next', 'watch_runtime::watch_queue_stop', 'watch_runtime::watch_queue_status', 'watch_runtime::watch_queue_show_window', 'viewer::open_lms_page_with_session']) {
    assert.ok(lib.match(new RegExp(`^\\s+${command},?$`, 'm')), `${command} must be registered in the invoke handler`);
  }
  assert.ok(lib.includes('watch_runtime::shutdown('), 'the playback window, menu bar item and badge go away when the app quits');
  assert.ok(readFileSync(new URL('../src-tauri/Cargo.toml', import.meta.url), 'utf8').includes('"tray-icon"'), 'the menu bar item needs the tauri tray-icon feature');
  const runtime = readFileSync(new URL('../src-tauri/src/watch_runtime.rs', import.meta.url), 'utf8').split('#[cfg(test)]')[0];
  for (const marker of ['TrayIconBuilder', 'PlatformNotifier', 'set_badge(', 'set_tray_title(', 'always_on_top(true)', '.focused(false)', 'bottom_right_position', 'window_title(']) {
    assert.ok(runtime.includes(marker), `watch runtime missing ${marker}`);
  }
  // The badge call and the osascript notifier moved behind the platform seam (src-tauri/src/platform/macos.rs).
  const macosShell = readFileSync(new URL('../src-tauri/src/platform/macos.rs', import.meta.url), 'utf8').split('#[cfg(test)]')[0];
  for (const marker of ['set_badge_count', '/usr/bin/osascript']) {
    assert.ok(macosShell.includes(marker), `macOS platform shell missing ${marker}`);
  }
  const queueRs = readFileSync(new URL('../src-tauri/src/watch_queue.rs', import.meta.url), 'utf8').split('#[cfg(test)]')[0];
  assert.ok(!queueRs.includes('tauri::'), 'the queue state machine stays free of Tauri types');
}

// watch_assist.js: resume dialog, muting, progress/stall reports, and nothing that fakes visibility, speed or position.
const assistSource = readFileSync('src-tauri/src/assets/watch_assist.js', 'utf8');
{
  assert.ok(assistSource.includes('이어서 보시겠습니까') && assistSource.includes("RESUME_YES_LABEL = '예'"), 'the resume dialog is answered with 예');
  assert.ok(assistSource.includes('video.muted = true') && assistSource.includes("signal('progress'") && assistSource.includes("signal('stalled'") && assistSource.includes("signal('ended'"));
  // Signals travel over Tauri IPC. Assigning location aborts the lecture page's own load, so no navigation may remain.
  assert.ok(assistSource.includes('__TAURI_INTERNALS__') && assistSource.includes("SIGNAL_COMMAND = 'watch_signal'"), 'signals are sent with the watch_signal command');
  assert.ok(!/location\s*\.\s*href\s*=[^=]/.test(assistSource) && !/location\s*=[^=]/.test(assistSource) && !/location\s*\.\s*(assign|replace)\s*\(/.test(assistSource), 'watch_assist.js must not navigate to signal');
  assert.ok(!assistSource.includes('ssu-watch'), 'no ssu-watch:// URL signalling is left');
  for (const forbidden of ['visibilityState', 'document.hidden', 'visibilitychange', 'playbackRate', 'Object.defineProperty', 'preventDefault', 'stopImmediatePropagation', /currentTime\s*=[^=]/, 'fastSeek']) {
    const found = typeof forbidden === 'string' ? assistSource.includes(forbidden) : forbidden.test(assistSource);
    assert.ok(!found, `watch_assist.js must not use ${String(forbidden)}`);
  }
  assert.ok(!/\bhidden\b/.test(assistSource), 'watch_assist.js must not read or fake the page-hidden state');
}

// watch_assist.js behaviour on a fake page with virtual time (no real media is ever played).
{
  const DIALOG = '이전에 시청했던 07:29부터 이어서 보시겠습니까?';
  function makePage({ top = true, playWorks = true, ipc = true } = {}) {
    let now = 5_000_000;
    let seq = 0;
    const timers = [];
    const frames = [];
    const env = { rendering: true, navigations: [], posted: [], invocations: [], handlers: {}, clicks: [], writes: [], playCalls: 0, playWorks, dialog: false, videos: [], rejectInvoke: false };
    const rect = () => ({ width: 320, height: 180 });
    const dialogNode = { get textContent() { return env.dialog ? `${DIALOG} 예 아니오` : ''; }, parentElement: null };
    const control = (label) => ({ innerText: label, textContent: label, value: '', parentElement: dialogNode, getBoundingClientRect: () => (env.dialog ? rect() : { width: 0, height: 0 }), click() { env.clicks.push(label); if (label === '예') env.dialog = false; } });
    const yes = control('예');
    const no = control('아니오');
    const sandbox = {
      Date: { now: () => now },
      performance: { now: () => now },
      setTimeout(fn, ms = 0) { timers.push({ id: ++seq, at: now + ms, fn }); return seq; },
      setInterval(fn, ms) { timers.push({ id: ++seq, at: now + ms, fn, every: ms }); return seq; },
      requestAnimationFrame(cb) { frames.push(cb); },
      encodeURIComponent, String, Math, JSON, Array, Object, Number, Promise, Error, URL, console: { ...console, warn() {} },
      addEventListener(type, fn) { (env.handlers[type] ||= []).push(fn); },
      document: {
        readyState: 'complete',
        get body() { return { get textContent() { return dialogNode.textContent; } }; },
        querySelectorAll(selector) {
          if (selector === 'video') return env.videos;
          if (selector.startsWith('button') && env.plainDialog) return [];
          return env.dialog ? [yes, no] : [];
        },
        querySelector() { return null; },
      },
      location: { hostname: 'commons.ssu.ac.kr', set href(value) { env.navigations.push(value); } },
    };
    // Tauri injects this into the top frame of every page of the window (not into child frames).
    if (top && ipc) {
      sandbox.__TAURI_INTERNALS__ = {
        invoke(command, args) {
          env.invocations.push({ command, args: JSON.parse(JSON.stringify(args)) });
          return env.rejectInvoke ? Promise.reject(new Error('not allowed')) : Promise.resolve();
        },
      };
    }
    sandbox.window = sandbox;
    sandbox.top = top ? sandbox : { postMessage(message) { env.posted.push(message); } };
    vm.createContext(sandbox);
    env.video = (duration, time = 0) => {
      const video = {
        paused: true, ended: false, muted: false, readyState: 4, networkState: 1, currentSrc: 'blob:x', src: '', duration,
        _time: time, listeners: {},
        get currentTime() { return this._time; },
        set currentTime(value) { env.writes.push(['currentTime', value]); },
        set playbackRate(value) { env.writes.push(['playbackRate', value]); },
        getBoundingClientRect: rect,
        addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
        play() { env.playCalls += 1; if (env.playWorks) this.paused = false; return Promise.resolve(); },
      };
      env.videos.push(video);
      return video;
    };
    env.advance = (ms) => {
      for (let elapsed = 0; elapsed < ms; elapsed += 100) {
        now += 100;
        for (const video of env.videos) if (!video.paused && !video.ended && env.rendering) video._time += 0.1;
        for (const timer of timers.filter((entry) => entry.at <= now).sort((a, b) => a.at - b.at || a.id - b.id)) {
          if (timer.every) timer.at += timer.every; else timers.splice(timers.indexOf(timer), 1);
          timer.fn();
        }
        if (env.rendering) frames.splice(0).forEach((cb) => cb());
      }
    };
    env.signals = () => (top
      ? env.invocations.map((call) => ({ kind: call.args.kind, params: call.args.payload }))
      : env.posted.map((message) => ({ kind: message.kind, params: message.payload || {} })));
    env.fire = (video, type) => (video.listeners[type] || []).forEach((fn) => fn());
    env.sandbox = sandbox;
    vm.runInContext(assistSource, sandbox);
    return env;
  }

  // Mutes, plays, answers 예 (never 아니오) and reports progress every few seconds.
  {
    const page = makePage();
    const video = page.video(450, 449);
    page.dialog = true;
    page.advance(3000);
    assert.equal(video.muted, true, 'the video is muted');
    assert.ok(page.playCalls >= 1 && video.paused === false, 'play() is called');
    assert.deepEqual(page.clicks, ['예'], 'the resume dialog is answered with 예 exactly once and 아니오 is never pressed');
    page.advance(20000);
    const progress = page.signals().filter((signal) => signal.kind === 'progress');
    assert.ok(progress.length >= 3 && progress.length <= 5, `progress about every 5s, got ${progress.length}`);
    assert.equal(progress[progress.length - 1].params.main, '1');
    assert.equal(progress[progress.length - 1].params.paused, '0');
    assert.equal(progress[progress.length - 1].params.rendering, '1');
    assert.equal(progress[progress.length - 1].params.duration, '450');
    assert.ok(Number(progress[progress.length - 1].params.time) > 449);
    assert.deepEqual(page.writes, [], 'no seeking, no speed changes');
    // Ended: signalled once, only for the main video.
    page.fire(video, 'ended');
    page.fire(video, 'ended');
    page.advance(1000);
    assert.equal(page.signals().filter((signal) => signal.kind === 'ended').length, 1);
  }

  // Players that draw 예 / 아니오 as plain elements instead of buttons are answered too.
  {
    const page = makePage();
    page.plainDialog = true;
    page.video(450, 449);
    page.dialog = true;
    page.advance(3000);
    assert.deepEqual(page.clicks, ['예']);
  }

  // A short intro clip ending is not the lecture ending.
  {
    const page = makePage();
    const intro = page.video(6);
    page.advance(2000);
    page.fire(intro, 'ended');
    page.advance(1000);
    assert.equal(page.signals().filter((signal) => signal.kind === 'ended').length, 0);
    const progress = page.signals().filter((signal) => signal.kind === 'progress');
    assert.ok(progress.length >= 1 && progress.every((signal) => signal.params.main === '0'));
  }

  // A player that never moves is reported as stalled after 60s; a covered window never is.
  {
    const frozen = makePage({ playWorks: false });
    frozen.video(450, 100);
    frozen.advance(50000);
    assert.equal(frozen.signals().filter((signal) => signal.kind === 'stalled').length, 0, 'not before 60s');
    frozen.advance(20000);
    assert.equal(frozen.signals().filter((signal) => signal.kind === 'stalled').length, 1, 'reported once');
    assert.ok(frozen.playCalls >= 3, 'play is retried with backoff meanwhile');
    assert.ok(frozen.playCalls <= 12, `backoff, not a tight loop (${frozen.playCalls} calls)`);

    const covered = makePage({ playWorks: false });
    covered.video(450, 100);
    covered.rendering = false;
    covered.advance(300000);
    assert.equal(covered.signals().filter((signal) => signal.kind === 'stalled').length, 0, 'time without drawing never counts as a stall');
    covered.rendering = true;
    covered.advance(30000);
    assert.equal(covered.signals().filter((signal) => signal.kind === 'stalled').length, 0, 'the 60s start over once the window is drawn again');
    covered.advance(40000);
    assert.equal(covered.signals().filter((signal) => signal.kind === 'stalled').length, 1);
  }

  // A video that plays normally never stalls; the page tells the app whether it is drawn.
  {
    const page = makePage();
    const video = page.video(450);
    page.advance(90000);
    assert.equal(page.signals().filter((signal) => signal.kind === 'stalled').length, 0);
    page.rendering = false;
    page.advance(12000);
    page.rendering = true;
    page.advance(6000);
    const beats = page.signals().filter((signal) => signal.kind === 'heartbeat');
    assert.ok(beats.some((signal) => signal.params.rendering === '0'), 'heartbeat reports a window that is not being drawn');
    assert.ok(beats.some((signal) => signal.params.rendering === '1'));
    assert.ok(video.paused === false);
  }

  // Child frames hand their reports to the top frame instead of navigating themselves.
  {
    const child = makePage({ top: false });
    const video = child.video(450);
    child.advance(6000);
    child.fire(video, 'ended');
    assert.deepEqual(child.navigations, []);
    const kinds = child.posted.map((message) => message.kind);
    assert.ok(kinds.includes('progress') && kinds.includes('ended'), `child reports go to the top frame (${kinds.join(',')})`);
    assert.ok(child.posted.every((message) => message.__SSU_WATCH_SIGNAL__ === true));
  }

  // Signals are Tauri IPC calls of the top frame; the page itself is never navigated (that would abort its load).
  {
    const page = makePage();
    const video = page.video(450, 10);
    assert.deepEqual(page.invocations, [], 'nothing is sent while the script installs: the page load is not touched');
    page.advance(15000);
    page.fire(video, 'ended');
    page.advance(1000);
    assert.ok(page.invocations.length >= 4, `signals are sent (${page.invocations.length})`);
    assert.ok(page.invocations.every((call) => call.command === 'watch_signal'), 'only the watch_signal command is invoked');
    assert.ok(page.invocations.every((call) => Object.values(call.args.payload).every((value) => typeof value === 'string')), 'payload values are strings');
    assert.ok(page.invocations.every((call) => call.args.payload.frame === 'top' && call.args.payload.mode === 'sequential'));
    assert.deepEqual(page.navigations, [], 'no location assignment for signalling');
    const kinds = new Set(page.signals().map((signal) => signal.kind));
    for (const kind of ['debug', 'progress', 'heartbeat', 'ended']) assert.ok(kinds.has(kind), `${kind} is signalled`);
    assert.equal(page.signals().find((signal) => signal.kind === 'debug').params.stage, 'installed');
  }

  // Progress is rate limited (every 5s at most), also when a child frame floods the top frame.
  {
    const page = makePage();
    page.video(450, 10);
    page.advance(2000);
    const relay = (payload, origin = 'https://commons.ssu.ac.kr') => page.handlers.message.forEach((fn) => fn({ origin, data: { __SSU_WATCH_SIGNAL__: true, kind: 'progress', payload } }));
    const before = page.signals().filter((signal) => signal.kind === 'progress').length;
    for (let index = 0; index < 20; index += 1) {
      relay({ frame: 'child', host: 'commons.ssu.ac.kr', time: String(index), duration: '450', main: '1', paused: '0', rendering: '1' });
      page.advance(100);
    }
    page.advance(6000);
    const childProgress = page.signals().filter((signal) => signal.kind === 'progress' && signal.params.frame === 'child');
    assert.ok(childProgress.length >= 1 && childProgress.length <= 3, `child progress is coalesced and throttled (${childProgress.length})`);
    assert.equal(childProgress[childProgress.length - 1].params.time, '19', 'the newest report wins');
    assert.ok(page.signals().filter((signal) => signal.kind === 'progress').length >= before);
  }

  // The top frame accepts relayed signals from Soongsil origins only, and only well-formed ones.
  {
    const page = makePage();
    const post = (origin, data) => page.handlers.message.forEach((fn) => fn({ origin, data }));
    const good = { __SSU_WATCH_SIGNAL__: true, kind: 'ended', payload: { frame: 'child', time: '449', duration: '450' } };
    post('https://evil.example', good);
    post('https://ssu.ac.kr.evil.example', good);
    post('https://evilssu.ac.kr', good);
    post('null', good);
    post('https://commons.ssu.ac.kr', { ...good, __SSU_WATCH_SIGNAL__: false });
    post('https://commons.ssu.ac.kr', { __SSU_WATCH_SIGNAL__: true, kind: 'ended' });
    post('https://commons.ssu.ac.kr', { ...good, kind: 'eval' });
    page.advance(1000);
    assert.equal(page.signals().filter((signal) => signal.kind === 'ended' || signal.kind === 'eval').length, 0);
    post('https://commons.ssu.ac.kr', good);
    post('https://ssu-toast.commonscdn.com', { ...good, kind: 'stalled' });
    page.advance(1000);
    assert.equal(page.signals().filter((signal) => signal.kind === 'ended').length, 1);
    assert.equal(page.signals().filter((signal) => signal.kind === 'stalled').length, 1);
  }

  // Without Tauri IPC (or when it refuses) the script neither throws nor navigates; it delivers once IPC shows up.
  {
    const page = makePage({ ipc: false });
    page.video(450, 10);
    page.advance(5000);
    assert.deepEqual(page.navigations, []);
    assert.equal(page.invocations.length, 0);
    const sandboxGlobal = page.sandbox;
    sandboxGlobal.__TAURI_INTERNALS__ = { invoke(command, args) { page.invocations.push({ command, args: JSON.parse(JSON.stringify(args)) }); return Promise.resolve(); } };
    page.advance(5000);
    assert.ok(page.invocations.length > 0, 'waiting signals are delivered once IPC exists');
    assert.deepEqual(page.navigations, []);

    const refused = makePage();
    refused.rejectInvoke = true;
    refused.video(450, 10);
    refused.advance(20000);
    assert.ok(refused.invocations.length > 0 && refused.navigations.length === 0, 'a refused call is swallowed');
  }
}

// Hidden LMS items in the 자료 tab, opened inside the app.
{
  const content = await import('../src/lib/content.ts');
  const page = { kind: 'page', module_item_id: 9, url: 'https://canvas.ssu.ac.kr/api/v1/courses/7/pages/week-1', inner_content_type: null, downloadable: false, page_ref: 'week-1' };
  assert.deepEqual(['material', 'page', 'board', 'unknown', 'video', 'assignment', 'section'].map((kind) => content.isLibraryItem({ kind })), [true, true, true, true, false, false, false]);
  assert.equal(content.libraryKindChip(page), '페이지');
  assert.equal(content.libraryKindChip({ kind: 'board' }), '게시판');
  assert.equal(content.libraryKindChip({ kind: 'unknown', inner_content_type: 'zip-archive' }), '기타 · zip-archive');
  assert.equal(content.libraryKindChip({ kind: 'unknown', inner_content_type: null }), '기타');
  assert.equal(content.libraryKindChip({ kind: 'material' }), null);
  // Not selectable for bulk download.
  assert.equal(content.isSelectable(page), false);
  assert.equal(content.isSelectable({ ...page, kind: 'material', downloadable: true }), true);
  // A JSON (API) address is never opened; the module item page is.
  assert.equal(content.itemPageUrl(page, 7), 'https://canvas.ssu.ac.kr/courses/7/modules/items/9');
  assert.equal(content.itemPageUrl({ url: null, module_item_id: 9 }, '7'), 'https://canvas.ssu.ac.kr/courses/7/modules/items/9');
  assert.equal(content.itemPageUrl({ url: 'https://canvas.ssu.ac.kr/courses/7/assignments/3', module_item_id: 9 }, 7), 'https://canvas.ssu.ac.kr/courses/7/assignments/3');
  assert.equal(content.itemPageUrl({ url: '/courses/7/discussion_topics/4', module_item_id: 9 }, 7), 'https://canvas.ssu.ac.kr/courses/7/discussion_topics/4');
  assert.equal(content.itemPageUrl({ url: 'https://evil.example/x', module_item_id: 9 }, 7), 'https://canvas.ssu.ac.kr/courses/7/modules/items/9');
  assert.equal(content.itemPageUrl({ url: null, module_item_id: 'x/../1' }, 7), null);
  assert.equal(content.itemPageUrl({ url: null, module_item_id: null }, 7), null);

  const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
  const list = read('src/components/LectureList.tsx');
  assert.ok(list.includes('앱에서 열기') && list.includes('첨부 파일') && list.includes('libraryKindChip'), 'page rows open in the app and expand to their attachments');
  const tasksUi = read('src/components/TaskList.tsx');
  assert.ok(tasksUi.includes('앱에서 열기') && tasksUi.includes('브라우저로 열기'), 'assignments and quizzes open in the app, with the browser as the secondary action');
  const deadlinesUi = read('src/components/DeadlinesPane.tsx');
  assert.ok(deadlinesUi.includes('브라우저로 열기') && deadlinesUi.includes('앱 안에서 열기'));
  const pages = read('src/hooks/useLmsPages.ts');
  assert.ok(pages.includes('fetchLmsSessionBridge') && pages.includes('openLmsPageWindow') && pages.includes('lmsItemUrl'), 'in-app pages go through the session bridge after the same host check');
  assert.ok(read('src/lib/desktop.ts').includes("invoke<void>('open_lms_page_with_session'"));
  const apiSource = read('src/lib/api.ts');
  assert.ok(apiSource.includes('/pages/${encodeURIComponent(pageRef)}/files') && apiSource.includes('/api/download/canvas-file'));
  const attachmentsUi = read('src/components/PageAttachments.tsx');
  assert.ok(attachmentsUi.includes('받기') && attachmentsUi.includes('첨부 파일을 찾는 중'));
  const { knownErrorText } = await import('../src/lib/errors.ts');
  assert.ok(/[가-힣]/.test(knownErrorText('canvas_file_unavailable')));
}

// The injected watch script must parse (a duplicate declaration silently disables it in the page).
{
  const { execFileSync } = await import('node:child_process');
  execFileSync(process.execPath, ['--check', new URL('../src-tauri/src/assets/watch_assist.js', import.meta.url).pathname]);
  const script = readFileSync(new URL('../src-tauri/src/assets/watch_assist.js', import.meta.url), 'utf8');
  assert.ok(script.includes('.vc-front-screen-play-btn'), 'Commons front-screen play button must be pressed');
}

// The app keeps courses, deadlines and the reminder copy current while it stays open.
{
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  assert.ok(app.includes('AUTO_REFRESH_EVERY_MS = 30 * 60 * 1000') && app.includes("addEventListener('focus'"), 'periodic and on-focus refresh');
  const session = readFileSync(new URL('../src/hooks/useLmsSession.ts', import.meta.url), 'utf8');
  assert.ok(session.includes('refreshQuietly') && session.includes('loadCourses(termId, true, true)'), 'background refresh bypasses LMS caches quietly');
}

// Grades tab (u-SAINT, read only): consent first, errors stay inside the tab, nothing about grades is stored.
{
  const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
  const grades = await import('../src/lib/grades.ts');
  const errors = await import('../src/lib/errors.ts');

  // AC1 / AC2: the button, the exact consent sentence, the choices and the way to turn it off.
  assert.equal(grades.GRADES_LABEL, '성적');
  assert.equal(grades.GRADES_CONSENT_SENTENCE, 'u-SAINT에 같은 계정으로 로그인해서 성적을 읽어 와요');
  assert.equal(grades.GRADES_CONSENT_ACCEPT, '동의하고 성적 보기');
  assert.equal(grades.GRADES_CONSENT_LATER, '나중에');
  assert.ok(grades.GRADES_CONSENT_POINTS.some((point) => point.includes('읽기만 해요')) && grades.GRADES_CONSENT_POINTS.some((point) => point.includes('설정에서 언제든 끌 수 있어요')));
  const topbar = read('src/components/TopBar.tsx');
  assert.ok(topbar.includes('GRADES_LABEL') && topbar.includes('onToggleGrades') && topbar.includes('aria-pressed={gradesOpen}') && topbar.includes('view-toggle'), 'top bar needs the 성적 toggle from the start');
  const pane = read('src/components/GradesPane.tsx');
  assert.ok(pane.includes('GRADES_CONSENT_SENTENCE') && pane.includes('GRADES_CONSENT_ACCEPT') && pane.includes('GRADES_CONSENT_LATER'));
  assert.ok(pane.includes('GRADES_ERROR_TITLE') && pane.includes('role="alert"') && pane.includes('GRADES_EMPTY_TEXT') && pane.includes('aria-expanded'));
  assert.equal(grades.GRADES_ERROR_TITLE, '지금 가져올 수 없어요');
  const settings = read('src/components/SettingsDialog.tsx');
  assert.ok(settings.includes('성적 보기') && settings.includes('끄기') && settings.includes('꺼져 있어요'), 'settings need 성적 보기 > 끄기');
  const app = read('src/App.tsx');
  assert.ok(app.includes("'course' | 'deadlines' | 'grades'") && app.includes('<GradesPane'), 'App switches between course, deadlines and grades');
  // AC1: the grades tab asks nothing of the backend until it is opened. The only caller of open() is
  // this effect, and it runs only while the grades view is shown and nothing was asked yet.
  assert.ok(app.includes("if (view === 'grades' && grades.phase === 'idle') openGrades();"), 'App opens the grades tab only when its view is shown and the hook is idle');
  assert.equal((app.match(/openGrades\(/g) ?? []).length, 1, 'openGrades is called in exactly one place');
  const hook = read('src/hooks/useGrades.ts');
  const hookCode = hook.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  // No effect of the hook may start, open or fetch anything (effects run on mount).
  const effects = hookCode.split('useEffect(').slice(1).map((part) => part.slice(0, part.search(/\n  \}, \[|\n  \}\);/)));
  assert.ok(effects.length >= 1);
  for (const body of effects) {
    assert.ok(!/\b(start|open|retry|grant|refresh|loadOverview|loadClasses|refreshStatus)\(|fetchGrade|fetchGrades/.test(body), 'a useGrades effect must not call the backend');
  }

  // The app identity stays the same (an installed app keeps its data and permissions).
  const tauriJson = JSON.parse(readFileSync(new URL('../src-tauri/tauri.conf.json', import.meta.url), 'utf8'));
  assert.equal(tauriJson.identifier, 'com.local.ssu-lecture-downloader');
  assert.equal(tauriJson.productName, 'SSU Lecture Downloader');

  // AC5: u-SAINT failures stay in the tab; the hook has no way to sign the user out.
  assert.ok(!hookCode.includes('onAuthError') && !hookCode.includes('isAuthenticationError') && !hookCode.includes('expire'), 'useGrades never ends the LMS session');
  const gradesFailures = { login: { code: 'grades_login_rejected' }, consent: { code: 'grades_consent_required' }, down: { code: 'grades_unavailable' } };
  assert.equal(grades.classifyGradesError(gradesFailures.login), 'login_rejected');
  assert.equal(grades.classifyGradesError(gradesFailures.consent), 'consent_required');
  assert.equal(grades.classifyGradesError(gradesFailures.down), 'unavailable');
  for (const code of ['HTTP 404', 'HTTP 403', 'HTTP 401', 'HTTP 503', 'session_expired', 'authentication_failed', 'something_new']) {
    assert.equal(grades.classifyGradesError({ code }), 'unavailable', `${code} must stay a grades-tab error`);
  }
  assert.equal(grades.classifyGradesError(new Error('boom')), 'unavailable');
  assert.equal(grades.classifyGradesError({ code: 'missing_credentials' }), 'credentials_missing');
  assert.ok(grades.gradesFailureText('login_rejected').includes('u-SAINT 웹에서 직접 로그인되는지 확인해 주세요'));
  assert.equal(grades.GRADES_LECTURES_OK, '강의 기능은 그대로 쓸 수 있어요.');
  for (const kind of ['unavailable', 'login_rejected']) {
    assert.ok(grades.gradesFailureText(kind).endsWith(grades.GRADES_LECTURES_OK), `${kind} must carry the reassurance that lecture features keep working`);
  }
  // Missing LMS credentials also hit the lecture features, so that reassurance would be untrue there.
  assert.ok(!grades.gradesFailureText('credentials_missing').includes(grades.GRADES_LECTURES_OK));
  assert.ok(grades.gradesFailureText('credentials_missing').includes('앱에 다시 로그인한 뒤 시도해 주세요'));
  assert.equal(grades.isInvalidConsentVersion({ code: 'invalid_consent_version' }), true);
  assert.equal(grades.isInvalidConsentVersion({ code: 'HTTP 400' }), false);
  assert.ok(hookCode.includes('isInvalidConsentVersion') && hookCode.includes('fetchGradesStatus()'), 'a stale consent version is re-read once');
  assert.ok(pane.includes("'[aria-modal=\"true\"]'") && pane.includes('GRADES_CERTIFICATED_NOTE') && pane.includes('과목</span>'), 'pane: modal-safe focus, certificated note, class count with unit');
  assert.equal(grades.GRADES_CERTIFICATED_NOTE, '졸업증명서 등 공식 서류에 쓰는 평점이에요.');

  // Refresh: the newest request per semester wins; class tables of closed semesters are dropped.
  {
    const sequence = grades.createRequestSequence();
    const first = sequence.begin('2025-ONE');
    const second = sequence.begin('2025-ONE');
    assert.equal(sequence.isLatest('2025-ONE', first), false);
    assert.equal(sequence.isLatest('2025-ONE', second), true);
    const other = sequence.begin('2025-TWO');
    sequence.invalidate(['2025-TWO']);
    assert.equal(sequence.isLatest('2025-TWO', other), false);
    assert.equal(sequence.isLatest('2025-ONE', second), true);
    sequence.clear();
    assert.equal(sequence.isLatest('2025-ONE', second), false);
    const kept = grades.keepKeys({ a: 1, b: 2, c: 3 }, new Set(['b']));
    assert.deepEqual(kept, { b: 2 });
  }
  assert.ok(hookCode.includes("mode === 'refresh'") && hookCode.includes('keepKeys') && hookCode.includes('setRefreshFailed(true)'), 'refresh keeps open tables and only marks the refresh failed');
  for (const code of ['grades_consent_required', 'grades_login_rejected', 'grades_unavailable', 'invalid_consent_version', 'invalid_semester']) {
    assert.ok(/[가-힣]/.test(errors.knownErrorText(code) ?? ''), `${code} needs Korean text`);
  }

  // API client: the frozen contract, and nothing stored or logged.
  const apiSource = read('src/lib/api.ts');
  for (const marker of ["'/api/grades/status'", "'/api/grades/consent'", "method: 'PUT'", 'consent_version: consentVersion', "{ method: 'DELETE' }", '/api/grades?refresh=', '/api/grades/classes?']) {
    assert.ok(apiSource.includes(marker), `api.ts needs ${marker}`);
  }
  for (const path of ['src/hooks/useGrades.ts', 'src/lib/grades.ts', 'src/components/GradesPane.tsx']) {
    const source = read(path);
    assert.ok(!/console\./.test(source), `${path} must not log`);
    assert.ok(!/localStorage|sessionStorage|indexedDB/.test(source), `${path} must not store grades or consent`);
  }

  // Formatting.
  assert.equal(grades.semesterLabel(2025, 'ONE'), '2025년 1학기');
  assert.equal(grades.semesterLabel(2025, 'TWO'), '2025년 2학기');
  assert.equal(grades.semesterLabel(2025, 'SUMMER'), '2025년 하계 계절학기');
  assert.equal(grades.semesterLabel(2025, 'WINTER'), '2025년 동계 계절학기');
  assert.equal(grades.formatGpa(3.854), '3.85');
  assert.equal(grades.formatGpa(null), '-');
  assert.equal(grades.formatCredits(18), '18');
  assert.equal(grades.formatCredits(17.5), '17.5');
  assert.equal(grades.formatCredits(undefined), '-');
  assert.equal(grades.formatRank({ rank: 12, total: 340 }), '12/340');
  assert.equal(grades.formatRank({ rank: 1, total: 0 }), null);
  assert.equal(grades.formatRank({ rank: null, total: 340 }), null);
  assert.equal(grades.semesterStatsText({ grade_points_average: 3.9, earned_credits: 18, semester_rank: { rank: 12, total: 340 } }), '평점 3.90 · 취득 18학점 · 석차 12/340');
  assert.equal(grades.semesterStatsText({ grade_points_average: null, earned_credits: null, semester_rank: null }), '');
  assert.equal(grades.classScoreText({ score: 'P' }), 'P');
  assert.equal(grades.classScoreText({ score: null }), '-');
  const fetched = '2026-10-29T03:00:00Z';
  assert.equal(grades.updatedAgoText(fetched, new Date('2026-10-29T03:00:20Z')), '방금 업데이트');
  assert.equal(grades.updatedAgoText(fetched, new Date('2026-10-29T03:03:10Z')), '3분 전 업데이트');
  assert.equal(grades.updatedAgoText(fetched, new Date('2026-10-29T05:10:00Z')), '2시간 전 업데이트');
  assert.equal(grades.updatedAgoText(fetched, new Date('2026-10-31T05:10:00Z')), '2일 전 업데이트');
  assert.equal(grades.updatedAgoText(null, new Date()), '');
  assert.equal(grades.updatedAgoText('not a date', new Date()), '');
}

{
  // AU-UPDATER-SHELL: the updater is wired but inert (no frontend caller yet); its config, key,
  // endpoint and ACL scoping are pinned here.
  const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), 'utf8');
  const conf = JSON.parse(read('src-tauri/tauri.conf.json'));
  const PUBKEY = 'dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEFFNEQ1MkY3RDJFRjVBRDQKUldUVVd1L1M5MUpOcmprK0xwbEY4aXJQa3ZBcnBlRjh1QUVXVWdZTUcvd3g3Nk1hMFJJTVNsbWEK';
  assert.equal(conf.bundle.createUpdaterArtifacts, true);
  assert.equal(conf.plugins.updater.pubkey, PUBKEY, 'the updater trusts exactly this public key');
  // The pubkey is the key CONTENT (a minisign public key), never a path and never a secret key.
  const pubkeyText = Buffer.from(conf.plugins.updater.pubkey, 'base64').toString('utf8');
  assert.match(pubkeyText, /^untrusted comment: minisign public key: [0-9A-F]{16}\nRW[A-Za-z0-9+/=]+\n?$/);
  assert.deepEqual(conf.plugins.updater.endpoints, ['https://github.com/cafitac/ssu-lecture-downloader/releases/latest/download/latest.json']);
  assert.equal(conf.plugins.updater.windows.installMode, 'passive');
  assert.ok(!('dangerousInsecureTransportProtocol' in conf.plugins.updater), 'updates only over HTTPS');

  // ACL: updater and restart for the main window only, never for the remote watch window.
  const mainCapability = JSON.parse(read('src-tauri/capabilities/default.json'));
  assert.deepEqual(mainCapability.windows, ['main']);
  assert.ok(mainCapability.permissions.includes('updater:default'));
  assert.ok(mainCapability.permissions.includes('process:allow-restart'));
  assert.ok(!mainCapability.permissions.includes('process:default') && !mainCapability.permissions.includes('process:allow-exit'), 'only restart, not exit');
  const remoteCapability = read('src-tauri/capabilities/watch-remote.json');
  assert.ok(!/updater|process/.test(remoteCapability), 'the remote watch window never gets update or process permissions');
  const mainPermissions = read('src-tauri/permissions/main-window-commands.toml');
  for (const command of ['update_install_guard', 'prepare_for_update']) {
    assert.ok(mainPermissions.includes(`commands.allow = ["${command}"]`), `${command} permission`);
    assert.ok(mainPermissions.includes(`  "allow-${command.replaceAll('_', '-')}",`), `${command} in the main-window set`);
  }

  // Plugins: crates, JS packages and desktop-only registration.
  const cargoToml = read('src-tauri/Cargo.toml');
  assert.match(cargoToml, /tauri-plugin-updater = "2\.14"/);
  assert.match(cargoToml, /tauri-plugin-process = "2\.4"/);
  const pkg = JSON.parse(read('package.json'));
  assert.ok(pkg.dependencies['@tauri-apps/plugin-updater'] && pkg.dependencies['@tauri-apps/plugin-process']);
  const libRs = read('src-tauri/src/lib.rs');
  assert.match(libRs, /#\[cfg\(desktop\)\]\s*let builder = builder\s*\.plugin\(tauri_plugin_updater::Builder::new\(\)\.build\(\)\)\s*\.plugin\(tauri_plugin_process::init\(\)\)/);
  assert.ok(['update_install_guard', 'prepare_for_update'].every((name) => libRs.includes(`update::${name}`)));

  // Local and PR builds have no signing key: `tauri:build` opts out of updater artifacts through a
  // config file (cross-platform, no shell quoting); only `tauri:build:release` signs, and it fails
  // fast without the key. CI uses `tauri:build`.
  assert.deepEqual(JSON.parse(read('src-tauri/no-updater.conf.json')), { bundle: { createUpdaterArtifacts: false } });
  assert.equal(pkg.scripts['tauri:build'], 'tauri build --config src-tauri/no-updater.conf.json');
  assert.equal(pkg.scripts['tauri:build:release'], 'node scripts/require-signing-key.mjs && tauri build');
  assert.ok(existsSync('scripts/require-signing-key.mjs'));
  const gate = (env) => spawnSync(process.execPath, ['scripts/require-signing-key.mjs'], { env, encoding: 'utf8' });
  const withoutKey = gate({ PATH: process.env.PATH });
  assert.equal(withoutKey.status, 1);
  assert.match(withoutKey.stderr, /TAURI_SIGNING_PRIVATE_KEY/);
  assert.equal(gate({ PATH: process.env.PATH, TAURI_SIGNING_PRIVATE_KEY: 'x' }).status, 0);
  // tauri-cli 2.12 `tauri build` ignores _PATH, so it must not open the gate.
  assert.equal(gate({ PATH: process.env.PATH, TAURI_SIGNING_PRIVATE_KEY_PATH: '/x' }).status, 1);
  assert.equal(gate({ PATH: process.env.PATH, TAURI_SIGNING_PRIVATE_KEY: '' }).status, 1);
  const ci = read('../.github/workflows/ci.yml');
  assert.ok(ci.includes('run: npm run tauri:build -- --bundles app\n'), 'ci.yml PR build uses the unsigned tauri:build script');
  assert.ok(!ci.includes('run: npm run tauri:build:release'), 'PR CI never runs the signed build');

  // The update plugins and the two update commands are reached through one adapter file only
  // (AU-UPDATE-UI-BASIC added src/lib/updater.ts); nothing else in the frontend source touches them.
  const frontendSource = readTree('src', ['.ts', '.tsx']).filter((file) => file !== join('src', 'lib', 'updater.ts')).map((file) => readFileSync(file, 'utf8')).join('\n');
  assert.ok(!/@tauri-apps\/plugin-(updater|process)|invoke(<[^>]*>)?\(\s*['"](update_install_guard|prepare_for_update)['"]/.test(frontendSource), 'only src/lib/updater.ts may use the update plugins and commands');
}

// ---------------------------------------------------------------------------
// AU-UPDATE-UI-BASIC: the update notice (chip + non-modal panel) and the install flow.
// The plugin and command calls are mocked; nothing here touches a network or a real app.
// ---------------------------------------------------------------------------
{
  for (const file of ['src/lib/appUpdate.ts', 'src/lib/updater.ts', 'src/lib/trackedWork.ts', 'src/hooks/useAppUpdate.ts', 'src/components/UpdateChip.tsx', 'src/styles/update.css']) {
    assert.ok(existsSync(file), `missing ${file}`);
  }
  const au = await import('../src/lib/appUpdate.ts');
  const { isTrackedWorkBusy, anyTransferBusy } = await import('../src/lib/trackedWork.ts');

  // Exact Korean copy.
  assert.equal(au.BUSY_NOTICE, '작업이 끝난 뒤 다시 시도해 주세요');
  assert.equal(au.CHECK_INTERVAL_MS, 6 * 60 * 60 * 1000);
  assert.equal(au.LATER_HIDE_MS, 24 * 60 * 60 * 1000);

  // A fake clock: timers fire only when the test advances time.
  function fakeClock() {
    let now = 0;
    let nextId = 1;
    const timers = new Map();
    const api = {
      setTimeout: (callback, ms) => { const id = nextId++; timers.set(id, { callback, at: now + ms, every: null }); return id; },
      clearTimeout: (id) => { timers.delete(id); },
      setInterval: (callback, ms) => { const id = nextId++; timers.set(id, { callback, at: now + ms, every: ms }); return id; },
      clearInterval: (id) => { timers.delete(id); },
    };
    async function advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        const [id, timer] = due;
        now = timer.at;
        if (timer.every === null) timers.delete(id);
        else timer.at += timer.every;
        timer.callback();
        await flush();
      }
      now = end;
    }
    return { api, advance, pending: () => timers.size };
  }
  const flush = async () => { for (let i = 0; i < 20; i += 1) await Promise.resolve(); };

  const deferred = () => {
    let resolve; let reject;
    const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
    return { promise, resolve, reject };
  };

  /** A mocked plugin API that records every call in order. */
  function mockApi(options = {}) {
    const calls = [];
    const update = {
      version: '1.2.3',
      notes: '- 자막 만들기가 빨라졌어요',
      download: async (onProgress) => {
        calls.push('download');
        onProgress({ downloaded: 0, total: 1000 });
        onProgress({ downloaded: 400, total: 1000 });
        if (options.download) await options.download();
        onProgress({ downloaded: 1000, total: 1000 });
      },
      install: async () => { calls.push('install'); if (options.install) await options.install(); },
      close: async () => { calls.push('close'); },
    };
    const guards = [...(options.guards ?? [])];
    const api = {
      check: async () => {
        calls.push('check');
        if (options.checkError) throw new Error('offline');
        return options.noUpdate ? null : update;
      },
      installGuard: async () => {
        calls.push('guard');
        if (options.guardError) throw new Error('guard unavailable');
        return guards.length > 0 ? guards.shift() : { status: 'allowed' };
      },
      prepare: async () => { calls.push('prepare'); if (options.prepare) await options.prepare(); },
      restartBackend: async () => { calls.push('restart'); if (options.restart) await options.restart(); },
      relaunch: async () => { calls.push('relaunch'); if (options.relaunch) await options.relaunch(); },
    };
    return { api, calls, update };
  }

  function setup(options = {}, busy = () => false) {
    const mock = mockApi(options);
    const clock = fakeClock();
    const logs = [];
    const controller = au.createUpdateController({ api: mock.api, isWorkBusy: busy, timers: clock.api, log: (message) => logs.push(message) });
    return { ...mock, clock, logs, controller };
  }
  const guardRefused = { status: 'refused', reason: 'download_active' };

  // Launch check, then every 6 h; a failed or empty check is only logged and shows nothing.
  {
    const t = setup({ noUpdate: true });
    t.controller.start();
    await flush();
    assert.deepEqual(t.calls, ['check']);
    assert.equal(au.isChipVisible(t.controller.getState()), false);
    await t.clock.advance(au.CHECK_INTERVAL_MS);
    assert.deepEqual(t.calls, ['check', 'check'], 'second check after 6 h');
    t.controller.stop();
    await t.clock.advance(au.CHECK_INTERVAL_MS * 3);
    assert.equal(t.calls.length, 2, 'stop clears the timer');
    assert.equal(t.clock.pending(), 0);

    const failing = setup({ checkError: true });
    failing.controller.start();
    await flush();
    assert.equal(failing.controller.getState().phase, 'idle');
    assert.equal(au.isChipVisible(failing.controller.getState()), false);
    assert.ok(failing.logs.some((line) => line.includes('check failed')), 'a failed check is logged');
    failing.controller.stop();
  }

  // AC8: an update that is found never interrupts. The controller works with no DOM at all, and the
  // notification and sound APIs would throw if anything reached for them; only the state changes.
  {
    const trap = () => { throw new Error('the update notice must not touch this'); };
    const saved = { Notification: globalThis.Notification, Audio: globalThis.Audio, document: globalThis.document, alert: globalThis.alert };
    globalThis.Notification = trap;
    globalThis.Audio = trap;
    globalThis.alert = trap;
    globalThis.document = new Proxy({}, { get: trap });
    try {
      const t = setup();
      t.controller.start();
      await flush();
      const state = t.controller.getState();
      assert.equal(state.phase, 'available');
      assert.equal(au.isChipVisible(state), true);
      assert.equal(au.chipLabel(state), '새 버전 1.2.3');
      assert.equal(state.notes, '- 자막 만들기가 빨라졌어요');
      assert.deepEqual(t.calls, ['check'], 'finding an update installs, downloads and stops nothing');
      assert.equal(state.notice, null);
      t.controller.stop();
    } finally {
      for (const [key, value] of Object.entries(saved)) {
        if (value === undefined) delete globalThis[key];
        else globalThis[key] = value;
      }
    }
  }

  // 나중에: the chip hides for 24 h; the 6 h checks inside the window are skipped; then it asks again.
  {
    const t = setup();
    t.controller.start();
    await flush();
    t.controller.later();
    assert.equal(au.isChipVisible(t.controller.getState()), false);
    await t.clock.advance(au.LATER_HIDE_MS - 1000);
    assert.equal(t.calls.filter((call) => call === 'check').length, 1, 'ticks inside the 24 h window do not check');
    assert.equal(au.isChipVisible(t.controller.getState()), false);
    await t.clock.advance(1000);
    assert.equal(t.calls.filter((call) => call === 'check').length, 2, 'checked again when the 24 h are over');
    assert.equal(au.isChipVisible(t.controller.getState()), true);
    t.controller.stop();
  }

  // Busy: tracked UI work or a refusing guard keeps the flow in the panel and starts nothing.
  {
    let busy = true;
    const t = setup({}, () => busy);
    t.controller.start();
    await flush();
    await t.controller.update();
    assert.equal(t.controller.getState().phase, 'available');
    assert.equal(t.controller.getState().notice, '작업이 끝난 뒤 다시 시도해 주세요');
    assert.deepEqual(t.calls, ['check'], 'tracked work blocks before any download or guard call');
    busy = false;
    await t.controller.update();
    assert.equal(t.controller.getState().notice, null, 'the message goes away on the next try');
    assert.deepEqual(t.calls, ['check', 'guard', 'download', 'guard', 'prepare', 'install', 'relaunch']);
    t.controller.stop();

    const refused = setup({ guards: [guardRefused] });
    refused.controller.start();
    await flush();
    await refused.controller.update();
    assert.equal(refused.controller.getState().notice, '작업이 끝난 뒤 다시 시도해 주세요');
    assert.deepEqual(refused.calls, ['check', 'guard'], 'a refusing guard stops before the download');
    refused.controller.stop();
  }

  // Happy path: order, progress, relaunch.
  {
    const gate = deferred();
    const t = setup({ download: () => gate.promise });
    t.controller.start();
    await flush();
    const seen = [];
    t.controller.subscribe(() => seen.push(t.controller.getState().phase));
    const running = t.controller.update();
    await flush();
    let state = t.controller.getState();
    assert.equal(state.phase, 'downloading');
    assert.equal(au.progressPercent(state.progress), 40);
    assert.equal(au.progressText(state.progress), '내려받는 중이에요 40%');
    assert.equal(au.chipLabel(state), '새 버전 1.2.3 · 받는 중 40%');
    // A second click while the flow is in flight does nothing.
    await t.controller.update();
    assert.equal(t.calls.filter((call) => call === 'download').length, 1);
    gate.resolve();
    await running;
    assert.deepEqual(t.calls, ['check', 'guard', 'download', 'guard', 'prepare', 'install', 'relaunch']);
    assert.equal(t.controller.getState().phase, 'relaunching');
    assert.ok(seen.indexOf('guarding') < seen.indexOf('downloading') && seen.indexOf('downloading') < seen.indexOf('installing') && seen.indexOf('installing') < seen.indexOf('relaunching'));
    assert.ok(!t.calls.includes('restart'), 'a successful install never restarts the backend');
    t.controller.stop();
  }

  // The guard is checked again after the download (work may have started meanwhile).
  {
    const late = setup({ guards: [{ status: 'allowed' }, guardRefused] });
    late.controller.start();
    await flush();
    await late.controller.update();
    assert.deepEqual(late.calls, ['check', 'guard', 'download', 'guard', 'close', 'check'], 'refused at the re-check: bytes dropped (close), checked again, no prepare, no install');
    assert.equal(late.controller.getState().phase, 'available');
    assert.equal(late.controller.getState().notice, '작업이 끝난 뒤 다시 시도해 주세요');
    assert.equal(late.controller.getState().version, '1.2.3');
    await late.controller.update();
    assert.equal(late.calls.filter((call) => call === 'download').length, 2, 'the next click downloads again');
    assert.equal(late.controller.getState().phase, 'relaunching');
    late.controller.stop();

    // When the check after the refusal finds nothing, the next click asks again before it downloads.
    let lost = false;
    const gone = setup({ guards: [{ status: 'allowed' }, guardRefused] });
    const realCheck = gone.api.check;
    gone.api.check = async () => (lost ? null : realCheck());
    gone.controller.start();
    await flush();
    lost = true;
    await gone.controller.update();
    assert.equal(gone.controller.getState().phase, 'available');
    assert.equal(gone.controller.getState().notice, '작업이 끝난 뒤 다시 시도해 주세요');
    lost = false;
    await gone.controller.update();
    assert.equal(gone.controller.getState().phase, 'relaunching', 'asks again, then installs');
    gone.controller.stop();

    let busy = false;
    const uiBusy = setup({ download: async () => { busy = true; } }, () => busy);
    uiBusy.controller.start();
    await flush();
    await uiBusy.controller.update();
    assert.ok(!uiBusy.calls.includes('prepare') && !uiBusy.calls.includes('install'));
    assert.deepEqual(uiBusy.calls.slice(-2), ['close', 'check'], 'tracked work seen at the re-check also drops the bytes');
    assert.equal(uiBusy.controller.getState().notice, '작업이 끝난 뒤 다시 시도해 주세요');
    uiBusy.controller.stop();
  }

  // Download failure: Korean error with retry, the old app is untouched, no restart is needed.
  {
    let fail = true;
    const t = setup({ download: async () => { if (fail) throw new Error('network'); } });
    t.controller.start();
    await flush();
    await t.controller.update();
    const state = t.controller.getState();
    assert.equal(state.phase, 'failed');
    assert.equal(state.error, '업데이트를 내려받지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.');
    assert.equal(state.canRetry, true);
    assert.ok(!t.calls.includes('prepare') && !t.calls.includes('restart'));
    fail = false;
    await t.controller.update();
    assert.equal(t.controller.getState().phase, 'relaunching', '다시 시도 runs the flow again');
    t.controller.stop();
  }

  // prepare_for_update failures. Refused: back to the waiting message. backend_starting / unavailable:
  // nothing was stopped, so no restart. An unknown failure may have stopped it: restart to be safe.
  {
    const refused = setup({ prepare: async () => { throw { code: 'refused', reason: 'watch_active' }; } });
    refused.controller.start();
    await flush();
    await refused.controller.update();
    assert.equal(refused.controller.getState().phase, 'available');
    assert.equal(refused.controller.getState().notice, '작업이 끝난 뒤 다시 시도해 주세요');
    assert.ok(!refused.calls.includes('install') && !refused.calls.includes('restart'));
    assert.deepEqual(refused.calls.slice(-3), ['prepare', 'close', 'check'], 'a refusing prepare drops the bytes as well');
    refused.controller.stop();

    const unavailable = setup({ prepare: async () => { throw { code: 'backend_unavailable' }; } });
    unavailable.controller.start();
    await flush();
    await unavailable.controller.update();
    assert.equal(unavailable.controller.getState().phase, 'failed');
    assert.equal(unavailable.controller.getState().error, '업데이트를 준비하지 못했어요. 다시 시도해 주세요.');
    assert.equal(unavailable.controller.getState().canRetry, true);
    assert.ok(!unavailable.calls.includes('install') && !unavailable.calls.includes('restart'), 'nothing was stopped, so no restart');
    unavailable.controller.stop();

    const starting = setup({ prepare: async () => { throw { code: 'backend_starting' }; } });
    starting.controller.start();
    await flush();
    await starting.controller.update();
    assert.equal(starting.controller.getState().error, '앱이 아직 시작하는 중이에요. 잠시 후 다시 시도해 주세요.');
    assert.ok(!starting.calls.includes('install') && !starting.calls.includes('restart'));
    starting.controller.stop();

    const unknown = setup({ prepare: async () => { throw new Error('ipc'); } });
    unknown.controller.start();
    await flush();
    await unknown.controller.update();
    assert.equal(unknown.controller.getState().error, '업데이트를 준비하지 못했어요. 다시 시도해 주세요.');
    assert.deepEqual(unknown.calls.slice(-2), ['prepare', 'restart']);
    assert.ok(!unknown.calls.includes('install'));
    assert.equal(unknown.controller.getState().canRetry, true);
    unknown.controller.stop();

    // ... and when that restart does not bring the backend back, the copy asks to reopen the app, with no retry.
    const unknownDead = setup({ prepare: async () => { throw new Error('ipc'); }, restart: async () => { throw new Error('no'); } });
    unknownDead.controller.start();
    await flush();
    await unknownDead.controller.update();
    assert.equal(unknownDead.controller.getState().error, au.INSTALL_FAILED_RESTART);
    assert.equal(unknownDead.controller.getState().canRetry, false);
    unknownDead.controller.stop();
  }

  // Install failure: no restart is offered or called between prepare Ok and the install result; a failure
  // then restarts the backend and shows a Korean error with retry.
  {
    const installGate = deferred();
    let failInstall = true;
    const t = setup({ install: async () => { if (failInstall) await installGate.promise; } });
    t.controller.start();
    await flush();
    const running = t.controller.update();
    await flush();
    assert.equal(t.controller.getState().phase, 'installing');
    assert.deepEqual(t.calls.slice(-2), ['prepare', 'install']);
    assert.ok(!t.calls.includes('restart'), 'no backend restart while the install is pending');
    assert.equal(t.controller.getState().canRetry, false, 'nothing to retry or restart yet');
    await t.controller.update();
    await t.controller.update();
    assert.equal(t.calls.filter((call) => call === 'prepare').length, 1);
    installGate.reject(new Error('swap failed'));
    await running;
    assert.deepEqual(t.calls.slice(-3), ['prepare', 'install', 'restart'], 'restart comes after the install result');
    let state = t.controller.getState();
    assert.equal(state.phase, 'failed');
    assert.equal(state.error, '업데이트를 설치하지 못했어요. 지금 쓰던 버전은 그대로 쓸 수 있어요. 다시 시도해 주세요.');
    assert.equal(state.canRetry, true);
    failInstall = false;
    await t.controller.update();
    state = t.controller.getState();
    assert.equal(state.phase, 'relaunching', 'retry installs and relaunches');
    assert.equal(t.calls.at(-1), 'relaunch');
    t.controller.stop();

    // Every recovery attempt is announced once, successful or not (the app re-reads its backend status).
    let restored = 0;
    const announced = au.createUpdateController({ ...{ api: setup({ install: async () => { throw new Error('x'); } }).api, isWorkBusy: () => false, timers: fakeClock().api }, onRecoveryAttempted: () => { restored += 1; } });
    announced.start();
    await flush();
    await announced.update();
    assert.equal(restored, 1);
    announced.stop();
    const silent = au.createUpdateController({ api: setup({ install: async () => { throw new Error('x'); }, restart: async () => { throw new Error('no'); } }).api, isWorkBusy: () => false, timers: fakeClock().api, onRecoveryAttempted: () => { restored += 1; } });
    silent.start();
    await flush();
    await silent.update();
    assert.equal(restored, 2, 'a failed recovery is announced too, so the app shows the real backend state');
    silent.stop();

    // The restart itself failing asks for a manual reopen.
    const both = setup({ install: async () => { throw new Error('swap failed'); }, restart: async () => { throw new Error('no'); } });
    both.controller.start();
    await flush();
    await both.controller.update();
    assert.equal(both.controller.getState().error, '업데이트를 설치하지 못했어요. 앱을 한 번 닫았다가 다시 열어 주세요.');
    // The copy says to close and reopen, so no "다시 시도" is offered next to it (and the panel shows none).
    assert.equal(both.controller.getState().canRetry, false);
    assert.equal(au.chipLabel(both.controller.getState()), '새 버전 1.2.3 · 앱을 다시 열어 주세요');
    const callsBefore = both.calls.length;
    await both.controller.update();
    both.controller.later();
    assert.equal(both.calls.length, callsBefore, 'no retry flow starts');
    assert.equal(both.controller.getState().phase, 'failed');
    both.controller.stop();
  }

  // The install worked but the relaunch did not: say so, no retry, never restart the (stopped) backend.
  {
    const t = setup({ relaunch: async () => { throw new Error('exit'); } });
    t.controller.start();
    await flush();
    await t.controller.update();
    const state = t.controller.getState();
    assert.equal(state.phase, 'failed');
    assert.equal(state.error, '업데이트는 설치됐어요. 앱을 닫았다가 다시 열면 새 버전으로 시작해요.');
    assert.equal(state.canRetry, false);
    assert.ok(!t.calls.includes('restart'));
    const before = t.calls.length;
    await t.controller.update();
    assert.equal(t.calls.length, before, 'no retry offered');
    t.controller.stop();
  }

  // Tracked work helper.
  {
    const idle = { downloadActive: false, componentsInstalling: false, attachmentSaving: false };
    assert.equal(isTrackedWorkBusy(idle), false);
    assert.equal(isTrackedWorkBusy({ ...idle, downloadActive: true }), true);
    assert.equal(isTrackedWorkBusy({ ...idle, componentsInstalling: true }), true);
    assert.equal(isTrackedWorkBusy({ ...idle, attachmentSaving: true }), true);
    const { busyAfterBackendStatus } = await import('../src/lib/trackedWork.ts');
    assert.equal(busyAfterBackendStatus(true, 'ready'), true, 'kept while the backend is ready');
    assert.equal(busyAfterBackendStatus(true, 'failed'), false, 'no job survives a dead sidecar');
    assert.equal(busyAfterBackendStatus(true, 'starting'), false);
    assert.equal(busyAfterBackendStatus(false, 'ready'), false);
    assert.equal(anyTransferBusy({}), false);
    assert.equal(anyTransferBusy({ a: { transfers: { f1: 'done', f2: 'error' } } }), false);
    assert.equal(anyTransferBusy({ a: { transfers: { f1: 'done' } }, b: { transfers: { f2: 'busy' } } }), true);
  }

  // The adapter against a mocked Tauri bridge: which plugin calls and commands are made, in which form.
  {
    const bridgeCalls = [];
    const savedWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
    const savedInternals = Object.getOwnPropertyDescriptor(globalThis, '__TAURI_INTERNALS__');
    globalThis.window = globalThis;
    globalThis.__TAURI_INTERNALS__ = {
      transformCallback: (callback) => { const id = 9000 + bridgeCalls.length; globalThis[`_${id}`] = callback; return id; },
      unregisterCallback() {},
      invoke: async (command, args) => {
        bridgeCalls.push(command);
        if (command === 'plugin:updater|check') return { rid: 7, currentVersion: '0.1.0', version: '0.2.0', body: '  ', rawJson: {} };
        if (command === 'plugin:updater|download') {
          args.onEvent.onmessage({ event: 'Started', data: { contentLength: 200 } });
          args.onEvent.onmessage({ event: 'Progress', data: { chunkLength: 50 } });
          args.onEvent.onmessage({ event: 'Progress', data: { chunkLength: 100 } });
          args.onEvent.onmessage({ event: 'Finished' });
          return undefined;
        }
        if (command === 'update_install_guard') return { status: 'allowed' };
        if (command === 'backend_supervision_status') return { state: 'ready', mode: 'managed', error: null, log_path: null };
        return null;
      },
    };
    try {
      const { tauriUpdateApi } = await import('../src/lib/updater.ts');
      const found = await tauriUpdateApi.check();
      assert.equal(found.version, '0.2.0');
      assert.equal(found.notes, null, 'blank release notes count as none');
      const steps = [];
      await found.download((step) => steps.push(step));
      assert.deepEqual(steps.at(-1), { downloaded: 150, total: 200 });
      await found.install();
      assert.deepEqual(await tauriUpdateApi.installGuard(), { status: 'allowed' });
      await tauriUpdateApi.prepare();
      await tauriUpdateApi.restartBackend();
      await tauriUpdateApi.relaunch();
      await found.close();
      assert.deepEqual(
        [...new Set(bridgeCalls)],
        ['plugin:updater|check', 'plugin:updater|download', 'plugin:updater|install', 'update_install_guard', 'prepare_for_update', 'backend_restart', 'backend_supervision_status', 'plugin:process|restart', 'plugin:resources|close'],
      );
    } finally {
      if (savedWindow) Object.defineProperty(globalThis, 'window', savedWindow);
      else delete globalThis.window;
      if (savedInternals) Object.defineProperty(globalThis, '__TAURI_INTERNALS__', savedInternals);
      else delete globalThis.__TAURI_INTERNALS__;
    }
  }

  // The component, rendered with real React on the server. The notice is a chip plus an opt-in panel:
  // no dialog, overlay, modal flag, autofocus or live alert; the other top-bar controls are not touched.
  {
    const { createServer } = await import('vite');
    const React = (await import('react')).default;
    const { renderToStaticMarkup } = await import('react-dom/server');
    const server = await createServer({ root: process.cwd(), server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom', logLevel: 'error', optimizeDeps: { noDiscovery: true } });
    try {
      const { UpdateChipView, UpdateChip } = await server.ssrLoadModule('/src/components/UpdateChip.tsx');
      const { BackendScreen } = await server.ssrLoadModule('/src/components/Screens.tsx');
      const failedStatus = { state: 'failed', mode: 'failed', error: 'x', log_path: null };
      const normal = renderToStaticMarkup(React.createElement(BackendScreen, { status: failedStatus, onRetry() {} }));
      assert.ok(normal.includes('다시 시도</button>'), 'the backend screen offers its retry');
      const blocked = renderToStaticMarkup(React.createElement(BackendScreen, { status: failedStatus, onRetry() {}, retryBlockedText: '업데이트하는 중이에요' }));
      assert.ok(blocked.includes('업데이트하는 중이에요') && !blocked.includes('다시 시도</button>'), 'while an update installs the retry is replaced by a status line');
      // 진단 파일 저장: the failure screen offers it as a quiet secondary action, the Settings row as its own section.
      assert.ok(normal.includes('class="btn btn-sm btn-ghost">진단 파일 저장</button>') && normal.indexOf('다시 시도</button>') < normal.indexOf('진단 파일 저장'), 'the failure screen carries 진단 파일 저장 after the retry');
      assert.ok(blocked.includes('진단 파일 저장'), 'the diagnostics action stays while an update installs');
      const { DiagnosticsButtonView } = await server.ssrLoadModule('/src/components/Screens.tsx');
      const { DiagnosticsSectionView } = await server.ssrLoadModule('/src/components/Diagnostics.tsx');
      const savedOutcome = { ok: true, path: '/Users/x/Desktop/ssu-진단-20261030-1105.zip' };
      const failedOutcome = { ok: false, text: '진단 파일을 쓰지 못했어요. (disk full)' };
      const sectionIdle = renderToStaticMarkup(React.createElement(DiagnosticsSectionView, { busy: false, outcome: null, onSave() {}, onReveal() {} }));
      assert.ok(sectionIdle.startsWith('<section class="setting"><h3>문제 보내기</h3>') && sectionIdle.includes('>진단 파일 저장</button>'), 'Settings row: 문제 보내기 with the save button');
      assert.ok(!/disabled|role="status"|role="alert"|폴더에서 보기/.test(sectionIdle), 'idle: enabled button, no result, no reveal');
      const sectionBusy = renderToStaticMarkup(React.createElement(DiagnosticsSectionView, { busy: true, outcome: null, onSave() {}, onReveal() {} }));
      assert.ok(/<button type="button" class="btn btn-sm" disabled="">.*저장하는 중…<\/button>/.test(sectionBusy), 'the button is disabled while saving');
      const sectionSaved = renderToStaticMarkup(React.createElement(DiagnosticsSectionView, { busy: false, outcome: savedOutcome, onSave() {}, onReveal() {} }));
      assert.ok(sectionSaved.includes('<p class="small" role="status">진단 파일을 저장했어요. 이 파일을 전달해 주세요.</p>') && sectionSaved.includes('폴더에서 보기'), 'success text and a Finder reveal');
      const sectionFailed = renderToStaticMarkup(React.createElement(DiagnosticsSectionView, { busy: false, outcome: failedOutcome, onSave() {}, onReveal() {} }));
      assert.ok(sectionFailed.includes('role="alert">진단 파일을 쓰지 못했어요. (disk full)</p>') && !sectionFailed.includes('폴더에서 보기'), 'failure is a plain Korean alert');
      // The real SettingsDialog: the row exists in the desktop app (Tauri bridge present) and not in a plain browser.
      {
        const { SettingsDialog } = await server.ssrLoadModule('/src/components/SettingsDialog.tsx');
        const media = { missingComponents: [], ffmpegDependency: { available: true }, whisperStatus: { available: true }, subtitlesViaRemote: false, subtitlesUnsupportedReason: null, canInstall: false, autoInstallAvailable: true, autoInstallUnavailableReason: null, isInstalling: false, install() {}, load() {}, remote: { status: null, busy: null, outcome: null, save: async () => true, check: async () => {} } };
        const grades = { consent: 'off', refreshStatus: async () => {}, revoke: async () => null };
        const settings = () => renderToStaticMarkup(React.createElement(SettingsDialog, { downloadDir: '/Users/x/Downloads', busy: false, workBusy: false, media, debugEvents: [], grades, watchMode: 'chrome', chromeAvailable: true, queueActive: false, onWatchModeChange() {}, onPickFolder() {}, onOpenFolder() {}, onSignOut: async () => null, onClose() {} }));
        const savedWindow = Object.getOwnPropertyDescriptor(globalThis, 'window');
        const savedInternals = Object.getOwnPropertyDescriptor(globalThis, '__TAURI_INTERNALS__');
        try {
          delete globalThis.window;
          delete globalThis.__TAURI_INTERNALS__;
          const browser = settings();
          assert.ok(browser.includes('저장 폴더') && !browser.includes('문제 보내기') && !browser.includes('진단 파일 저장'), 'no diagnostics row in a plain browser');
          globalThis.window = globalThis;
          globalThis.__TAURI_INTERNALS__ = {};
          const desktopHtml = settings();
          assert.ok(desktopHtml.includes('<h3>문제 보내기</h3>') && desktopHtml.includes('>진단 파일 저장</button>'), 'the desktop app shows the 문제 보내기 row');
          assert.ok(desktopHtml.indexOf('기한 알림') < desktopHtml.indexOf('문제 보내기'), 'after the reminders row');
        } finally {
          if (savedWindow) Object.defineProperty(globalThis, 'window', savedWindow);
          else delete globalThis.window;
          if (savedInternals) Object.defineProperty(globalThis, '__TAURI_INTERNALS__', savedInternals);
          else delete globalThis.__TAURI_INTERNALS__;
        }
      }
      const screenSaved = renderToStaticMarkup(React.createElement(DiagnosticsButtonView, { busy: false, outcome: savedOutcome, onSave() {}, onReveal() {} }));
      assert.ok(screenSaved.includes('진단 파일을 저장했어요. 이 파일을 전달해 주세요.') && screenSaved.includes('폴더에서 보기'));
      assert.ok(/disabled=""/.test(renderToStaticMarkup(React.createElement(DiagnosticsButtonView, { busy: true, outcome: null, onSave() {}, onReveal() {} }))), 'the failure-screen button is disabled while saving');
      const base = { phase: 'available', version: '1.2.3', notes: '- 빨라졌어요', hidden: false, progress: null, notice: null, error: null, canRetry: false };
      const render = (state, open) => renderToStaticMarkup(React.createElement(UpdateChipView, { state, open, onToggle() {}, onUpdate() {}, onLater() {} }));
      const noInterruption = (html) => {
        assert.ok(!/role="(alertdialog|dialog|alert)"|aria-modal|autofocus|<dialog|class="[^"]*(overlay|backdrop|toast)/i.test(html), `interrupting markup in: ${html}`);
        assert.ok(!/\sdisabled(=|\s|>)/.test(html.replace(/<button type="button" class="btn btn-sm update-go" disabled/g, '')), 'the notice disables no other control');
      };

      // Closed: one small button, nothing else.
      const closed = render(base, false);
      assert.match(closed, /^<div class="update-anchor"><button[^>]*class="update-chip"[^>]*aria-expanded="false"[^>]*>.*새 버전 1\.2\.3<\/button><\/div>$/);
      noInterruption(closed);
      const heldView = (state, busyHeld) => renderToStaticMarkup(React.createElement(UpdateChipView, { state, open: true, busyHeld, onToggle() {}, onUpdate() {}, onLater() {} }));
      assert.ok(heldView(base, true).includes('앞에서 하던 작업이 끝났는지 확인하려면 다시 로그인해 주세요'), 'the login-screen panel says that earlier work may still run');
      assert.ok(!heldView(base, false).includes('앞에서 하던 작업'), 'no hint when nothing is held');
      const cornerHtml = renderToStaticMarkup(React.createElement(UpdateChip, { update: { state: base, update() {}, later() {} }, corner: true }));
      assert.match(cornerHtml, /^<div class="update-host update-corner"><div class="update-anchor"><button[^>]*class="update-chip"/);
      noInterruption(cornerHtml);
      assert.equal(renderToStaticMarkup(React.createElement(UpdateChip, { update: { state: { ...base, hidden: true }, update() {}, later() {} }, corner: true })), '<div class="update-host update-corner"></div>');
      assert.equal(render({ ...base, hidden: true }, false), '', '나중에 hides the chip');
      assert.equal(render({ ...base, phase: 'idle', version: null }, false), '', 'no release: no chip');
      assert.equal(render({ ...base, phase: 'checking', version: null }, false), '', 'a check in progress shows nothing');

      // Open panel: notes and both buttons, a plain section.
      const opened = render(base, true);
      noInterruption(opened);
      assert.match(opened, /<section class="update-panel"/);
      assert.ok(opened.includes('- 빨라졌어요') && opened.includes('지금 업데이트') && opened.includes('나중에'));
      assert.ok(render({ ...base, notes: null }, true).includes('변경 내용이 따로 적혀 있지 않아요.'));

      // Busy message, progress, error with retry.
      const busy = render({ ...base, notice: au.BUSY_NOTICE }, true);
      assert.ok(busy.includes('작업이 끝난 뒤 다시 시도해 주세요') && busy.includes('지금 업데이트'));
      noInterruption(busy);
      const downloading = render({ ...base, phase: 'downloading', progress: { downloaded: 420, total: 1000 } }, true);
      assert.ok(downloading.includes('내려받는 중이에요 42%') && downloading.includes('aria-valuenow="42"') && downloading.includes('새 버전 1.2.3 · 받는 중 42%'));
      assert.ok(!downloading.includes('지금 업데이트') && !downloading.includes('나중에'), 'no buttons while the install runs');
      noInterruption(downloading);
      const installing = render({ ...base, phase: 'installing' }, true);
      assert.ok(installing.includes('설치하는 중이에요') && !installing.includes('다시 시도') && !installing.includes('재시작') && !installing.includes('다시 켜기'), 'no restart offered while installing');
      const failed = render({ ...base, phase: 'failed', error: au.INSTALL_FAILED, canRetry: true }, true);
      assert.ok(failed.includes('다시 시도') && failed.includes(au.INSTALL_FAILED));
      const stuck = render({ ...base, phase: 'failed', error: au.RELAUNCH_FAILED, canRetry: false }, true);
      assert.ok(stuck.includes(au.RELAUNCH_FAILED) && !stuck.includes('다시 시도</button>'));
      noInterruption(failed);
    } finally {
      await server.close();
    }

    // Source rules for the interactive part (events, focus, sound, notifications).
    const chipSource = readFileSync('src/components/UpdateChip.tsx', 'utf8');
    const hookSource = readFileSync('src/hooks/useAppUpdate.ts', 'utf8');
    for (const [name, source] of [['UpdateChip.tsx', chipSource], ['useAppUpdate.ts', hookSource], ['appUpdate.ts', readFileSync('src/lib/appUpdate.ts', 'utf8')]]) {
      assert.ok(!/autoFocus|showModal|createPortal|aria-modal|role="(dialog|alertdialog|alert)"|Notification|new Audio|AudioContext|alert\(|confirm\(|setFocus|sendNotification|preventDefault|stopPropagation|focusTrap|\.blur\(/.test(source), `${name} must not interrupt`);
    }
    assert.equal((chipSource.match(/\.focus\(/g) ?? []).length, 1, 'only Esc inside the panel returns focus to the chip');
    assert.ok(/event\.key !== 'Escape'/.test(chipSource) && /addEventListener\('mousedown'/.test(chipSource), 'Esc and an outside click close the panel');
    assert.ok(/if \(!open\) return undefined;/.test(chipSource), 'the listeners exist only while the panel is open');
    assert.ok(/<UpdateChip update=\{update\} \/>/.test(readFileSync('src/components/TopBar.tsx', 'utf8')), 'TopBar mounts the chip');
    // One controller for the whole app, created in App above every early return: the check needs
    // neither the backend nor a session, and "나중에" survives sign-out and sign-in.
    const appSource = readFileSync('src/App.tsx', 'utf8');
    assert.equal(readTree('src', ['.ts', '.tsx']).filter((file) => readFileSync(file, 'utf8').includes('useAppUpdate(')).length, 2, 'the hook is defined once and called once');
    const appFn = appSource.slice(appSource.indexOf('export function App()'));
    assert.ok(appFn.indexOf('useAppUpdate(') > 0 && appFn.indexOf('useAppUpdate(') < appFn.indexOf('BackendScreen') && appFn.indexOf('useAppUpdate(') < appFn.indexOf('SessionGate'), 'the controller is created before the backend and session branches');
    assert.equal((appSource.match(/<UpdateChip update=\{update\} corner( busyHeld=\{workBusy\})? \/>/g) ?? []).length, 2, 'corner chip on the backend-failure screen and on the login screen');
    // The workspace reports tracked work up and never clears it on unmount (a job may still run behind the login screen);
    // sign-out waits for tracked work, like it does for downloads.
    assert.ok(/onWorkBusyChange\(workBusy\)/.test(appSource) && !/onWorkBusyChange\(false\)/.test(appSource), 'unmount keeps the last busy value');
    assert.ok(/workBusy=\{workBusy\}/.test(appSource), 'the settings dialog gets the tracked-work flag');
    assert.ok(/disabled=\{signingOut \|\| busy \|\| workBusy\}/.test(readFileSync('src/components/SettingsDialog.tsx', 'utf8')), 'sign-out waits for component installs and attachment saves');
    // While an update installs, the backend screen's retry would restart the backend under the installer.
    assert.ok(/retryBlockedText=\{installing \? '업데이트하는 중이에요' : undefined\}/.test(appSource) && /isInstalling\(update\.state\.phase\)/.test(appSource), 'backend retry replaced while installing');
    assert.ok(/useAppUpdate\(workBusy, \{ onRecoveryAttempted: refresh \}\)/.test(appSource), 'every recovery attempt makes the app re-read its status');
    assert.ok(/setWorkBusy\(\(held\) => busyAfterBackendStatus\(held, status\.state\)\)/.test(appSource), 'a held busy flag is dropped when the backend is not ready');
    assert.ok(/<UpdateChip update=\{update\} corner busyHeld=\{workBusy\} \/>/.test(appSource), 'the login-screen chip explains a held busy flag');
    assert.ok(/const refresh = useCallback\(\(\) => setAttempt/.test(readFileSync('src/hooks/useBackendStatus.ts', 'utf8')));
    const updateCss = readFileSync('src/styles/update.css', 'utf8');
    assert.ok(!/inset:\s*0|backdrop/.test(updateCss), 'no full-screen layer');
    assert.deepEqual(updateCss.match(/[^}]*position:\s*fixed[^}]*/g)?.map((rule) => rule.trim().split('{')[0].trim()), ['.update-host.update-corner'], 'only the small corner chip is fixed');
    assert.ok(readFileSync('src/styles.css', 'utf8').includes("@import './styles/update.css';"));
  }
}

// ---------------------------------------------------------------------------
// AU-UPDATE-UI-BASIC repair: backend recovery after a failed install, and the hook lifecycle.
// ---------------------------------------------------------------------------
{
  const tauriBridge = (handlers) => {
    const calls = [];
    const saved = { window: Object.getOwnPropertyDescriptor(globalThis, 'window'), internals: Object.getOwnPropertyDescriptor(globalThis, '__TAURI_INTERNALS__') };
    globalThis.window = globalThis;
    globalThis.__TAURI_INTERNALS__ = {
      transformCallback: () => 1,
      unregisterCallback() {},
      invoke: async (command, args) => {
        calls.push(command);
        return handlers[command] ? handlers[command](args) : null;
      },
    };
    const restore = () => {
      if (saved.window) Object.defineProperty(globalThis, 'window', saved.window);
      else delete globalThis.window;
      if (saved.internals) Object.defineProperty(globalThis, '__TAURI_INTERNALS__', saved.internals);
      else delete globalThis.__TAURI_INTERNALS__;
    };
    return { calls, restore };
  };
  const desktop = await import('../src/lib/desktop.ts');
  const { recoverBackend, waitForBackendSettled } = await import('../src/lib/updater.ts');

  // 진단 파일 저장: save dialog with the dated default name, then the command with the chosen path.
  {
    assert.equal(desktop.diagnosticsFileName(new Date(2026, 9, 30, 9, 5)), 'ssu-진단-20261030-0905.zip');
    assert.equal(desktop.diagnosticsFileName(new Date(2026, 0, 2, 23, 59)), 'ssu-진단-20260102-2359.zip');
    let dialogOptions;
    let commandArgs;
    const picked = tauriBridge({
      'plugin:dialog|save': (args) => { dialogOptions = args.options; return '/Users/x/Desktop/diag.zip'; },
      save_diagnostics: (args) => { commandArgs = args; return { path: args.destination, files: ['backend.log', 'info.json'] }; },
    });
    try {
      const saved = await desktop.saveDiagnosticsFile(new Date(2026, 9, 30, 11, 5));
      assert.deepEqual(saved, { path: '/Users/x/Desktop/diag.zip', files: ['backend.log', 'info.json'] });
      assert.equal(dialogOptions.defaultPath, 'ssu-진단-20261030-1105.zip');
      assert.deepEqual(commandArgs, { destination: '/Users/x/Desktop/diag.zip' });
      assert.deepEqual(picked.calls, ['plugin:dialog|save', 'save_diagnostics']);
    } finally { picked.restore(); }

    // Cancelling the dialog writes nothing and is not an error.
    const cancelled = tauriBridge({ 'plugin:dialog|save': () => null });
    try {
      assert.equal(await desktop.saveDiagnosticsFile(), null);
      assert.deepEqual(cancelled.calls, ['plugin:dialog|save'], 'no command call after a cancel');
    } finally { cancelled.restore(); }

    // A failing command rejects with the Rust sentence, which the hook shows as it is.
    const failing = tauriBridge({
      'plugin:dialog|save': () => '/Users/x/Desktop/diag.zip',
      save_diagnostics: () => { throw '저장할 위치에 파일을 만들지 못했어요. (denied)'; },
    });
    try {
      const { friendlyError: describeFailure } = await import('../src/lib/errors.ts');
      await assert.rejects(desktop.saveDiagnosticsFile(), (failure) => describeFailure(failure) === '저장할 위치에 파일을 만들지 못했어요. (denied)');
    } finally { failing.restore(); }

    // Outside the desktop app nothing is asked or written.
    assert.equal(await desktop.saveDiagnosticsFile(), null);

    // The save, as the states the UI goes through (the hook's pure core): busy at once, then idle with the result.
    {
      const { runDiagnosticsSave, DIAGNOSTICS_SAVED_TEXT, DIAGNOSTICS_FAILED_TEXT } = await import('../src/lib/diagnosticsSave.ts');
      const run = async (saveFile) => {
        const states = [];
        await runDiagnosticsSave(saveFile, (state) => states.push(state));
        return states;
      };
      assert.deepEqual(await run(async () => ({ path: '/x/diag.zip', files: [] })), [{ busy: true, outcome: null }, { busy: false, outcome: { ok: true, path: '/x/diag.zip' } }], 'busy, then the saved path');
      assert.deepEqual(await run(async () => null), [{ busy: true, outcome: null }, { busy: false, outcome: null }], 'a cancelled dialog ends idle without a result');
      assert.deepEqual(await run(async () => { throw '저장할 위치에 파일을 만들지 못했어요. (denied)'; }), [{ busy: true, outcome: null }, { busy: false, outcome: { ok: false, text: '저장할 위치에 파일을 만들지 못했어요. (denied)' } }], 'a Rust sentence is shown as it is');
      assert.deepEqual((await run(async () => { throw new Error('boom'); })).at(-1), { busy: false, outcome: { ok: false, text: DIAGNOSTICS_FAILED_TEXT } }, 'an unknown error gets the plain fallback');
      assert.equal(DIAGNOSTICS_SAVED_TEXT, '진단 파일을 저장했어요. 이 파일을 전달해 주세요.');
    }
  }

  // After backend_restart the cached connection (old port, old token) must not survive. A request made
  // while the sidecar is still starting caches the old port with no token, so the reset comes only
  // after the status left `starting`.
  {
    let connections = 0;
    let statusCalls = 0;
    const requestedWhileStarting = [];
    const statuses = ['starting', 'starting', 'ready'];
    const bridge = tauriBridge({
      backend_api_connection: () => { connections += 1; return { base_url: `http://127.0.0.1:${9000 + connections}`, token: connections === 1 ? 'old' : 'new' }; },
      backend_supervision_status: async () => {
        const state = statuses[Math.min(statusCalls, statuses.length - 1)];
        statusCalls += 1;
        if (state === 'starting') requestedWhileStarting.push((await desktop.backendApiConnection()).baseUrl); // a request during the restart
        return { state, mode: 'managed', error: null, log_path: null };
      },
    });
    try {
      desktop.resetBackendConnection();
      assert.equal((await desktop.backendApiConnection()).token, 'old');
      assert.equal((await desktop.backendApiConnection()).token, 'old');
      assert.equal(connections, 1, 'the connection is cached');
      await recoverBackend({ intervalMs: 5, timeoutMs: 2000 });
      assert.ok(bridge.calls.indexOf('backend_restart') < bridge.calls.indexOf('backend_supervision_status'), 'status is read after the restart');
      assert.ok(statusCalls >= 3, 'waited until the status was no longer starting');
      assert.deepEqual(requestedWhileStarting, ['http://127.0.0.1:9001', 'http://127.0.0.1:9001'], 'while the backend is starting nothing new is cached (no reset before it settled)');
      assert.equal(connections, 1);
      const next = await desktop.backendApiConnection();
      assert.equal(connections, 2, 'the next connection request after recovery asks backend_api_connection again');
      assert.deepEqual(next, { baseUrl: 'http://127.0.0.1:9002', token: 'new' });
      assert.equal((await desktop.backendApiConnection()).baseUrl, 'http://127.0.0.1:9002');
      assert.equal(connections, 2);
    } finally {
      desktop.resetBackendConnection();
      bridge.restore();
    }
  }

  // A backend that stays down, or never leaves `starting`, makes the recovery reject (bounded wait).
  {
    let statusCalls = 0;
    const bridge = tauriBridge({ backend_supervision_status: () => { statusCalls += 1; return { state: 'failed', mode: 'failed', error: 'x', log_path: null }; } });
    try {
      await assert.rejects(recoverBackend({ intervalMs: 5, timeoutMs: 500 }));
    } finally { bridge.restore(); }
    let starting = 0;
    const slow = tauriBridge({ backend_supervision_status: () => { starting += 1; return { state: 'starting', mode: 'checking', error: null, log_path: null }; } });
    try {
      assert.equal(await waitForBackendSettled({ intervalMs: 5, timeoutMs: 60 }), null, 'gives up after the timeout');
    } finally { slow.restore(); }

    // The wait leaves no timer behind (polling stopped, deadline cleared), observed by tracking live timers.
    const live = new Set();
    const realSet = globalThis.setTimeout; const realClear = globalThis.clearTimeout;
    globalThis.setTimeout = (callback, ms, ...rest) => { const handle = realSet((...args) => { live.delete(handle); callback(...args); }, ms, ...rest); live.add(handle); return handle; };
    globalThis.clearTimeout = (handle) => { live.delete(handle); realClear(handle); };
    const quiet = tauriBridge({ backend_supervision_status: () => ({ state: 'starting', mode: 'checking', error: null, log_path: null }) });
    try {
      assert.equal(await waitForBackendSettled({ intervalMs: 5, timeoutMs: 40 }), null);
      for (let i = 0; i < 30; i += 1) await Promise.resolve();
      assert.equal(live.size, 0, 'no polling or deadline timer survives the end of the wait');
    } finally {
      globalThis.setTimeout = realSet; globalThis.clearTimeout = realClear;
      quiet.restore();
    }

    // Still `starting` at the bound: the recovery rejects and leaves the cached connection alone.
    let fetches = 0;
    const stuck = tauriBridge({
      backend_api_connection: () => { fetches += 1; return { base_url: 'http://127.0.0.1:9100', token: 'old' }; },
      backend_supervision_status: () => ({ state: 'starting', mode: 'checking', error: null, log_path: null }),
    });
    try {
      desktop.resetBackendConnection();
      await desktop.backendApiConnection();
      await assert.rejects(recoverBackend({ intervalMs: 5, timeoutMs: 30 }));
      await desktop.backendApiConnection();
      assert.equal(fetches, 1, 'a timeout while `starting` does not reset the cached connection');
    } finally {
      desktop.resetBackendConnection();
      stuck.restore();
    }

    // One documented bound, at least Rust's 90 s startup timeout plus a margin.
    const updaterModule = await import('../src/lib/updater.ts');
    assert.match(readFileSync('src-tauri/src/backend.rs', 'utf8'), /const BACKEND_STARTUP_TIMEOUT: Duration = Duration::from_secs\(90\);/);
    assert.equal(updaterModule.BACKEND_STARTUP_TIMEOUT_MS, 90_000);
    assert.ok(updaterModule.BACKEND_RECOVERY_TIMEOUT_MS >= 90_000 + 10_000, 'the recovery wait outlasts a backend start');
    assert.equal((readFileSync('src/lib/updater.ts', 'utf8').match(/timeoutMs = /g) ?? []).length, 1);
    assert.ok(statusCalls >= 1);
  }

  // The hook (React hooks replaced by a recording fake): it starts the controller under the Tauri
  // runtime, never outside it, and stops it on unmount.
  {
    const { createServer } = await import('vite');
    const fakeReact = `
      let slots; let index; let effects;
      export const __begin = () => { slots = []; index = 0; effects = []; };
      export const __effects = () => effects;
      const slot = (init) => { const i = index++; if (!(i in slots)) slots[i] = init(); return i; };
      export const __rerender = () => { index = 0; effects = []; };
      export const useRef = (value) => slots[slot(() => ({ current: value }))];
      export const useState = (init) => {
        const i = slot(() => (typeof init === 'function' ? init() : init));
        return [slots[i], (value) => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
      };
      export const useCallback = (callback) => callback;
      export const useMemo = (compute) => compute();
      export const useEffect = (effect) => { effects.push(effect); };
      export const useSyncExternalStore = (subscribe, getSnapshot) => getSnapshot();
    `;
    const server = await createServer({
      root: process.cwd(),
      server: { middlewareMode: true, hmr: false, watch: null },
      appType: 'custom',
      logLevel: 'error',
      optimizeDeps: { noDiscovery: true },
      ssr: { noExternal: ['react'] },
      plugins: [{ name: 'fake-react', enforce: 'pre', resolveId: (id) => (id === 'react' ? '\0fake-react' : null), load: (id) => (id === '\0fake-react' ? fakeReact : null) }],
    });
    try {
      const fake = await server.ssrLoadModule('\0fake-react');
      const { useAppUpdate } = await server.ssrLoadModule('/src/hooks/useAppUpdate.ts');
      const installs = { set: 0, cleared: 0 };
      const realSet = globalThis.setInterval; const realClear = globalThis.clearInterval;
      globalThis.setInterval = (callback, ms) => { installs.set += 1; return realSet(callback, ms); };
      globalThis.clearInterval = (handle) => { installs.cleared += 1; return realClear(handle); };
      const runHook = async (inTauri) => {
        const bridge = inTauri ? tauriBridge({}) : null;
        if (!inTauri) delete globalThis.__TAURI_INTERNALS__;
        const calls = [];
        const api = { check: async () => { calls.push('check'); return null; }, installGuard: async () => ({ status: 'allowed' }), prepare: async () => {}, restartBackend: async () => {}, relaunch: async () => {} };
        fake.__begin();
        const result = useAppUpdate(false, { api });
        const cleanups = fake.__effects().map((effect) => effect()).filter(Boolean);
        for (let i = 0; i < 20; i += 1) await Promise.resolve();
        const checkedAtMount = [...calls];
        cleanups.forEach((cleanup) => cleanup());
        bridge?.restore();
        return { result, checkedAtMount };
      };
      try {
        const outside = await runHook(false);
        assert.deepEqual(outside.checkedAtMount, [], 'no check outside the Tauri runtime');
        assert.equal(installs.set, 0);
        const inside = await runHook(true);
        assert.deepEqual(inside.checkedAtMount, ['check'], 'checks at mount under the Tauri runtime, without any session or backend');
        assert.equal(installs.set, 1, 'the 6 h timer is set');
        assert.equal(installs.cleared, 1, 'and cleared on unmount');
        assert.equal(inside.result.state.phase === 'idle' || inside.result.state.phase === 'checking', true);
        assert.equal(typeof inside.result.update, 'function');
        assert.equal(typeof inside.result.later, 'function');
      } finally {
        globalThis.setInterval = realSet;
        globalThis.clearInterval = realClear;
      }

      // The auth-error path of a download: a running job whose poll comes back "session expired" is made
      // terminal in state, so `active` (the busy flag the app update reads) drops before the workspace is
      // replaced by the login screen.
      const { useDownloads } = await server.ssrLoadModule('/src/hooks/useDownloads.ts');
      const realFetch = globalThis.fetch;
      const realTimeout = globalThis.setTimeout;
      const responses = [{ job_id: 'job-1', status: 'running' }, { job_id: 'job-1', status: 'failed', error_code: 'session_expired' }];
      globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => responses.shift() });
      const pollTicks = [];
      globalThis.setTimeout = (callback, ms, ...rest) => (ms === 1500 ? pollTicks.push(callback) : realTimeout(callback, ms, ...rest));
      let cleanups = [];
      try {
        let authErrors = 0;
        const options = { downloadDir: '/x', onAuthError: () => { authErrors += 1; }, onError() {} };
        fake.__begin();
        const render = () => { fake.__rerender(); return useDownloads(options); };
        let downloads = useDownloads(options);
        assert.equal(downloads.active, false);
        const started = await downloads.start({ courseId: 1, courseName: 'c', kind: 'material', outputs: ['mp4'], items: [{ id: 'a', rawId: 1, title: 't', sizeBytes: null }] });
        downloads = render();
        assert.equal(started, true);
        assert.equal(downloads.active, true, 'a running job is busy');
        cleanups = fake.__effects().map((effect) => effect()).filter(Boolean);
        assert.equal(pollTicks.length, 1, 'the job is polled');
        pollTicks[0]();
        for (let i = 0; i < 40; i += 1) await Promise.resolve();
        downloads = render();
        assert.equal(authErrors, 1, 'the login screen is requested');
        assert.equal(downloads.active, false, 'the failed job is terminal, so no busy flag is left behind');
      } finally {
        cleanups.forEach((cleanup) => cleanup());
        globalThis.fetch = realFetch;
        globalThis.setTimeout = realTimeout;
      }
    } finally {
      await server.close();
    }
  }
}

// ---------------------------------------------------------------------------
// LAYOUT-UI-TIDY: the folder tidy-up (notice, Settings entries, TidyDialog) against the internal API
// (design/09, checked against the backend of LAYOUT-PY-MIGRATION). Copy is design/09 verbatim.
// Nothing here touches a real folder or backend.
// ---------------------------------------------------------------------------
{
  const tidy = await import('../src/lib/tidy.ts');
  const { knownErrorText } = await import('../src/lib/errors.ts');
  const { isTrackedWorkBusy } = await import('../src/lib/trackedWork.ts');
  const { TIDY_COPY } = tidy;
  const noop = () => undefined;

  // Copy (design/09 "화면과 문구").
  assert.equal(TIDY_COPY.notice(3), '이 과목에 예전 방식으로 저장된 파일이 3개 있어요. 주차·강의별 폴더로 정리할 수 있어요.');
  assert.equal(TIDY_COPY.busyHint, '다운로드가 끝나면 정리할 수 있어요');
  assert.equal(TIDY_COPY.preview(2), '파일 2개를 주차·강의별 폴더로 옮겨요. 지우거나 덮어쓰는 파일은 없어요.');
  assert.equal(TIDY_COPY.moving(3, 10), '정리하는 중… 3/10');
  assert.equal(TIDY_COPY.result(3, 0), '3개를 옮겼어요.');
  assert.equal(TIDY_COPY.result(3, 1), '3개를 옮겼어요. 1개는 그대로 뒀어요.');
  assert.equal(TIDY_COPY.undone(2, 1), '2개를 원래 자리로 돌려놨어요. 1개는 그대로 뒀어요.');
  const reasons = {
    destination_exists: '같은 이름의 파일이 이미 있어요', unknown_file: '이 앱이 받은 파일인지 알 수 없어요', partial_download: '받다 만 파일이에요',
    course_not_matched: '이번 학기 과목 목록에 없는 폴더예요', duplicate_transcript: '같은 강의의 자막 글이 이미 있어요 (예전 파일)',
    source_changed: '미리보기 뒤에 파일이 바뀌었어요', source_missing: '미리보기 뒤에 파일이 없어졌어요', not_a_regular_file: '폴더나 바로가기는 옮기지 않아요',
    in_use: '다른 앱이 쓰고 있어요 (Windows)', path_too_long: '경로가 너무 길어요 (Windows)', move_failed: '옮기지 못했어요',
  };
  for (const [code, text] of Object.entries(reasons)) assert.equal(tidy.skipReasonText(code), text);
  assert.equal(tidy.skipReasonText('source_changed', 'undo'), '정리한 뒤에 파일이 바뀌어서 그대로 뒀어요', 'an undo does not talk about the preview');
  assert.ok(!tidy.skipReasonText('source_missing', 'undo').includes('미리보기'));
  assert.equal(tidy.skipReasonText('destination_exists', 'undo'), reasons.destination_exists);
  assert.ok(tidy.skipReasonText('a_future_reason').length > 0 && !tidy.skipReasonText('a_future_reason').includes('a_future_reason'), 'an unknown reason code is never shown raw');
  assert.equal(knownErrorText('library_busy'), '다운로드가 끝난 뒤에 정리할 수 있어요.');
  assert.equal(knownErrorText('library_tidy_running'), '폴더를 정리하는 중이에요. 끝나면 받을 수 있어요.');
  assert.equal(knownErrorText('tidy_plan_expired'), '미리보기가 오래돼서 다시 확인했어요.');
  assert.equal(knownErrorText('tidy_run_not_found'), '되돌릴 정리 기록을 찾지 못했어요.');
  assert.ok(knownErrorText('invalid_run_id') && knownErrorText('library_conflict') && knownErrorText('path_too_long'), 'plain copy for the remaining codes');
  assert.equal(knownErrorText('tidy_job_not_found'), knownErrorText('download_job_not_found'));
  assert.equal(isTrackedWorkBusy({ downloadActive: false, componentsInstalling: false, attachmentSaving: false, libraryTidying: true }), true, 'an app update waits for a running tidy-up');

  // Job outcomes -> dialog states.
  const plan = (moves) => ({ plan_id: 'p1', expires_at: 1, move_count: moves.length, skip_count: 0, moves, skips: [], open_files_note: true });
  const job = (status, extra = {}) => ({ job_id: 'j1', status, cancel_requested: false, progress: [], result: null, error_code: null, ...extra });
  const move = (course, week, stem, from, to) => ({ course, week_folder: week, lecture_stem: stem, from, to, role: stem === null ? 'material' : 'video' });
  const moves = [
    move('선형대수', '05주차', '선대_5_2', 'a/선대_5_2 [68e66b6c1b816].mp4', 'a/05주차/선대_5_2/선대_5_2.mp4'),
    move('선형대수', '05주차', '선대_5_2', 'a/선대_5_2 [68e66b6c1b816].txt', 'a/05주차/선대_5_2/선대_5_2 자막.txt'),
    move('선형대수', '05주차', null, 'a/5주차 자료 [660650405b034].file', 'a/05주차/5주차 자료.pdf'),
    move('선형대수', '06주차', '선대_6_1', 'a/선대_6_1 [68feeb3f3b6dd].mp4', 'a/06주차/선대_6_1/선대_6_1.mp4'),
  ];
  assert.equal(tidy.outcomeOf('plan', job('succeeded', { result: plan(moves) })).phase.kind, 'preview');
  assert.equal(tidy.outcomeOf('plan', job('succeeded', { result: plan([]) })).phase.kind, 'empty');
  assert.equal(tidy.outcomeOf('apply', job('succeeded', { result: { run_id: 'r1', moved: 3, skipped: 0, skips: [], undo_available: true } })).phase.kind, 'result');
  assert.equal(tidy.outcomeOf('apply', job('cancelled', { result: { run_id: 'r1', moved: 1, skipped: 0, skips: [], undo_available: true } })).phase.kind, 'result', 'a stopped move reports what it moved');
  assert.equal(tidy.outcomeOf('undo', job('succeeded', { result: { run_id: 'r1', restored: 3, skipped: 0, skips: [] } })).phase.kind, 'undone');
  assert.deepEqual(tidy.outcomeOf('apply', job('failed', { error_code: 'tidy_plan_expired' })), { replan: true });
  assert.equal(tidy.outcomeOf('plan', job('failed', { error_code: 'library_busy' })).phase.message, '다운로드가 끝난 뒤에 정리할 수 있어요.');
  assert.deepEqual(tidy.outcomeOf('plan', job('cancelled')), { close: true });
  const ticks = job('running', { progress: [{ stage: 'tidy_moving', done: 1, total: 9 }, { stage: 'other' }, { stage: 'tidy_moving', done: 4, total: 9 }] });
  assert.deepEqual(tidy.movingProgress(ticks), { done: 4, total: 9 });
  assert.deepEqual(tidy.movingProgress(job('running'), { done: 0, total: 12 }), { done: 0, total: 12 }, 'the known total stays until the first event');

  // Grouping (a material has no lecture), the "n개 더" cap, which run Settings can undo, unsupported backends.
  assert.deepEqual(tidy.groupMoves(moves).map((course) => course.weeks.map((week) => [week.week, week.lectures.map((lecture) => [lecture.stem, lecture.moves.length])])), [[['05주차', [['선대_5_2', 2], [null, 1]]], ['06주차', [['선대_6_1', 1]]]]]);
  assert.deepEqual(tidy.capRows([1, 2, 3], 2), { shown: [1, 2], hidden: 1 });
  assert.equal(tidy.undoableRun([{ run_id: 'new', at: null, undo_available: false }, { run_id: 'old', at: null, undo_available: true }]), null, 'only the latest run can be undone');
  assert.equal(tidy.undoableRun([{ run_id: 'new', at: null, undo_available: true }]).run_id, 'new');
  assert.ok(tidy.isUnsupportedBackend('not_found') && tidy.isUnsupportedBackend('HTTP 404') && !tidy.isUnsupportedBackend('network_error'));

  // The notice: shown for old files, not for 0 / unknown, after 나중에, or when a preview found nothing; blocked while downloading.
  assert.deepEqual(tidy.legacyNoticeState(12, false, false, false), { count: 12, blocked: false });
  assert.deepEqual(tidy.legacyNoticeState(12, false, false, true), { count: 12, blocked: true });
  for (const hidden of [tidy.legacyNoticeState(0, false, false, false), tidy.legacyNoticeState(null, false, false, false), tidy.legacyNoticeState(12, true, false, false), tidy.legacyNoticeState(12, false, true, false)]) assert.equal(hidden, null);

  const { createServer } = await import('vite');
  const React = (await import('react')).default;
  const { renderToStaticMarkup } = await import('react-dom/server');
  const server = await createServer({ root: process.cwd(), server: { middlewareMode: true, hmr: false, watch: null }, appType: 'custom', logLevel: 'error', optimizeDeps: { noDiscovery: true } });
  const realFetch = globalThis.fetch;
  try {
    // Every state of the dialog, the notice and the Settings entries, rendered with real React.
    const { TidyDialog, LegacyNotice, TidyEntries } = await server.ssrLoadModule('/src/components/TidyDialog.tsx');
    const show = (phase, extra = {}) => renderToStaticMarkup(React.createElement(TidyDialog, { tidy: { phase, note: null, stopping: false, reconnecting: false, close: noop, apply: noop, undo: noop, stop: noop, ...extra }, canOpenFolder: true, onOpenFolder: noop }));
    const withSkip = { ...plan(moves), skip_count: 1, skips: [{ path: 'a/메모.docx', reason: 'unknown_file' }] };
    assert.ok(show({ kind: 'checking' }).includes('옮길 파일을 확인하고 있어요…'));
    const preview = show({ kind: 'preview', plan: withSkip }, { note: '미리보기가 오래돼서 다시 확인했어요.' });
    for (const text of ['파일 4개를 주차·강의별 폴더로 옮겨요. 지우거나 덮어쓰는 파일은 없어요.', '선형대수', '05주차', '선대_5_2', '선대_5_2 [68e66b6c1b816].mp4', '선대_5_2 자막.txt', '5주차 자료.pdf', '<summary>그대로 두는 파일 1개</summary>', '이 앱이 받은 파일인지 알 수 없어요', '열려 있는 파일도 옮겨져요. 재생 중인 영상은 계속 재생되지만, 다시 열 때는 새 폴더에서 열어 주세요.', '미리보기가 오래돼서 다시 확인했어요.', '취소', 'btn btn-sm btn-primary" type="button">정리하기'.replace('" type="button"', '')]) {
      assert.ok(preview.includes(text) || text.startsWith('btn'), `preview missing ${text}`);
    }
    assert.ok(/class="btn btn-sm btn-primary"[^>]*>정리하기/.test(preview), 'the commit button is the accent button');
    assert.equal((preview.match(/class="tidy-lecture"/g) ?? []).length, 2, 'two lecture headings; the material sits directly under its week');
    assert.ok(!preview.includes('<details open') && !preview.includes('>a/'), 'skips start collapsed and the list shows names, not paths');
    assert.ok(show({ kind: 'empty' }).includes('정리할 파일이 없어요.'));
    const movingView = show({ kind: 'moving', job: 'apply', done: 3, total: 10 }, { reconnecting: true });
    for (const text of ['정리하는 중… 3/10', '지금 파일까지만 옮기고 멈춰요', '연결 확인 중…', '멈추기']) assert.ok(movingView.includes(text), `moving missing ${text}`);
    assert.ok(!movingView.includes('aria-label="닫기"'), 'a running move cannot be closed');
    assert.ok(show({ kind: 'moving', job: 'undo', done: 0, total: 0 }).includes('되돌리는 중…'));
    const result = show({ kind: 'result', result: { run_id: 'r1', moved: 3, skipped: 1, skips: withSkip.skips, undo_available: true } });
    for (const text of ['3개를 옮겼어요. 1개는 그대로 뒀어요.', '폴더 열기', '되돌리기', '닫기']) assert.ok(result.includes(text), `result missing ${text}`);
    assert.ok(!show({ kind: 'result', result: { run_id: null, moved: 0, skipped: 2, skips: [], undo_available: false } }).includes('되돌리기'));
    assert.ok(show({ kind: 'undone', result: { run_id: 'r1', restored: 3, skipped: 1, skips: [{ path: 'a/x.mp4', reason: 'source_changed' }] } }).includes('정리한 뒤에 파일이 바뀌어서 그대로 뒀어요'));
    assert.ok(show({ kind: 'error', message: '다운로드가 끝난 뒤에 정리할 수 있어요.' }).includes('role="alert"'));

    const render = (element) => renderToStaticMarkup(element);
    const notice = tidy.legacyNoticeState(12, false, false, false);
    assert.ok(render(React.createElement(LegacyNotice, { notice, onTidy: noop, onLater: noop })).includes(TIDY_COPY.notice(12)));
    const blockedNotice = render(React.createElement(LegacyNotice, { notice: tidy.legacyNoticeState(12, false, false, true), onTidy: noop, onLater: noop }));
    assert.ok(/<button[^>]*disabled=""[^>]*title="다운로드가 끝나면 정리할 수 있어요"[^>]*>정리하기/.test(blockedNotice) || /<button[^>]*title="다운로드가 끝나면 정리할 수 있어요"[^>]*disabled=""[^>]*>정리하기/.test(blockedNotice), 'blocked: disabled with the busy title');
    const entries = (runs, undoRun, blocked) => render(React.createElement(TidyEntries, { tidy: { runs, undoRun }, blocked, onTidy: noop, onUndoTidy: noop }));
    const undoable = { run_id: 'r1', at: null, moved: 1, undo_available: true };
    assert.ok(entries({ state: 'ready', list: [undoable] }, undoable, false).includes('폴더 정리…') && entries({ state: 'ready', list: [undoable] }, undoable, false).includes('마지막 정리 되돌리기'));
    assert.ok(!entries({ state: 'ready', list: [] }, null, false).includes('마지막 정리 되돌리기'), 'no undo entry without an undo-able run');
    assert.equal(entries({ state: 'unsupported', list: [] }, null, false), '', 'no entries on a backend without the tidy-up');
    const blockedEntries = entries({ state: 'ready', list: [undoable] }, undoable, true);
    assert.equal((blockedEntries.match(/disabled=""/g) ?? []).length, 2);
    assert.equal((blockedEntries.match(/title="다운로드가 끝나면 정리할 수 있어요"/g) ?? []).length, 2);

    // Requests of the contract; Canvas downloads pass the page's week; undo names its folder.
    const api = await server.ssrLoadModule('/src/lib/api.ts');
    const sent = [];
    globalThis.fetch = async (url, init = {}) => { sent.push([init.method ?? 'GET', String(url), init.body ? JSON.parse(init.body) : undefined]); return { ok: true, status: 200, json: async () => ({}) }; };
    await api.downloadCanvasFile(7, 'f1', '선형대수', '/dl', 'skip', { module_name: '5주차', week_position: 5 });
    await api.downloadCanvasFile(7, 'f1', '선형대수', '/dl');
    await api.startTidyPlanJob('/dl');
    await api.startTidyApplyJob('p1');
    await api.startTidyUndoJob('r1', '/dl');
    await api.cancelTidyJob('apply', 'j1');
    await api.fetchTidyRuns('/dl');
    assert.equal(sent[0][2].module_name, '5주차');
    assert.equal(sent[0][2].week_position, 5);
    assert.ok(!('module_name' in sent[1][2]) && !('week_position' in sent[1][2]), 'without a week the optional fields are left out');
    assert.deepEqual(sent.slice(2), [
      ['POST', '/api/library/tidy/plan/jobs', { output_dir: '/dl' }],
      ['POST', '/api/library/tidy/apply/jobs', { plan_id: 'p1' }],
      ['POST', '/api/library/tidy/undo/jobs', { run_id: 'r1', output_dir: '/dl' }],
      ['POST', '/api/library/tidy/apply/jobs/j1/cancel', undefined],
      ['GET', '/api/library/tidy/runs?output_dir=%2Fdl', undefined],
    ]);
  } finally {
    globalThis.fetch = realFetch;
    await server.close();
  }

  // The hook, with a small React stand-in (state, refs, effects with dependencies; no rendering) and a scripted backend.
  const fakeReact = `
    let slots; let index; let effects;
    export const __begin = () => { slots = []; index = 0; effects = []; };
    export const __effects = () => { const run = effects; effects = []; run.forEach((effect) => effect()); };
    export const __rerender = () => { index = 0; effects = []; };
    export const __unmount = () => slots.forEach((entry) => entry && entry.cleanup && entry.cleanup());
    const slot = (init) => { const i = index++; if (!(i in slots)) slots[i] = init(); return i; };
    export const useRef = (value) => slots[slot(() => ({ current: value }))];
    export const useState = (init) => {
      const i = slot(() => (typeof init === 'function' ? init() : init));
      return [slots[i], (value) => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
    };
    export const useCallback = (callback, deps) => {
      const entry = slots[slot(() => ({ deps: undefined, callback: null }))];
      if (!entry.callback || !deps || !deps.every((dep, k) => Object.is(dep, entry.deps[k]))) { entry.callback = callback; entry.deps = deps; }
      return entry.callback;
    };
    export const useEffect = (effect, deps) => {
      const entry = slots[slot(() => ({ deps: undefined, ran: false, cleanup: null }))];
      if (entry.ran && deps && deps.every((dep, k) => Object.is(dep, entry.deps[k]))) return;
      effects.push(() => { if (entry.cleanup) entry.cleanup(); entry.cleanup = effect() || null; entry.deps = deps; entry.ran = true; });
    };
  `;
  const hookServer = await createServer({
    root: process.cwd(),
    server: { middlewareMode: true, hmr: false, watch: null },
    appType: 'custom',
    logLevel: 'error',
    optimizeDeps: { noDiscovery: true },
    ssr: { noExternal: ['react'] },
    plugins: [{ name: 'fake-react', enforce: 'pre', resolveId: (id) => (id === 'react' ? '\0fake-react' : null), load: (id) => (id === '\0fake-react' ? fakeReact : null) }],
  });
  const realTimeout = globalThis.setTimeout;
  try {
    const fake = await hookServer.ssrLoadModule('\0fake-react');
    const { useLibraryTidy } = await hookServer.ssrLoadModule('/src/hooks/useLibraryTidy.ts');
    const { legacyNoticeState } = await hookServer.ssrLoadModule('/src/lib/tidy.ts');
    const flush = async () => { for (let i = 0; i < 60; i += 1) await Promise.resolve(); };
    const pollTicks = [];
    globalThis.setTimeout = (callback, ms, ...rest) => (ms === 1500 ? pollTicks.push(callback) : realTimeout(callback, ms, ...rest));
    const json = (status, body) => ({ ok: status < 400, status, json: async () => body });
    const applyResult = { run_id: 'r1', moved: 4, skipped: 0, skips: [], undo_available: true };

    // routes: 'METHOD /path' -> function(body) returning a response (or a promise of one); calls are recorded.
    const scenario = async (routes, run) => {
      const calls = [];
      let changed = 0;
      let nothing = 0;
      globalThis.fetch = async (url, init = {}) => {
        const key = `${init.method ?? 'GET'} ${String(url).split('?')[0]}`;
        calls.push([key, init.body ? JSON.parse(init.body) : undefined]);
        const handler = routes[key];
        if (!handler) return json(200, { runs: [] });
        return handler(init.body ? JSON.parse(init.body) : undefined, String(url));
      };
      fake.__begin();
      pollTicks.length = 0;
      const workspace = readFileSync('src/App.tsx', 'utf8');
      const noticeContext = vm.createContext({ dir: '/dl', nothingToTidy: null, latest: { current: { course: null } } });
      noticeContext.setNothingToTidy = (value) => { noticeContext.nothingToTidy = value; };
      const noticeCallbacks = vm.runInContext(`({
        ${workspace.match(/onNothingToTidy: \(\) => [^\n]+/)[0]}
        ${workspace.match(/onChanged: \(\) => \{[\s\S]*?\n    \},/)[0]}
      })`, noticeContext);
      const ctx = { calls, flush, get notice() { return legacyNoticeState(12, false, noticeContext.nothingToTidy === noticeContext.dir, false); }, get changed() { return changed; }, get nothing() { return nothing; } };
      const read = (outputDir = '/dl') => { noticeContext.dir = outputDir; fake.__rerender(); const value = useLibraryTidy({ outputDir, onChanged: () => { changed += 1; noticeCallbacks.onChanged(); }, onNothingToTidy: () => { nothing += 1; noticeCallbacks.onNothingToTidy(); }, onAuthError: noop }); fake.__effects(); return value; };
      ctx.read = read;
      ctx.tick = async () => { pollTicks.shift()(); await flush(); return read(); };
      ctx.count = (key) => calls.filter(([entry]) => entry === key).length;
      await run(ctx, read());
      fake.__unmount();
    };
    const planDone = () => json(200, job('succeeded', { result: plan(moves) }));
    const running = (id, done, total) => json(200, job('running', { job_id: id, progress: [{ stage: 'tidy_moving', done, total }] }));

    // An expired preview is checked again by itself, with the plain note; nothing moved, so nothing reloads.
    await scenario({ 'POST /api/library/tidy/plan/jobs': planDone, 'POST /api/library/tidy/apply/jobs': () => json(409, { ok: false, error_code: 'tidy_plan_expired' }) }, async (c, hook) => {
      hook.open();
      await flush();
      hook = c.read();
      assert.equal(hook.phase.kind, 'preview');
      hook.apply();
      await flush();
      hook = c.read();
      assert.equal(c.count('POST /api/library/tidy/plan/jobs'), 2);
      assert.equal(hook.phase.kind, 'preview', 'the expired preview was replaced by a fresh one');
      assert.equal(hook.note, '미리보기가 오래돼서 다시 확인했어요.');
      assert.equal(c.changed, 0);
    });

    // A preview with nothing to move tells the app (the notice goes away).
    await scenario({ 'POST /api/library/tidy/plan/jobs': () => json(200, job('succeeded', { result: plan([]) })) }, async (c, hook) => {
      hook.open();
      await flush();
      assert.equal(c.read().phase.kind, 'empty');
      assert.equal(c.nothing, 1);
      assert.equal(c.notice, null);
      c.read('/other');
      assert.deepEqual(c.notice, { count: 12, blocked: false }, 'another folder is not suppressed');
    });

    // Stop during a move: the job reports what it moved.
    await scenario({
      'POST /api/library/tidy/plan/jobs': planDone,
      'POST /api/library/tidy/apply/jobs': () => running('j-apply', 2, 4),
      'POST /api/library/tidy/apply/jobs/j-apply/cancel': () => json(200, job('cancelled', { job_id: 'j-apply', result: { ...applyResult, moved: 2 } })),
    }, async (c, hook) => {
      hook.open();
      await flush();
      hook = c.read();
      hook.apply();
      await flush();
      hook = c.read();
      assert.deepEqual([hook.phase.kind, hook.phase.done, hook.phase.total, hook.moving], ['moving', 2, 4, true]);
      await hook.stop();
      hook = c.read();
      assert.equal(c.count('POST /api/library/tidy/apply/jobs/j-apply/cancel'), 1);
      assert.deepEqual([hook.phase.kind, hook.phase.result.moved, hook.moving], ['result', 2, false]);
      assert.equal(c.changed, 1);
    });

    // 멈추기 pressed before the backend named the job is sent as soon as it did.
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    await scenario({
      'POST /api/library/tidy/plan/jobs': planDone,
      'POST /api/library/tidy/apply/jobs': async () => { await gate; return running('j-apply', 0, 4); },
      'POST /api/library/tidy/apply/jobs/j-apply/cancel': () => json(200, job('cancelled', { job_id: 'j-apply', result: { ...applyResult, moved: 0, run_id: null, undo_available: false } })),
    }, async (c, hook) => {
      hook.open();
      await flush();
      hook = c.read();
      hook.apply();
      hook = c.read();
      assert.deepEqual([hook.phase.kind, hook.phase.total], ['moving', 4], 'the known total shows before the first event');
      await hook.stop();
      assert.equal(c.count('POST /api/library/tidy/apply/jobs/j-apply/cancel'), 0, 'nothing to cancel yet');
      release();
      await flush();
      hook = c.read();
      assert.equal(c.count('POST /api/library/tidy/apply/jobs/j-apply/cancel'), 1, 'the queued stop was sent');
      assert.equal(hook.phase.kind, 'result');
    });

    // A failed poll during a move keeps the move on screen; the next good poll finishes it.
    let polls = 0;
    await scenario({
      'POST /api/library/tidy/plan/jobs': planDone,
      'POST /api/library/tidy/apply/jobs': () => running('j-apply', 1, 4),
      'GET /api/library/tidy/apply/jobs/j-apply': () => {
        polls += 1;
        if (polls === 1) throw new TypeError('Failed to fetch');
        return json(200, job('succeeded', { job_id: 'j-apply', result: applyResult }));
      },
    }, async (c, hook) => {
      hook.open();
      await flush();
      hook = c.read();
      hook.apply();
      await flush();
      hook = c.read();
      hook = await c.tick();
      assert.deepEqual([hook.phase.kind, hook.moving, hook.reconnecting], ['moving', true, true], 'a poll error does not abandon the move');
      hook = await c.tick();
      assert.deepEqual([hook.phase.kind, hook.reconnecting], ['result', false]);
      assert.equal(c.changed, 1);
    });

    // A lost job (not a hiccup) does end the move view, with plain copy.
    await scenario({
      'POST /api/library/tidy/plan/jobs': planDone,
      'POST /api/library/tidy/apply/jobs': () => running('j-apply', 1, 4),
      'GET /api/library/tidy/apply/jobs/j-apply': () => json(404, { ok: false, error_code: 'tidy_job_not_found' }),
    }, async (c, hook) => {
      hook.open();
      await flush();
      hook = c.read();
      hook.apply();
      await flush();
      hook = c.read();
      hook = await c.tick();
      assert.deepEqual([hook.phase.kind, hook.moving], ['error', false]);
      assert.equal(c.changed, 1, 'a lost move refreshes saved files');
      assert.equal(c.count('GET /api/library/tidy/runs'), 2, 'a lost move refreshes the run list');
    });

    // Closing during the check cancels the preview job, even when the close came before the backend named it.
    let releasePlan;
    const planGate = new Promise((resolve) => { releasePlan = resolve; });
    await scenario({
      'POST /api/library/tidy/plan/jobs': async () => { await planGate; return json(200, job('running', { job_id: 'j-plan' })); },
      'POST /api/library/tidy/plan/jobs/j-plan/cancel': () => json(200, job('cancelled', { job_id: 'j-plan' })),
    }, async (c, hook) => {
      hook.open();
      hook = c.read();
      assert.equal(hook.phase.kind, 'checking');
      hook.close();
      releasePlan();
      await flush();
      hook = c.read();
      assert.equal(hook.phase, null, 'a late answer does not reopen the dialog');
      assert.equal(c.count('POST /api/library/tidy/plan/jobs/j-plan/cancel'), 1);
    });

    // Undo (named folder), then the course reloads; a start that fails reloads the run list.
    const undoable = { run_id: 'run-1', at: null, moved: 4, undo_available: true };
    await scenario({
      'GET /api/library/tidy/runs': () => json(200, { runs: [undoable] }),
      'POST /api/library/tidy/plan/jobs': () => json(200, job('succeeded', { result: plan([]) })),
      'POST /api/library/tidy/undo/jobs': () => json(200, job('succeeded', { job_id: 'j-undo', result: { run_id: 'run-1', restored: 4, skipped: 0, skips: [] } })),
    }, async (c, hook) => {
      await flush();
      hook = c.read();
      assert.equal(hook.undoRun.run_id, 'run-1');
      hook.open();
      await flush();
      hook = c.read();
      assert.equal(c.notice, null, 'an empty preview hides the notice');
      hook.undo();
      await flush();
      hook = c.read();
      assert.deepEqual(c.calls.find(([key]) => key === 'POST /api/library/tidy/undo/jobs')[1], { run_id: 'run-1', output_dir: '/dl' });
      assert.equal(hook.phase.kind, 'undone');
      assert.equal(c.changed, 1);
      assert.deepEqual(c.notice, { count: 12, blocked: false }, 'undo after an empty preview brings the notice back');
    });
    await scenario({
      'GET /api/library/tidy/runs': () => json(200, { runs: [undoable] }),
      'POST /api/library/tidy/undo/jobs': () => json(409, { ok: false, error_code: 'tidy_run_not_found' }),
    }, async (c, hook) => {
      await flush();
      hook = c.read();
      const before = c.count('GET /api/library/tidy/runs');
      hook.undo();
      await flush();
      hook = c.read();
      assert.equal(hook.phase.message, '되돌릴 정리 기록을 찾지 못했어요.');
      assert.equal(c.count('GET /api/library/tidy/runs'), before + 1, 'the run list is read again');
    });

    // A late response for the previous folder cannot replace the current folder's runs.
    for (const staleFails of [false, true]) {
      let releaseOld;
      const oldResponse = new Promise((resolve) => { releaseOld = resolve; });
      const currentRun = { ...undoable, run_id: 'new-folder-run' };
      await scenario({
        'GET /api/library/tidy/runs': async (_body, url) => {
          if (new URL(url, 'http://localhost').searchParams.get('output_dir') === '/dl') {
            await oldResponse;
            return staleFails ? json(404, { ok: false, error_code: 'not_found' }) : json(200, { runs: [undoable] });
          }
          return json(200, { runs: [currentRun] });
        },
      }, async (c) => {
        c.read('/other');
        await flush();
        assert.equal(c.read('/other').undoRun.run_id, currentRun.run_id);
        releaseOld();
        await flush();
        const hook = c.read('/other');
        assert.equal(hook.runs.state, 'ready');
        assert.equal(hook.undoRun.run_id, currentRun.run_id, 'stale success and failure both leave the current list intact');
      });
    }

    // A backend that answers its unknown routes with `not_found` has no tidy-up: the entries stay hidden.
    await scenario({ 'GET /api/library/tidy/runs': () => json(404, { ok: false, error_code: 'not_found' }) }, async (c, hook) => {
      await flush();
      assert.equal(c.read().runs.state, 'unsupported');
    });
  } finally {
    globalThis.setTimeout = realTimeout;
    globalThis.fetch = realFetch;
    await hookServer.close();
  }
}
