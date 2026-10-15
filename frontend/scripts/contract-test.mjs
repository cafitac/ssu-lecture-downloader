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
  'src/lib/api.ts',
  'src/lib/desktop.ts',
  'src/lib/types.ts',
  'src/lib/polling.ts',
  'src/lib/progress.ts',
  'src/lib/http.ts',
  'src/lib/postprocess.ts',
  'src/hooks/usePolledJob.ts',
  'src/hooks/useBackendStatus.ts',
  'src/hooks/useTauriEvent.ts',
  'src/components/LoginPanel.tsx',
  'src/components/CourseList.tsx',
  'src/components/DownloadPanel.tsx',
  'src/components/WatchQueue.tsx',
  'src/components/BackendStatus.tsx',
  'src-tauri/src/lib.rs',
  'src-tauri/src/backend.rs',
  'src-tauri/src/native_download.rs',
  'src-tauri/src/viewer.rs',
  'src-tauri/src/assets/watch_assist.js',
  'src/styles.css',
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
for (const endpoint of ['/api/credentials/status', '/api/credentials', '/api/courses', '/api/courses/', '/api/watch/viewer-plan', '/api/download/jobs', '/api/download/batch/jobs', '/api/download/plan', '/api/download', '/api/download/batch/plan', '/api/download/batch', '/api/media/audio/jobs', '/api/stt/providers/status', '/api/media/dependencies/status', '/api/media/dependencies/install/jobs', '/api/stt/jobs']) {
  if (!api.includes(endpoint)) {
    throw new Error(`API client missing endpoint ${endpoint}`);
  }
}

// The UI is split across main.tsx, components/, hooks/ and the pure UI helpers in lib/;
// the copy/behavior checks below look at all of them together.
const uiSourceFiles = [
  'src/main.tsx',
  ...readTree('src/components', ['.tsx']),
  ...readTree('src/hooks', ['.ts']),
  'src/lib/progress.ts',
  'src/lib/polling.ts',
  'src/lib/format.ts',
  'src/lib/content.ts',
];
const main = uiSourceFiles.map((file) => readFileSync(file, 'utf8')).join('\n');
const mainEntry = readFileSync('src/main.tsx', 'utf8');
for (const text of ['처음 실행하면 계정 정보만 입력하면 됩니다', '앱 저장소', '로그아웃', '다운로드 폴더', '현재 학기', '과거 학기는 명시 선택', '학기 선택', '선택한 학기 불러오기', '강의 선택', '전체 선택', '다운로드 시작', '취소하기', '다운로드 진행률', '저장 위치', '다운로드 폴더 열기', '다운로드할 강의', '하나만 선택하면 단일 다운로드', '2개 이상이면 최대 4개씩 동시 다운로드합니다', '자동 준비', '영상·음성·자막 저장 준비 완료', '구성요소 확인 필요', '필요한 항목만 앱에서 자동으로 준비할 수 있어요']) {
  if (!main.includes(text)) {
    throw new Error(`UI missing required Korean copy: ${text}`);
  }
}

if (main.includes('credential-panel') || main.includes('앱 실행 중 로그인 세션')) {
  throw new Error('Logged-in UI must not reserve a full credential/session panel; expose logout in the header instead');
}

if (!main.includes('handleSelectAllLectures') || !main.includes('const selectableLectureIds') || !main.includes('const allLecturesSelected') || !main.includes('lecture-toolbar')) {
  throw new Error('UI must provide an all-lectures select/clear control for downloadable lecture rows');
}

if (!main.includes('completionFilter') || !main.includes('aria-label="완료 상태 필터"') || !main.includes('전체 완료 상태') || !main.includes("completionFilter === 'unknown'")) {
  throw new Error('Course detail UI must provide completion-state filtering for all/completed/uncompleted/unknown rows');
}
if (main.includes("if (contentKindFilter === 'downloadable') return item.downloadable;")) {
  throw new Error('Downloadable content filtering must not return before completion filters are applied');
}
if (!main.includes("contentKindFilter === 'downloadable' && !item.downloadable") || !main.includes("contentKindFilter !== 'downloadable' && item.kind !== contentKindFilter")) {
  throw new Error('Course detail filters must compose downloadable/type and completion predicates instead of short-circuiting them');
}

if (!main.includes('usePolledJob(downloadJob') || !main.includes('fetch: fetchDownloadJob')) {
  throw new Error('UI must auto-poll active download jobs');
}
const pollingSource = readFileSync('src/lib/polling.ts', 'utf8');
if (/\bsetInterval\b/.test(main) || /\bsetInterval\b/.test(pollingSource) || !pollingSource.includes('setTimeout')) {
  throw new Error('Polling must be a setTimeout chain (no setInterval) so requests never overlap or arrive out of order');
}
for (const asyncPoller of ['fetchAudioExtractionJob', 'fetchSttJob', 'fetchMediaDependencyInstallJob']) {
  if (!main.includes(`fetch: ${asyncPoller}`)) {
    throw new Error(`${asyncPoller} must be polled through usePolledJob`);
  }
}
if (main.includes('postprocess-card') || main.includes('후처리') || main.includes('음성 추출 · STT 실행')) {
  throw new Error('Default UI must not expose a separate manual postprocess panel; MP3/TXT should be created from the main file selection flow');
}
if (!api.includes('withArtifactJobId') || !api.includes('return withArtifactJobId(job);') || !api.includes('/api/download/artifacts/jobs/${jobId.slice')) {
  throw new Error('Artifact download jobs must keep their artifact-prefixed job id across polling/cancel so MP3/TXT progress does not disappear');
}
if (api.includes('failed_fallback_backend_job') || api.includes('startBackendBatchDownloadJob') || api.includes('startBackendDownloadJob')) {
  throw new Error('Tauri native download start must not hide failures behind backend fallback jobs');
}
if (!api.includes('native_plan_failed:${code}') || !api.includes('native_batch_plan_failed:${code}') || !api.includes('선택한 ${index + 1}번째 영상의 다운로드 주소를 준비하지 못했습니다')) {
  throw new Error('Tauri native plan failures must raise explicit item-level errors instead of ambiguous fallback behavior');
}
if (main.includes('value={credentialPassword}') || main.includes('{credentialPassword}</')) {
  throw new Error('UI must not render saved credential password values');
}

if (!main.includes('const currentTermId = await loadTerms()') || !main.includes('void loadCourses(currentTermId)')) {
  throw new Error('UI initial load must use current term id from /api/terms instead of scanning /api/courses without term_id');
}
if (main.includes('void loadCourses(null);')) {
  throw new Error('UI initial load must not call loadCourses(null), because that triggers duplicate current-term scanning');
}
if (!main.includes('isSessionVerified') || !main.includes('const shouldShowLogin = !hasStoredCredential || !isSessionVerified') || !main.includes('저장된 로그인 정보로 LMS 세션을 확인하는 중입니다')) {
  throw new Error('UI must not show the logged-in course screen until stored credentials have been verified by a real LMS list load');
}
if (main.includes("async function loadCourses(termId: number | null = selectedTermId) {\n    if (state === 'loading') return;")) {
  throw new Error('Course loading must not return early just because login/startup already set loading state');
}

if (!api.includes('output_dir') || !api.includes('outputDir')) {
  throw new Error('API client must pass the selected output_dir from the UI');
}
if (!api.includes('sso_redirect_missing') || !api.includes('숭실 SmartID 로그인은 응답했지만 LMS 이동 주소를 찾지 못했습니다') || !api.includes('처리 중 오류가 발생했습니다: ${code}')) {
  throw new Error('API client must translate low-level login error codes into clear Korean user messages');
}
if (!api.includes('payload?.message ?? errorMessage(code)')) {
  throw new Error('API client must prefer backend-provided error messages for detailed LMS/native-plan failures');
}
if (!api.includes("stt_model: 'large-v3'") || !api.includes("model = 'large-v3'")) {
  throw new Error('API client must default TXT/STT jobs to Whisper large-v3 for Korean lecture accuracy');
}
if (!main.includes('Whisper large-v3') || !main.includes("startSttJob(audioPath, 'faster_whisper', 'large-v3'")) {
  throw new Error('UI must run the high-accuracy large-v3 STT path by default');
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
if (!main.includes('Finder에서 선택') || !main.includes('handlePickDownloadDir') || !main.includes('defaultDownloadDirectory()')) {
  throw new Error('UI must initialize a native default download folder and expose Finder folder selection');
}
if (!main.includes('자동 기본값') || !main.includes('선택하지 않아도 다운로드할 수 있습니다') || !main.includes('~/Downloads')) {
  throw new Error('UI must explain automatic Downloads folder behavior');
}
if (main.includes('~/Downloads/ssu-lectures') || desktop.includes('ssu-lectures') || desktop.includes('/.hermes/profiles/')) {
  throw new Error('Default download destination must be the real user ~/Downloads, not a nested app/profile folder');
}
if (!tauriLib.includes('real_download_dir') || !tauriLib.includes('getpwuid') || !tauriLib.includes('invoke_handler')) {
  throw new Error('Tauri app must expose a real macOS user Downloads command that is not derived from Hermes HOME');
}
for (const marker of ['BackendManager', 'start_in_background', 'backend_health_check', 'backend_health_response_is_compatible', 'artifact_download_jobs', 'stt_provider_status', 'backend_launch_command', 'choose_backend_port', 'backend_api_base_url', 'uvicorn', 'backend_supervision_status']) {
  if (!tauriLib.includes(marker)) {
    throw new Error(`Tauri backend supervision missing marker: ${marker}`);
  }
}
if (!main.includes('앱 연결됨') || !main.includes('useBackendStatus')) {
  throw new Error('UI must expose a non-developer app connection state');
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
if (!main.includes('다시 시도') || !main.includes('log_path')) {
  throw new Error('Backend failure UI must offer a retry button and show the backend log path');
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
if (!main.includes('download-progress-card') || !main.includes('progressbar') || !main.includes('downloadPrimaryActionLabel')) {
  throw new Error('UI must present a user-friendly progress card and one primary start/cancel button');
}
if (!main.includes('const shouldShowDownloadProgress') || !main.includes('shouldShowDownloadProgress &&') || !main.includes('Boolean(isStartingDownload || downloadStartError || downloadJob || activeSingleResult || activeBatchResult)') || !main.includes('저장 위치와 파일 정보를 확인하는 중입니다')) {
  throw new Error('Download progress card must appear immediately during pre-download preparation, before the real job id exists');
}
if (!main.includes('appendDownloadDiagnostic') || !main.includes('ssu-download-diagnostics') || !main.includes('다운로드 진단 로그') || !main.includes('downloadStartError') || !main.includes('다운로드 시작 실패')) {
  throw new Error('Download start failures must persist visible diagnostics instead of making the progress card disappear');
}
if (main.includes('planDownload(') || main.includes('planBatchDownload(') || main.includes('conflict_check.partial_failure_fallback') || main.includes('conflict_check.start')) {
  throw new Error('Primary download start must not make duplicate LMS preflight calls for conflict checking');
}
if (!main.includes('conflict_check.skipped_default_skip')) {
  throw new Error('Primary download conflict policy must be explicit: skip duplicate preflight and reuse existing files by default');
}
if (main.includes('다운로드 작업 취소') || main.includes('작업 상태 새로고침') || main.includes('<b>job</b>') || main.includes('<b>status</b>') || main.includes('byte progress') || main.includes('<b>dry_run</b>')) {
  throw new Error('Default download UI must hide developer-oriented job/status/dry-run fields');
}
if (main.includes('download-debug-details') || main.includes('<summary>고급 정보</summary>')) {
  throw new Error('Single-download completion UI must not show advanced diagnostics; offer opening the downloaded folder instead');
}
if (!main.includes('const firstDownloadableVideo') || !main.includes('setSelectedLectureIds([]);') || !main.includes('다운로드할 영상을 직접 선택하세요') || main.includes('[firstDownloadableVideo.module_item_id]') || main.includes('첫 번째 영상 항목을 선택했습니다')) {
  throw new Error('Course detail load must not auto-check a default video; users should explicitly select the lectures they want to download');
}
if (!main.includes("selectedCount === 0 ? '강의를 선택하세요'")) {
  throw new Error('Primary download button must not show a download-start label when no lecture is selected');
}
if (!api.includes('overwrite_artifacts: false')) {
  throw new Error('Main MP3/TXT artifact flow should reuse existing derived audio/transcript outputs by default');
}
if (!main.includes('artifactErrorText') || !main.includes('파일 생성 실패')) {
  throw new Error('Artifact failures must show the concrete file-generation error in the progress card');
}
if (!main.includes('selectedVideoItems') || !main.includes('selectableVideoIds') || !main.includes('effectiveSelectedIds') || !main.includes('영상/음성/자막 파일은 영상 강의에서만 만들 수 있습니다') || !main.includes("format === 'mp4' || format === 'mp3' || format === 'txt'") || !main.includes("lecture.kind !== 'video'") || !main.includes('영상만')) {
  throw new Error('MP4/MP3/TXT selection must target video lecture rows only, not downloadable PDFs/materials');
}
if (!main.includes('const canOpenDownloadedFolder') || !main.includes('handleOpenDownloadedFolder') || !main.includes('openDownloadedFolder(downloadView.downloadOutputPath)')) {
  throw new Error('Completed download UI must offer opening the downloaded folder');
}
if (!main.includes('missingSelectedOutputComponents') || !main.includes('canStartSelectedDownload') || !main.includes('selectedOutputRequiresMediaDependencies') || !main.includes('disabled={isDownloadActive ? Boolean(downloadJob?.cancel_requested) : !canStartSelectedDownload}')) {
  throw new Error('MP3/TXT downloads must be blocked when required media dependencies are missing, while MP4-only downloads can still start');
}
const capabilities = readFileSync('src-tauri/capabilities/default.json', 'utf8');
if (!main.includes('handleOpenOfficialViewer') || !main.includes('inline-watch-button') || !main.includes('시청 창을 앞으로 가져왔습니다') || !main.includes('재생/진도 처리는 숭실 LMS 공식 플레이어가 그대로 수행합니다') || !desktop.includes('openOfficialViewer') || !desktop.includes('WebviewWindow') || !desktop.includes('OFFICIAL_VIEWER_LABEL') || !desktop.includes('getByLabel(OFFICIAL_VIEWER_LABEL)') || !desktop.includes('setFocus()') || !tauriLib.includes('open_official_viewer_with_session') || tauriLib.includes('official_watch_autoplay_script()') || !capabilities.includes('core:webview:allow-create-webview-window')) {
  throw new Error('UI must open/focus the official Commons viewer in a single app-owned Tauri WebView window without injecting autoplay/debug scripts into the default watch flow');
}
if (!main.includes('handleStartSequentialWatch') || !main.includes('표시된 영상 순차 시청') || !main.includes('official-watch-ended') || !desktop.includes('openAssistedOfficialViewer') || !desktop.includes("invoke<void>('open_assisted_official_viewer'") || !tauriLib.includes('open_assisted_official_viewer') || !tauriLib.includes('initialization_script_for_all_frames(official_watch_assist_script())') || !tauriLib.includes('ssu-watch://ended') || !tauriLib.includes("video.addEventListener('ended'") || tauriLib.includes('TargetUrl')) {
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
if (main.includes('앱에서 공식 시청 창 열기')) {
  throw new Error('Default download panel must not duplicate the per-row watch action');
}
if (desktop.includes("invoke<void>('open_url_in_browser', { url })") || main.includes('기본 브라우저로 대체됩니다')) {
  throw new Error('Tauri watch flow must not silently fall back to the external browser; keep playback inside the app and report in-app viewer errors');
}
if (!main.includes('downloadProgressPercent') || !main.includes('aria-valuenow')) {
  throw new Error('Download progress must expose a percent value and accessible progressbar state');
}
if (!main.includes('setDownloadResult(null);') || !main.includes('downloadJob') || !main.includes('? downloadProgress.percent')) {
  throw new Error('Starting a new download must clear stale completed result state and prefer the active job percent');
}
if (!main.includes('downloadDir') || !main.includes('name="downloadDir"')) {
  throw new Error('UI must expose a download folder field');
}
if (main.includes('macOS Keychain에 저장했습니다') || main.includes('macOS Keychain 상태')) {
  throw new Error('UI must not frame Keychain as the primary user-facing session model');
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

// Derived progress: spot checks that must survive refactors.
{
  const rows = progress.fileProgressRows([
    { stage: 'download_started', module_item_id: 1, title: 'A', bytes_written: 0, total_bytes: 100 },
    { stage: 'download_progress', module_item_id: 1, bytes_written: 50, total_bytes: 100 },
    { stage: 'audio_progress', module_item_id: 2, title: 'B', progress_percent: 41.6 },
    { stage: 'video_playable_check_failed', module_item_id: 3 },
  ]);
  assert.deepEqual(rows.map((row) => [row.title, row.status, row.percent]), [['A', '다운로드 중', 50], ['B', 'MP3 변환 중', 42], ['파일 3', '실패', 0]]);
  assert.equal(progress.downloadStageLabel('running', [{ stage: 'download_progress' }]), '동시 다운로드 중');
  assert.equal(progress.latestUserStage([{ stage: 'video_playable_check_started' }]), '재생 가능한 MP4인지 확인하는 중입니다.');
  const view = progress.deriveDownloadView({
    downloadJob: { job_id: 'native-1', status: 'running', cancel_requested: false, progress: [{ stage: 'video_playable_check_started' }], result: null, error_code: null },
    batchResult: null, downloadResult: null, isStartingDownload: false, downloadStartError: null, downloadDir: '/dl',
  });
  assert.notEqual(view.downloadStatusLabel, '완료', 'running post-processing must not render as 완료');
  assert.equal(view.downloadOutputPath, '/dl');
}

console.log('frontend contract ok');
