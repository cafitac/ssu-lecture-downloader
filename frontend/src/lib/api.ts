import type { AudioExtractionResult, ChromeWatchItemInput, ChromeWatchStatus, BatchDownloadItemResult, BatchDownloadResult, CanvasFileDownloadResponse, ConflictStrategy, CourseDetailResponse, CoursesResponse, CredentialStatus, DeadlinesResponse, DownloadJobStatus, DownloadOutputFormat, DownloadResult, LmsSessionBridgeResponse, MediaDependencyInstallResult, MediaDependencyStatusResponse, NativeDownloadPlanResult, OfficialViewerPlanResult, OfficialViewerSessionBridgeResult, PageFilesResponse, PostprocessJobStatus, ProgressEvent, RemoteSttConfigPatch, RemoteSttStatus, STTProviderStatusResponse, TermsResponse, TidyJobKind, TidyJobStatus, TidyRun, TranscriptionResult } from './types';
import type { GradesClassesResponse, GradesOverviewResponse, GradesSemesterKind, GradesStatusResponse } from './types';
import { buildRequestHeaders } from './http';
import { reportWithPostprocess } from './postprocess';
import type { PostprocessEntry } from './postprocess';
import {
  backendApiConnection,
  cancelNativeDownloadJob,
  fetchNativeDownloadJob,
  isTauriRuntime,
  startNativeBatchDownloadJob,
  startNativeDownloadJob,
} from './desktop';

async function apiUrl(path: string): Promise<string> {
  const { baseUrl } = await backendApiConnection();
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
  const connection = await backendApiConnection();
  const response = await fetch(await apiUrl(url), {
    ...init,
    headers: buildRequestHeaders(connection.token, init?.headers),
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

export function fetchTerms(refresh = false): Promise<TermsResponse> {
  return requestJson<TermsResponse>(refresh ? '/api/terms?refresh=true' : '/api/terms');
}

export function fetchCourses(termId?: number | null, refresh = false): Promise<CoursesResponse> {
  const params = new URLSearchParams();
  if (termId !== undefined && termId !== null) params.set('term_id', String(termId));
  if (refresh) params.set('refresh', 'true');
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return requestJson<CoursesResponse>(`/api/courses${suffix}`);
}

export function fetchDeadlines(termId?: number | null, refresh = false): Promise<DeadlinesResponse> {
  const params = new URLSearchParams();
  if (termId !== undefined && termId !== null) params.set('term_id', String(termId));
  if (refresh) params.set('refresh', 'true');
  const suffix = params.toString() ? `?${params.toString()}` : '';
  return requestJson<DeadlinesResponse>(`/api/deadlines${suffix}`);
}

// Grades (u-SAINT, read only). These calls are made only after the user opened the grades tab;
// their failures are the grades tab's own and never end the LMS login (see lib/grades.ts).
export function fetchGradesStatus(): Promise<GradesStatusResponse> {
  return requestJson<GradesStatusResponse>('/api/grades/status');
}

export function grantGradesConsent(consentVersion: number): Promise<GradesStatusResponse> {
  return requestJson<GradesStatusResponse>('/api/grades/consent', {
    method: 'PUT',
    body: JSON.stringify({ consent_version: consentVersion }),
  });
}

export function revokeGradesConsent(): Promise<GradesStatusResponse> {
  return requestJson<GradesStatusResponse>('/api/grades/consent', { method: 'DELETE' });
}

export function fetchGrades(refresh = false): Promise<GradesOverviewResponse> {
  return requestJson<GradesOverviewResponse>(`/api/grades?refresh=${refresh ? 'true' : 'false'}`);
}

export function fetchGradeClasses(year: number, semester: GradesSemesterKind, refresh = false): Promise<GradesClassesResponse> {
  const params = new URLSearchParams({ year: String(year), semester, refresh: refresh ? 'true' : 'false' });
  return requestJson<GradesClassesResponse>(`/api/grades/classes?${params.toString()}`);
}

export function fetchCourseDetail(courseId: string | number, courseName: string, options: { refresh?: boolean; outputDir?: string } = {}): Promise<CourseDetailResponse> {
  const params = new URLSearchParams();
  if (courseName) params.set('course_name', courseName);
  if (options.outputDir) params.set('output_dir', options.outputDir);
  if (options.refresh) params.set('refresh', 'true');
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

// ------------------------------------------------------------ Chrome window playback

/** Starts the queue in a dedicated Chrome window driven by the backend. */
export function startChromeWatch(items: ChromeWatchItemInput[]): Promise<ChromeWatchStatus> {
  return requestJson<ChromeWatchStatus>('/api/watch/chrome/start', { method: 'POST', body: JSON.stringify({ items }) });
}

export function fetchChromeWatchStatus(): Promise<ChromeWatchStatus> {
  return requestJson<ChromeWatchStatus>('/api/watch/chrome/status');
}

export function nextChromeWatch(): Promise<ChromeWatchStatus> {
  return requestJson<ChromeWatchStatus>('/api/watch/chrome/next', { method: 'POST' });
}

export function stopChromeWatch(): Promise<ChromeWatchStatus> {
  return requestJson<ChromeWatchStatus>('/api/watch/chrome/stop', { method: 'POST' });
}

/** Brings the Chrome window back (un-minimizes and raises it). */
export function showChromeWatch(): Promise<ChromeWatchStatus> {
  return requestJson<ChromeWatchStatus>('/api/watch/chrome/show', { method: 'POST' });
}

/** LMS cookies for an in-app page window (the desktop shell injects them into the new web view). */
export function fetchLmsSessionBridge(): Promise<LmsSessionBridgeResponse> {
  return requestJson<LmsSessionBridgeResponse>('/api/lms/session-bridge', { method: 'POST' });
}

/** Files linked from a wiki page ("1주차 강의자료"), each with name, size and download state. */
export function fetchPageFiles(courseId: string | number, pageRef: string, courseName: string, outputDir: string): Promise<PageFilesResponse> {
  const params = new URLSearchParams();
  if (courseName) params.set('course_name', courseName);
  if (outputDir.trim()) params.set('output_dir', outputDir.trim());
  return requestJson<PageFilesResponse>(`/api/courses/${encodeURIComponent(String(courseId))}/pages/${encodeURIComponent(pageRef)}/files?${params.toString()}`);
}

/** Saves one Canvas file into the course folder (an existing file is kept). */
export function downloadCanvasFile(courseId: string | number, fileId: string, courseName: string, outputDir: string, conflictStrategy: ConflictStrategy = 'skip', week: { module_name?: string | null; week_position?: number | string | null } = {}): Promise<CanvasFileDownloadResponse> {
  return requestJson<CanvasFileDownloadResponse>('/api/download/canvas-file', {
    method: 'POST',
    body: JSON.stringify({
      course_id: courseId,
      file_id: fileId,
      course_name: courseName,
      output_dir: outputDir.trim() || undefined,
      conflict_strategy: conflictStrategy,
      // The page's week decides the folder; without it the backend files the attachment under 기타.
      module_name: week.module_name || undefined,
      week_position: week.week_position ?? undefined,
    }),
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

function nativeJobNeedsVideoPostprocess(job: DownloadJobStatus): boolean {
  const isMp4Item = (item: DownloadResult | BatchDownloadItemResult) =>
    item.ok && typeof item.output_path === 'string' && item.output_path.toLowerCase().endsWith('.mp4');
  if (isSingleDownloadResult(job.result)) return isMp4Item(job.result);
  if (isBatchDownloadResult(job.result)) return job.result.items.some(isMp4Item);
  return false;
}

async function runNativeVideoPostprocess(job: DownloadJobStatus, progress: ProgressEvent[]): Promise<DownloadJobStatus> {
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

const nativePostprocessEntries = new Map<string, PostprocessEntry>();

/**
 * A native download that finished writing bytes is not finished for the user until
 * every MP4 passed the ensure-playable step. Until then it is reported as a
 * non-terminal `running` job; see `reportWithPostprocess`.
 */
export async function withNativeVideoPostprocess(job: DownloadJobStatus): Promise<DownloadJobStatus> {
  if (!isTauriRuntime() || !job.job_id.startsWith('native-')) return job;
  return reportWithPostprocess(nativePostprocessEntries, job, {
    needsPostprocess: nativeJobNeedsVideoPostprocess,
    run: runNativeVideoPostprocess,
  });
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
    // A cancel that lands during MP4 post-processing must not report the job as finished.
    return withNativeVideoPostprocess(await cancelNativeDownloadJob(jobId));
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

export function fetchRemoteSttStatus(): Promise<RemoteSttStatus> {
  return requestJson<RemoteSttStatus>('/api/stt/remote/status');
}

export function saveRemoteSttConfig(patch: RemoteSttConfigPatch): Promise<RemoteSttStatus> {
  return requestJson<RemoteSttStatus>('/api/stt/remote/config', { method: 'PUT', body: JSON.stringify(patch) });
}

/** Opens the SSH tunnel if needed and probes the server. A failed probe is data (`reachable: false`), not an error. */
export function checkRemoteStt(): Promise<RemoteSttStatus> {
  return requestJson<RemoteSttStatus>('/api/stt/remote/check', { method: 'POST' });
}

// ------------------------------------------------------------ Folder tidy-up (preview, move, undo)

const tidyJobsPath = (kind: TidyJobKind) => `/api/library/tidy/${kind}/jobs`;

function startTidyJob(kind: TidyJobKind, body: Record<string, string>): Promise<TidyJobStatus> {
  return requestJson<TidyJobStatus>(tidyJobsPath(kind), { method: 'POST', body: JSON.stringify(body) });
}

export const startTidyPlanJob = (outputDir: string) => startTidyJob('plan', { output_dir: outputDir.trim() });
export const startTidyApplyJob = (planId: string) => startTidyJob('apply', { plan_id: planId });
export const startTidyUndoJob = (runId: string) => startTidyJob('undo', { run_id: runId });

export function fetchTidyJob(kind: TidyJobKind, jobId: string): Promise<TidyJobStatus> {
  return requestJson<TidyJobStatus>(`${tidyJobsPath(kind)}/${jobId}`);
}

export function cancelTidyJob(kind: TidyJobKind, jobId: string): Promise<TidyJobStatus> {
  return requestJson<TidyJobStatus>(`${tidyJobsPath(kind)}/${jobId}/cancel`, { method: 'POST' });
}

/** Past tidy-up runs of the download folder, newest first. */
export function fetchTidyRuns(outputDir: string): Promise<{ runs: TidyRun[] }> {
  return requestJson<{ runs: TidyRun[] }>(`/api/library/tidy/runs?${new URLSearchParams({ output_dir: outputDir.trim() }).toString()}`);
}
