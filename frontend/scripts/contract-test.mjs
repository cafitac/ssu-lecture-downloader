import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

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
  'src/components/TopBar.tsx',
  'src/components/CourseSidebar.tsx',
  'src/components/LecturePane.tsx',
  'src/components/LectureList.tsx',
  'src/components/TaskList.tsx',
  'src/components/DeadlinesPane.tsx',
  'src/lib/deadline.ts',
  'src/hooks/useDeadlines.ts',
  'src/components/ActionBar.tsx',
  'src/components/DownloadDrawer.tsx',
  'src/components/SettingsDialog.tsx',
  'src/components/Screens.tsx',
  'src-tauri/src/lib.rs',
  'src-tauri/src/backend.rs',
  'src-tauri/src/native_download.rs',
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
for (const endpoint of ['/api/credentials/status', '/api/credentials', '/api/courses', '/api/courses/', '/api/deadlines', '/api/watch/viewer-plan', '/api/download/jobs', '/api/download/batch/jobs', '/api/download/plan', '/api/download', '/api/download/batch/plan', '/api/download/batch', '/api/media/audio/jobs', '/api/stt/providers/status', '/api/media/dependencies/status', '/api/media/dependencies/install/jobs', '/api/stt/jobs']) {
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
  'src/lib/errors.ts',
  'src/lib/polling.ts',
  'src/lib/format.ts',
  'src/lib/content.ts',
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
for (const file of readTree('src/components', ['.tsx']).filter((name) => !name.endsWith('ActionBar.tsx') && !name.endsWith('Screens.tsx'))) {
  if (readFileSync(file, 'utf8').includes('btn-primary')) throw new Error(`${file}: no other accent-filled button may compete with the download button`);
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
if (!main.includes('planOfficialViewerSessionBridge') || !main.includes('openOfficialViewer') || !main.includes('official-watch-ended') || !main.includes('openAssistedOfficialViewer')) {
  throw new Error('Row play and sequential watch must keep using the official viewer bridge and the real ended event');
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
if (!desktop.includes('openAssistedOfficialViewer') || !desktop.includes("invoke<void>('open_assisted_official_viewer'") || !tauriLib.includes('open_assisted_official_viewer') || !tauriLib.includes('initialization_script_for_all_frames(official_watch_assist_script())') || !tauriLib.includes('ssu-watch://ended') || !tauriLib.includes("video.addEventListener('ended'") || tauriLib.includes('TargetUrl')) {
  throw new Error('Sequential watch mode must use an assisted official viewer that only attempts visible playback and advances after the real video ended event');
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
