import { readFileSync, existsSync } from 'node:fs';

const requiredFiles = [
  'src/main.tsx',
  'src/lib/api.ts',
  'src/lib/desktop.ts',
  'src/lib/types.ts',
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

const main = readFileSync('src/main.tsx', 'utf8');
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

if (!main.includes('setInterval') || !main.includes('fetchDownloadJob(downloadJob.job_id)')) {
  throw new Error('UI must auto-poll active download jobs');
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
const tauriLib = readFileSync('src-tauri/src/lib.rs', 'utf8');
for (const marker of ['isTauriRuntime', 'pickDownloadDirectory', 'openDownloadedFolder', 'defaultDownloadDirectory', 'backendSupervisionStatus', 'backendApiBaseUrl', 'downloadDir()', 'invoke<string>(\'real_download_dir\')', 'invoke<string>(\'backend_supervision_status\')', 'invoke<string>(\'backend_api_base_url\')', 'invoke<void>(\'open_path_in_finder\'', 'open({']) {
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
for (const marker of ['BackendManager', 'ensure_started', 'backend_health_check', 'backend_health_response_is_compatible', 'artifact_download_jobs', 'stt_provider_status', 'backend_launch_command', 'choose_backend_port', 'backend_api_base_url', 'uvicorn', 'backend_supervision_status']) {
  if (!tauriLib.includes(marker)) {
    throw new Error(`Tauri backend supervision missing marker: ${marker}`);
  }
}
if (!main.includes('앱 연결됨') || !main.includes('loadBackendMode')) {
  throw new Error('UI must expose a non-developer app connection state');
}
if (!tauriLib.includes('child.kill()') || !tauriLib.includes('mode: Mutex<String>') || !tauriLib.includes('port: Mutex<u16>')) {
  throw new Error('Tauri-managed backend must track supervision mode/dynamic port and clean up only its owned child process');
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
if (!main.includes('const canOpenDownloadedFolder') || !main.includes('handleOpenDownloadedFolder') || !main.includes('openDownloadedFolder(downloadOutputPath)')) {
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
if (!tauriLib.includes('official_viewer_autoplay_configuration') || !tauriLib.includes('setMediaTypesRequiringUserActionForPlayback(WKAudiovisualMediaTypes::None)') || !tauriLib.includes('with_webview_configuration(official_viewer_autoplay_configuration()?)')) {
  throw new Error('Sequential assisted official viewer must configure WKWebView media autoplay so script playback is not blocked by macOS user-gesture policy');
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
if (!tauriConfig.includes('resources/backend-sidecar') || !tauriLib.includes('packaged_backend_executable') || !tauriLib.includes('managed-sidecar') || !tauriLib.includes('SSU_BACKEND_EXECUTABLE')) {
  throw new Error('Tauri packaged builds must bundle and prefer the Python backend sidecar while retaining explicit sidecar smoke-test override');
}
if (!tauriConfig.includes('SSU Lecture Downloader') || !tauriConfig.includes('beforeDevCommand') || !tauriConfig.includes('dev-or-reuse-vite') || !tauriConfig.includes('frontendDist')) {
  throw new Error('Tauri config must define app identity, dev command, and frontend dist');
}
if (!tauriCargo.includes('tauri-plugin-dialog') || !tauriLib.includes('tauri_plugin_dialog::init')) {
  throw new Error('Tauri Rust app must initialize the dialog plugin');
}
if (!vite.includes("'/api'") || !vite.includes('127.0.0.1:8000') || !api.includes('backendApiBaseUrl') || !api.includes('await apiUrl(url)')) {
  throw new Error('Vite browser dev keeps /api -> 127.0.0.1:8000, while Tauri API calls use the dynamic backend base URL');
}

console.log('frontend contract ok');
