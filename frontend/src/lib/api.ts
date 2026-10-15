import type { AudioExtractionResult, BatchDownloadItemResult, BatchDownloadResult, ConflictStrategy, CourseDetailResponse, CoursesResponse, CredentialStatus, DownloadJobStatus, DownloadOutputFormat, DownloadResult, MediaDependencyInstallResult, MediaDependencyStatusResponse, NativeDownloadPlanResult, OfficialViewerPlanResult, OfficialViewerSessionBridgeResult, PostprocessJobStatus, ProgressEvent, STTProviderStatusResponse, TermsResponse, TranscriptionResult } from './types';
import {
  backendApiBaseUrl,
  cancelNativeDownloadJob,
  fetchNativeDownloadJob,
  isTauriRuntime,
  startNativeBatchDownloadJob,
  startNativeDownloadJob,
} from './desktop';

async function apiUrl(path: string): Promise<string> {
  const baseUrl = await backendApiBaseUrl();
  return baseUrl ? `${baseUrl}${path}` : path;
}

export class ApiError extends Error {
  constructor(public readonly code: string, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

const AUTHENTICATION_ERROR_CODES = new Set([
  'smartid_login_rejected',
  'authentication_failed',
  'missing_credentials',
  'session_expired',
  'HTTP 401',
  'HTTP 403',
  'sso_redirect_missing',
  'iframe_src_missing',
  'canvas_form_missing',
  'login_cryption_missing',
  'token_decryption_failed',
  'xn_api_token_missing',
  'unknown_response_shape',
  'lms_session_unverified',
]);

const nativeVideoPostprocessedJobIds = new Set<string>();

export function isAuthenticationError(error: unknown): error is ApiError {
  return error instanceof ApiError && AUTHENTICATION_ERROR_CODES.has(error.code);
}

function errorMessage(code: string): string {
  if (code === 'smartid_login_rejected') return '아이디 또는 비밀번호가 올바르지 않습니다.';
  if (code === 'sso_redirect_missing') return '숭실 SmartID 로그인은 응답했지만 LMS 이동 주소를 찾지 못했습니다. 아이디/비밀번호를 다시 확인하고, SmartID 추가 인증/비밀번호 변경 안내/공지 화면이 뜨는 계정인지 브라우저에서 먼저 확인해 주세요.';
  if (code === 'iframe_src_missing') return 'LMS 로그인 콜백은 열렸지만 Canvas 로그인 화면 주소를 찾지 못했습니다. 잠시 후 다시 시도하거나 브라우저에서 LMS 접속이 정상인지 확인해 주세요.';
  if (code === 'canvas_form_missing') return 'LMS Canvas 로그인 양식을 찾지 못했습니다. 숭실 LMS 로그인 화면 구조가 바뀌었을 수 있습니다.';
  if (code === 'login_cryption_missing' || code === 'token_decryption_failed') return 'LMS 로그인 토큰 처리에 실패했습니다. 숭실 LMS 로그인 방식이 바뀌었을 수 있습니다.';
  if (code === 'xn_api_token_missing') return 'LMS 세션 쿠키를 받지 못했습니다. 로그인은 진행됐지만 강의 API 세션 생성에 실패했습니다.';
  if (code === 'unknown_response_shape') return '숭실 LMS 로그인 응답을 해석하지 못했습니다. 계정 추가 인증/공지/비밀번호 변경 화면이 끼어 있는지 브라우저에서 확인해 주세요.';
  if (code === 'lms_session_unverified') return '저장된 로그인 정보로 LMS 과목 목록을 확인하지 못했습니다. 다시 로그인해 주세요.';
  if (code === 'authentication_failed') return '숭실 LMS 인증에 실패했습니다. 로그인 정보를 다시 확인해 주세요.';
  if (code === 'session_expired') return '로그인 세션이 만료되었습니다. 다시 로그인해 주세요.';
  if (code === 'missing_credentials') return '로그인이 필요합니다.';
  if (code === 'stt_provider_not_installed') return 'TXT 생성에 필요한 STT 엔진이 설치되어 있지 않습니다. faster-whisper를 설치한 뒤 다시 시도해 주세요.';
  if (code === 'HTTP 404') return '현재 연결된 API 서버가 최신 기능을 지원하지 않습니다. 앱/API 서버를 재시작해 주세요.';
  return `처리 중 오류가 발생했습니다: ${code}`;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(await apiUrl(url), {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
  });
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const code = payload?.error_code ?? `HTTP ${response.status}`;
    throw new ApiError(code, payload?.message ?? errorMessage(code));
  }
  if (payload && typeof payload === 'object' && 'ok' in payload && payload.ok === false) {
    const code = payload.error_code ?? 'api_error';
    throw new ApiError(code, payload?.message ?? errorMessage(code));
  }
  return payload as T;
}

export function fetchCredentialStatus(): Promise<CredentialStatus> {
  return requestJson<CredentialStatus>('/api/credentials/status');
}

export function saveCredentials(username: string, password: string): Promise<CredentialStatus> {
  return requestJson<CredentialStatus>('/api/credentials', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export function deleteCredentials(): Promise<CredentialStatus> {
  return requestJson<CredentialStatus>('/api/credentials', { method: 'DELETE' });
}

export function fetchTerms(): Promise<TermsResponse> {
  return requestJson<TermsResponse>('/api/terms');
}

export function fetchCourses(termId?: number | null): Promise<CoursesResponse> {
  const params = new URLSearchParams();
  if (termId !== undefined && termId !== null) params.set('term_id', String(termId));
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return requestJson<CoursesResponse>(`/api/courses${suffix}`);
}

export function fetchCourseDetail(courseId: string | number, courseName: string): Promise<CourseDetailResponse> {
  const params = new URLSearchParams();
  if (courseName) params.set('course_name', courseName);
  return requestJson<CourseDetailResponse>(`/api/courses/${courseId}?${params.toString()}`);
}

function downloadPayload(courseId: string | number, moduleItemId: string | number, courseName: string, outputDir: string, conflictStrategy: ConflictStrategy = 'rename') {
  return JSON.stringify({
    course_id: courseId,
    module_item_id: moduleItemId,
    course_name: courseName,
    output_dir: outputDir.trim() || undefined,
    conflict_strategy: conflictStrategy,
  });
}

export function planDownload(courseId: string | number, moduleItemId: string | number, courseName: string, outputDir: string, conflictStrategy: ConflictStrategy = 'rename'): Promise<DownloadResult> {
  return requestJson<DownloadResult>('/api/download/plan', {
    method: 'POST',
    body: downloadPayload(courseId, moduleItemId, courseName, outputDir, conflictStrategy),
  });
}

function planNativeDownload(courseId: string | number, moduleItemId: string | number, courseName: string, outputDir: string, conflictStrategy: ConflictStrategy = 'rename'): Promise<NativeDownloadPlanResult> {
  return requestJson<NativeDownloadPlanResult>('/api/download/native-plan', {
    method: 'POST',
    body: downloadPayload(courseId, moduleItemId, courseName, outputDir, conflictStrategy),
  });
}

export function planOfficialViewer(courseId: string | number, moduleItemId: string | number, courseName: string): Promise<OfficialViewerPlanResult> {
  return requestJson<OfficialViewerPlanResult>('/api/watch/viewer-plan', {
    method: 'POST',
    body: downloadPayload(courseId, moduleItemId, courseName, '', 'rename'),
  });
}

export function planOfficialViewerSessionBridge(courseId: string | number, moduleItemId: string | number, courseName: string): Promise<OfficialViewerSessionBridgeResult> {
  return requestJson<OfficialViewerSessionBridgeResult>('/api/watch/session-bridge-plan', {
    method: 'POST',
    body: downloadPayload(courseId, moduleItemId, courseName, '', 'rename'),
  });
}

export function downloadLecture(courseId: string | number, moduleItemId: string | number, courseName: string, outputDir: string, conflictStrategy: ConflictStrategy = 'rename'): Promise<DownloadResult> {
  return requestJson<DownloadResult>('/api/download', {
    method: 'POST',
    body: downloadPayload(courseId, moduleItemId, courseName, outputDir, conflictStrategy),
  });
}

async function ensurePlayableVideo(inputPath: string): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>('/api/media/video/ensure-playable', {
    method: 'POST',
    body: JSON.stringify({ input_path: inputPath, overwrite: true }),
  });
}

function isSingleDownloadResult(result: DownloadJobStatus['result']): result is DownloadResult {
  return Boolean(result && typeof result === 'object' && 'output_path' in result && 'module_item_id' in result);
}

function isBatchDownloadResult(result: DownloadJobStatus['result']): result is BatchDownloadResult {
  return Boolean(result && typeof result === 'object' && 'items' in result && Array.isArray(result.items));
}

async function postprocessNativeVideoItem(item: DownloadResult | BatchDownloadItemResult, progress: ProgressEvent[]): Promise<{ item: DownloadResult | BatchDownloadItemResult; ok: boolean }> {
  const outputPath = typeof item.output_path === 'string' ? item.output_path : null;
  if (!item.ok || !outputPath || !outputPath.toLowerCase().endsWith('.mp4')) return { item, ok: item.ok };
  progress.push({ stage: 'video_playable_check_started', output_path: outputPath, module_item_id: item.module_item_id, title: item.title ?? undefined });
  try {
    const video = await ensurePlayableVideo(outputPath);
    const updated = {
      ...item,
      output_path: typeof video.output_path === 'string' ? video.output_path : outputPath,
      file_size: typeof video.file_size === 'number' ? video.file_size : item.file_size,
      playable_checked: true,
      reconstructed: video.reconstructed === true,
      source_kind: typeof video.source_kind === 'string' ? video.source_kind : undefined,
    };
    const doneEvent: ProgressEvent = {
      stage: video.reconstructed === true ? 'video_reconstructed' : 'video_playable_check_done',
      output_path: outputPath,
      module_item_id: item.module_item_id,
      title: item.title ?? undefined,
    };
    if (typeof updated.file_size === 'number') doneEvent.file_size = updated.file_size;
    progress.push(doneEvent);
    return { item: updated, ok: true };
  } catch (error) {
    const code = error instanceof ApiError ? error.code : 'video_reconstruction_failed';
    progress.push({ stage: 'video_playable_check_failed', output_path: outputPath, module_item_id: item.module_item_id, title: item.title ?? undefined, error_code: code });
    return { item: { ...item, ok: false, error_code: code, playable_checked: true }, ok: false };
  }
}

async function withNativeVideoPostprocess(job: DownloadJobStatus): Promise<DownloadJobStatus> {
  if (!isTauriRuntime() || !job.job_id.startsWith('native-') || job.status !== 'succeeded') return job;
  if (nativeVideoPostprocessedJobIds.has(job.job_id)) return job;
  nativeVideoPostprocessedJobIds.add(job.job_id);
  const progress: ProgressEvent[] = [...job.progress];
  if (isSingleDownloadResult(job.result)) {
    const processed = await postprocessNativeVideoItem(job.result, progress);
    return {
      ...job,
      status: processed.ok ? 'succeeded' : 'failed',
      error_code: processed.ok ? job.error_code : processed.item.error_code,
      result: processed.item as DownloadResult,
      progress,
    };
  }
  if (isBatchDownloadResult(job.result)) {
    const items: BatchDownloadItemResult[] = [];
    for (const item of job.result.items) {
      const processed = await postprocessNativeVideoItem(item, progress);
      items.push(processed.item as BatchDownloadItemResult);
    }
    const successCount = items.filter((item) => item.ok).length;
    const failureCount = items.length - successCount;
    return {
      ...job,
      status: failureCount === 0 ? 'succeeded' : 'failed',
      error_code: failureCount === 0 ? null : 'partial_failure',
      result: {
        ...job.result,
        ok: failureCount === 0,
        error_code: failureCount === 0 ? null : 'partial_failure',
        success_count: successCount,
        failure_count: failureCount,
        items,
      },
      progress,
    };
  }
  return job;
}

export async function startDownloadJob(courseId: string | number, moduleItemId: string | number, courseName: string, outputDir: string, conflictStrategy: ConflictStrategy = 'rename'): Promise<DownloadJobStatus> {
  if (isTauriRuntime()) {
    try {
      const plan = await planNativeDownload(courseId, moduleItemId, courseName, outputDir, conflictStrategy);
      return withNativeVideoPostprocess(await startNativeDownloadJob(plan));
    } catch (error) {
      const code = error instanceof ApiError ? error.code : 'native_plan_failed';
      const message = error instanceof ApiError
        ? `선택한 영상의 다운로드 주소를 준비하지 못했습니다. ${error.message}`
        : '선택한 영상의 다운로드 주소를 준비하지 못했습니다.';
      throw new ApiError(
        `native_plan_failed:${code}`,
        message
      );
    }
  }
  return requestJson<DownloadJobStatus>('/api/download/jobs', {
    method: 'POST',
    body: downloadPayload(courseId, moduleItemId, courseName, outputDir, conflictStrategy),
  });
}

export async function startBatchDownloadJob(courseId: string | number, courseName: string, moduleItemIds: Array<string | number>, outputDir: string, conflictStrategy: ConflictStrategy = 'rename'): Promise<DownloadJobStatus> {
  if (isTauriRuntime()) {
    const plans: NativeDownloadPlanResult[] = [];
    for (let index = 0; index < moduleItemIds.length; index += 1) {
      const moduleItemId = moduleItemIds[index];
      try {
        plans.push(await planNativeDownload(courseId, moduleItemId, courseName, outputDir, conflictStrategy));
      } catch (error) {
        const code = error instanceof ApiError ? error.code : 'native_plan_failed';
        const message = error instanceof ApiError
          ? `선택한 ${index + 1}번째 영상의 다운로드 주소를 준비하지 못했습니다. ${error.message}`
          : `선택한 ${index + 1}번째 영상의 다운로드 주소를 준비하지 못했습니다.`;
        throw new ApiError(
          `native_batch_plan_failed:${code}`,
          message
        );
      }
    }
    return withNativeVideoPostprocess(await startNativeBatchDownloadJob(courseId, courseName, plans));
  }
  return requestJson<DownloadJobStatus>('/api/download/batch/jobs', {
    method: 'POST',
    body: batchPayload(courseId, courseName, 'selected', outputDir, moduleItemIds, conflictStrategy),
  });
}


export async function startArtifactDownloadJob(courseId: string | number, courseName: string, moduleItemIds: Array<string | number>, outputDir: string, outputs: DownloadOutputFormat[], conflictStrategy: ConflictStrategy = 'skip'): Promise<DownloadJobStatus> {
  const job = await requestJson<DownloadJobStatus>('/api/download/artifacts/jobs', {
    method: 'POST',
    body: JSON.stringify({
      course_id: courseId,
      course_name: courseName,
      mode: 'selected',
      module_item_ids: moduleItemIds,
      output_dir: outputDir.trim() || undefined,
      conflict_strategy: conflictStrategy,
      outputs,
      audio_format: 'mp3',
      stt_provider: 'faster_whisper',
      stt_model: 'large-v3',
      stt_language: 'ko',
      overwrite_artifacts: false,
    }),
  });
  return withArtifactJobId(job);
}

function withArtifactJobId(job: DownloadJobStatus): DownloadJobStatus {
  return job.job_id.startsWith('artifact-') ? job : { ...job, job_id: `artifact-${job.job_id}` };
}

export async function fetchDownloadJob(jobId: string): Promise<DownloadJobStatus> {
  if (isTauriRuntime() && jobId.startsWith('native-')) {
    return withNativeVideoPostprocess(await fetchNativeDownloadJob(jobId));
  }
  if (jobId.startsWith('artifact-')) {
    const job = await requestJson<DownloadJobStatus>(`/api/download/artifacts/jobs/${jobId.slice('artifact-'.length)}`);
    return withArtifactJobId(job);
  }
  return requestJson<DownloadJobStatus>(`/api/download/jobs/${jobId}`);
}

export async function cancelDownloadJob(jobId: string): Promise<DownloadJobStatus> {
  if (isTauriRuntime() && jobId.startsWith('native-')) {
    return cancelNativeDownloadJob(jobId);
  }
  if (jobId.startsWith('artifact-')) {
    const job = await requestJson<DownloadJobStatus>(`/api/download/artifacts/jobs/${jobId.slice('artifact-'.length)}/cancel`, { method: 'POST' });
    return withArtifactJobId(job);
  }
  return requestJson<DownloadJobStatus>(`/api/download/jobs/${jobId}/cancel`, { method: 'POST' });
}

function batchPayload(courseId: string | number, courseName: string, mode: string, outputDir: string, moduleItemIds?: Array<string | number>, conflictStrategy: ConflictStrategy = 'rename') {
  return JSON.stringify({
    course_id: courseId,
    course_name: courseName,
    mode,
    module_item_ids: moduleItemIds,
    output_dir: outputDir.trim() || undefined,
    conflict_strategy: conflictStrategy,
  });
}

export function planBatchDownload(courseId: string | number, courseName: string, moduleItemIds: Array<string | number>, outputDir: string, conflictStrategy: ConflictStrategy = 'rename'): Promise<BatchDownloadResult> {
  return requestJson<BatchDownloadResult>('/api/download/batch/plan', {
    method: 'POST',
    body: batchPayload(courseId, courseName, 'selected', outputDir, moduleItemIds, conflictStrategy),
  });
}

export function downloadBatch(courseId: string | number, courseName: string, moduleItemIds: Array<string | number>, outputDir: string, conflictStrategy: ConflictStrategy = 'rename'): Promise<BatchDownloadResult> {
  return requestJson<BatchDownloadResult>('/api/download/batch', {
    method: 'POST',
    body: batchPayload(courseId, courseName, 'selected', outputDir, moduleItemIds, conflictStrategy),
  });
}

export function fetchSttProviderStatus(): Promise<STTProviderStatusResponse> {
  return requestJson<STTProviderStatusResponse>('/api/stt/providers/status');
}

export function fetchMediaDependencyStatus(): Promise<MediaDependencyStatusResponse> {
  return requestJson<MediaDependencyStatusResponse>('/api/media/dependencies/status');
}

export function startMediaDependencyInstallJob(components: string[]): Promise<PostprocessJobStatus<MediaDependencyInstallResult>> {
  return requestJson<PostprocessJobStatus<MediaDependencyInstallResult>>('/api/media/dependencies/install/jobs', {
    method: 'POST',
    body: JSON.stringify({ components }),
  });
}

export function fetchMediaDependencyInstallJob(jobId: string): Promise<PostprocessJobStatus<MediaDependencyInstallResult>> {
  return requestJson<PostprocessJobStatus<MediaDependencyInstallResult>>(`/api/media/dependencies/install/jobs/${jobId}`);
}

export function startAudioExtractionJob(inputPath: string, format: 'wav' | 'm4a' = 'wav', overwrite = false): Promise<PostprocessJobStatus<AudioExtractionResult>> {
  return requestJson<PostprocessJobStatus<AudioExtractionResult>>('/api/media/audio/jobs', {
    method: 'POST',
    body: JSON.stringify({ input_path: inputPath, format, overwrite }),
  });
}

export function fetchAudioExtractionJob(jobId: string): Promise<PostprocessJobStatus<AudioExtractionResult>> {
  return requestJson<PostprocessJobStatus<AudioExtractionResult>>(`/api/media/audio/jobs/${jobId}`);
}

export function startSttJob(audioPath: string, provider = 'faster_whisper', model = 'large-v3', overwrite = false): Promise<PostprocessJobStatus<TranscriptionResult>> {
  return requestJson<PostprocessJobStatus<TranscriptionResult>>('/api/stt/jobs', {
    method: 'POST',
    body: JSON.stringify({ audio_path: audioPath, provider, model, language: 'ko', overwrite }),
  });
}

export function fetchSttJob(jobId: string): Promise<PostprocessJobStatus<TranscriptionResult>> {
  return requestJson<PostprocessJobStatus<TranscriptionResult>>(`/api/stt/jobs/${jobId}`);
}

export function planUncompletedBatch(courseId: string | number, courseName: string, outputDir: string, conflictStrategy: ConflictStrategy = 'rename'): Promise<BatchDownloadResult> {
  return requestJson<BatchDownloadResult>('/api/download/batch/plan', {
    method: 'POST',
    body: batchPayload(courseId, courseName, 'uncompleted', outputDir, undefined, conflictStrategy),
  });
}
