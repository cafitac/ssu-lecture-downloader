/**
 * Plain-Korean text for backend / native error codes. The UI never shows a raw
 * code; unknown codes fall back to a generic sentence (and are logged for debugging).
 * This module is pure so Node can load it in the contract tests.
 */

export const GENERIC_ERROR = '문제가 생겼어요. 잠시 후 다시 시도해 주세요.';
export const SESSION_EXPIRED_MESSAGE = '로그인이 만료됐어요. 다시 로그인해 주세요.';

const LOGIN_FLOW_CHANGED = '숭실 LMS 로그인 방식이 바뀐 것 같아요. 잠시 후 다시 시도하고, 계속되면 앱 업데이트가 필요할 수 있어요.';
const LOGIN_BLOCKED = '로그인을 마치지 못했어요. SmartID 추가 인증이나 비밀번호 변경 안내가 떠 있는지 브라우저에서 확인한 뒤 다시 시도해 주세요.';
const NETWORK = '인터넷 연결이 불안정하거나 숭실 LMS에 닿지 못했어요. 연결을 확인하고 다시 시도해 주세요.';
const SOURCE_MISSING = '변환할 영상 파일을 찾지 못했어요. 먼저 영상을 받아 주세요.';
const NO_STREAM = '이 강의의 영상 주소를 찾지 못했어요. 아직 열리지 않은 강의일 수 있어요.';
const SAVE_FAILED = '파일을 저장하지 못했어요. 저장 폴더와 디스크 공간을 확인해 주세요.';
const JOB_LOST = '작업 정보를 찾지 못했어요. 앱을 다시 시작해 주세요.';

const KNOWN: Record<string, string> = {
  network_error: NETWORK,
  network_timeout: '숭실 LMS 응답이 너무 늦어요. 잠시 후 다시 시도해 주세요.',
  session_expired: SESSION_EXPIRED_MESSAGE,
  lms_session_unverified: SESSION_EXPIRED_MESSAGE,
  missing_credentials: '로그인이 필요해요.',
  authentication_failed: '숭실 LMS 인증에 실패했어요. 로그인 정보를 확인해 주세요.',
  smartid_login_rejected: '아이디 또는 비밀번호가 올바르지 않아요.',
  invalid_credentials_payload: '아이디와 비밀번호를 입력해 주세요.',
  sso_redirect_missing: LOGIN_BLOCKED,
  unknown_response_shape: LOGIN_BLOCKED,
  iframe_src_missing: LOGIN_FLOW_CHANGED,
  canvas_form_missing: LOGIN_FLOW_CHANGED,
  login_cryption_missing: LOGIN_FLOW_CHANGED,
  token_decryption_failed: LOGIN_FLOW_CHANGED,
  xn_api_token_missing: LOGIN_FLOW_CHANGED,
  lti_launch_action_missing: '강의 화면을 열지 못했어요. 잠시 후 다시 시도해 주세요.',
  lti_launch_form_missing: '강의 화면을 열지 못했어요. 잠시 후 다시 시도해 주세요.',
  media_path_not_allowed: '이 앱으로 받은 파일만 변환할 수 있어요. 저장 폴더 안의 파일인지 확인해 주세요.',
  internal_error: '앱 안에서 문제가 생겼어요. 다시 시도해 주세요.',
  service_error: GENERIC_ERROR,
  api_error: GENERIC_ERROR,
  grades_consent_required: '성적 보기를 켜야 성적을 읽어 올 수 있어요.',
  grades_login_rejected: 'u-SAINT가 로그인을 받지 않았어요. u-SAINT 웹에서 직접 로그인되는지 확인해 주세요.',
  grades_unavailable: 'u-SAINT에 연결하지 못했어요. 강의 기능은 그대로 쓸 수 있어요.',
  invalid_consent_version: '동의 안내가 바뀌었어요. 화면을 다시 열어 주세요.',
  invalid_semester: '학기 정보를 읽지 못했어요.',
  missing_batch_selection: '받을 항목을 먼저 선택해 주세요.',
  not_implemented_yet: '아직 지원하지 않는 기능이에요.',
  stt_provider_not_installed: '자막을 만드는 구성요소가 설치되어 있지 않아요. 설정에서 설치해 주세요.',
  stt_provider_not_implemented: '선택한 자막 엔진은 아직 지원하지 않아요.',
  transcript_exists: '이미 만들어진 자막 파일이 있어요.',
  transcript_write_failed: '자막 파일을 저장하지 못했어요. 저장 폴더를 확인해 주세요.',
  stt_failed: '자막을 만들지 못했어요.',
  stt_remote_not_configured: '원격 자막 서버 주소를 먼저 설정해 주세요.',
  stt_remote_unreachable: '원격 자막 서버에 연결하지 못했어요. 서버가 켜져 있는지, SSH 터널이 열리는지 설정에서 연결 확인을 해 주세요.',
  stt_remote_timeout: '원격 자막 서버가 시간 안에 응답하지 않았어요. 잠시 뒤 다시 시도해 주세요.',
  stt_remote_http_error: '원격 자막 서버가 오류를 돌려줬어요. 서버 상태를 확인해 주세요.',
  stt_remote_bad_response: '원격 자막 서버의 응답을 읽지 못했어요.',
  stt_remote_auth_failed: '원격 자막 서버가 API 키를 받아들이지 않았어요. 설정에서 키를 확인해 주세요.',
  stt_remote_job_lost: '자막 서버에서 작업을 찾지 못했어요. 다시 시도해 주세요.',
  stt_remote_upload_too_large: '음성 파일이 서버 허용 크기보다 커요.',
  stt_remote_bad_request: '자막 서버가 이 음성 파일을 받지 않았어요.',
  stt_empty_result: '원격 자막 서버가 아무 내용도 돌려주지 않았어요.',
  ssh_not_found: 'ssh 실행 파일을 찾지 못했어요.',
  ssh_start_failed: 'SSH 터널을 시작하지 못했어요.',
  ssh_tunnel_failed: 'SSH 터널을 열지 못했어요. 호스트 이름과 SSH 키 설정을 확인해 주세요.',
  ssh_tunnel_timeout: 'SSH 터널이 시간 안에 열리지 않았어요. 네트워크와 호스트를 확인해 주세요.',
  invalid_ssh_host: 'SSH 호스트 이름이 올바르지 않아요. 공백이나 -로 시작하는 이름은 쓸 수 없어요.',
  invalid_port: '포트 번호는 1부터 65535 사이여야 해요.',
  invalid_stt_base_url: '서버 주소는 http:// 또는 https://로 시작해야 해요.',
  invalid_stt_kind: '지원하지 않는 서버 종류예요.',
  missing_stt_base_url: '서버 주소나 SSH 터널 호스트를 먼저 입력해 주세요.',
  ffmpeg_not_found: '음성 변환 구성요소를 찾지 못했어요. 설정에서 확인해 주세요.',
  audio_extraction_failed: '음성을 추출하지 못했어요.',
  audio_not_found: '음성 파일을 찾지 못했어요.',
  audio_not_file: '음성 파일을 찾지 못했어요.',
  input_not_found: SOURCE_MISSING,
  source_video_not_found: SOURCE_MISSING,
  video_not_found: SOURCE_MISSING,
  video_reconstruction_failed: '받은 영상을 재생 가능한 파일로 만들지 못했어요.',
  dependency_install_failed: '구성요소를 설치하지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.',
  dependency_install_timeout: '구성요소 설치가 너무 오래 걸려 멈췄어요. 다시 시도해 주세요.',
  dependency_auto_install_unavailable: '이 환경에서는 구성요소를 자동으로 설치할 수 없어요.',
  download_failed: '파일을 받지 못했어요. 다시 시도해 주세요.',
  download_plan_failed: '파일을 받을 준비를 하지 못했어요. 다시 시도해 주세요.',
  canvas_file_unavailable: '이 파일은 LMS에서 받을 수 없어요. 권한이 없거나 삭제된 파일일 수 있어요.',
  invalid_page_ref: '이 페이지를 찾을 수 없어요.',
  invalid_file_id: '이 파일을 찾을 수 없어요.',
  invalid_course_id: '이 과목을 찾을 수 없어요.',
  commons_content_id_missing: NO_STREAM,
  commons_download_missing: NO_STREAM,
  commons_download_url_missing: NO_STREAM,
  content_id_missing: NO_STREAM,
  media_content_id_missing: NO_STREAM,
  file_exists: '같은 이름의 파일이 이미 있어요.',
  partial_failure: '일부 파일을 받지 못했어요.',
  cancelled: '취소했어요.',
  DownloadCancelled: '취소했어요.',
  download_job_not_found: JOB_LOST,
  artifact_job_not_found: JOB_LOST,
  audio_job_not_found: JOB_LOST,
  stt_job_not_found: JOB_LOST,
  dependency_install_job_not_found: JOB_LOST,
  native_download_job_not_found: JOB_LOST,
  chrome_not_found: 'Google Chrome을 찾지 못했어요. 설정의 자동 재생 방식에서 ‘앱 안의 작은 창’을 골라 주세요.',
  chrome_watch_busy: '이미 자동 재생이 진행 중이에요.',
  update_in_progress: '업데이트를 설치하는 중이에요. 잠시 후 다시 시도해 주세요.',
  empty_watch_queue: '순서대로 시청할 영상이 없어요.',
  chrome_launch_failed: 'Chrome을 실행하지 못했어요.',
  chrome_launch_timeout: 'Chrome 창이 열리지 않았어요. 다시 시도해 주세요.',
  chrome_exited: 'Chrome이 바로 종료됐어요. 다시 시도해 주세요.',
  chrome_connect_failed: 'Chrome에 연결하지 못했어요. 다시 시도해 주세요.',
  chrome_navigation_failed: '강의 페이지를 열지 못했어요.',
  chrome_window_closed: 'Chrome 창이 닫혀 자동 재생을 멈췄어요.',
  chrome_closed: 'Chrome과의 연결이 끊겨 자동 재생을 멈췄어요.',
  chrome_window_missing: 'Chrome 창이 열려 있지 않아요.',
  chrome_watch_error: '자동 재생 중 예상하지 못한 오류가 생겼어요.',
};

const KNOWN_PREFIXES: Array<[string, string]> = [
  ['download dir create failed', '저장 폴더를 만들지 못했어요. 폴더 권한을 확인해 주세요.'],
  ['partial file', SAVE_FAILED],
  ['download finalize failed', SAVE_FAILED],
  ['download stat failed', SAVE_FAILED],
  ['native download write failed', SAVE_FAILED],
  ['native download read failed', NETWORK],
  ['native download request failed', NETWORK],
  ['native download client failed', GENERIC_ERROR],
  ['lms_request_failed', '숭실 LMS가 요청을 받아주지 않았어요. 잠시 후 다시 시도해 주세요.'],
];

const HANGUL = /[가-힣]/;
const NETWORK_MESSAGE = /failed to fetch|load failed|networkerror|network request failed/i;

function codeOf(error: unknown): string | null {
  if (error && typeof error === 'object' && 'code' in error && typeof (error as { code: unknown }).code === 'string') {
    return (error as { code: string }).code;
  }
  return null;
}

/** Friendly text for a known code, or `null` when the code is not recognised. */
export function knownErrorText(code: string | null | undefined): string | null {
  if (!code) return null;
  if (Object.hasOwn(KNOWN, code)) return KNOWN[code];
  const http = /^HTTP (\d{3})$/.exec(code);
  if (http) {
    const status = Number(http[1]);
    if (status === 401 || status === 403) return SESSION_EXPIRED_MESSAGE;
    if (status === 404) return '앱 서버가 이 기능을 지원하지 않아요. 앱을 다시 시작해 주세요.';
    if (status === 410) return '다운로드 주소가 만료됐어요. 다시 시도해 주세요.';
    if (status >= 500) return '서버에서 문제가 생겼어요. 잠시 후 다시 시도해 주세요.';
    return GENERIC_ERROR;
  }
  const planPrefix = /^native_(?:batch_)?plan_failed:(.*)$/.exec(code);
  if (planPrefix) {
    const inner = knownErrorText(planPrefix[1]);
    return `다운로드 주소를 준비하지 못했어요.${inner ? ` ${inner}` : ''}`;
  }
  for (const [prefix, text] of KNOWN_PREFIXES) {
    if (code.startsWith(prefix)) return text;
  }
  return null;
}

/** Text for an error code stored on a job or item; unknown codes get the generic sentence. */
export function describeErrorCode(code: string | null | undefined, fallback: string = GENERIC_ERROR): string {
  const known = knownErrorText(code);
  if (known) return known;
  if (code) console.warn('[ssu] unmapped error code', code);
  return fallback;
}

/** Text for a thrown value (ApiError, Error, Tauri string rejection). Never exposes a raw code. */
export function friendlyError(error: unknown, fallback: string = GENERIC_ERROR): string {
  const code = codeOf(error);
  if (code) return describeErrorCode(code, fallback);
  const message = typeof error === 'string' ? error : error instanceof Error ? error.message : '';
  if (message && NETWORK_MESSAGE.test(message)) return NETWORK;
  // Tauri commands reject with Korean sentences written for the user; show those as they are.
  if (message && HANGUL.test(message) && message.length <= 160) return message;
  if (message) console.warn('[ssu] unmapped error', message);
  return fallback;
}
