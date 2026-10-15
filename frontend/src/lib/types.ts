export type ConflictStrategy = 'rename' | 'overwrite' | 'skip';

export interface CredentialStatus {
  ok: boolean;
  source: string;
  username_present: boolean;
  password_present: boolean;
  can_write: boolean;
}

export interface TermSummary {
  term_id: number;
  course_count: number;
  current_course_count: number;
  is_current: boolean;
  label: string;
}

export interface TermsResponse {
  ok: boolean;
  error_code: string | null;
  terms: TermSummary[];
}

export interface CourseSummary {
  id: string | number;
  name: string;
  module_count: number;
  lecture_count: number;
  uncompleted_lecture_count: number;
}

export interface LectureItem {
  module_id: string | number | null;
  module_name: string;
  item_id: string | number | null;
  module_item_id: string | number | null;
  title: string;
  completed: boolean;
  duration: number | null;
  content_type: string | null;
}

export interface CourseContentItem {
  module_id: string | number | null;
  module_name: string;
  week_position: string | number | null;
  lesson_position: string | number | null;
  item_id: string | number | null;
  module_item_id: string | number | null;
  title: string;
  content_type: string | null;
  inner_content_type: string | null;
  kind: 'video' | 'material' | 'assignment' | 'quiz' | 'board' | 'page' | 'section' | 'unknown' | string;
  completed: boolean | null;
  attendance_status: string | null;
  duration: number | null;
  size_bytes: number | null;
  file_name: string | null;
  url: string | null;
  published: boolean | null;
  unlock_at: string | null;
  due_at: string | null;
  lock_at: string | null;
  late_at: string | null;
  downloadable: boolean;
  resolvable: boolean;
}

export interface CoursesResponse {
  ok: boolean;
  error_code: string | null;
  course_count: number;
  courses: CourseSummary[];
  term_ids: number[] | null;
}

export interface CourseDetailResponse {
  ok: boolean;
  error_code: string | null;
  course: CourseSummary;
  lecture_count: number;
  lectures: LectureItem[];
  content_count: number;
  content_items: CourseContentItem[];
}

export interface ProgressEvent {
  stage: string;
  index?: number;
  total?: number;
  module_item_id?: string | number;
  title?: string;
  output_path?: string;
  file_size?: number;
  bytes_written?: number;
  total_bytes?: number;
  resume_from?: number;
  error_code?: string;
  resolved_count?: number;
  download_count?: number;
  max_parallel_downloads?: number;
  http_status?: number;
  content_type?: string;
  content_length?: number;
  response_host?: string;
  progress_percent?: number | null;
  elapsed_ms?: number | null;
  duration_ms?: number | null;
  input_path?: string;
  segment_count?: number;
  duration?: number | null;
  elapsed_seconds?: number | null;
}

export interface DownloadResult {
  ok: boolean;
  error_code: string | null;
  dry_run: boolean;
  written: boolean;
  course_id: string | number;
  module_item_id: string | number;
  attendance_item_id: string | null;
  title: string | null;
  content_id: string | null;
  download_url: string | null;
  download_size: number | null;
  output_path: string | null;
  file_size: number | null;
  file_exists: boolean;
  skipped: boolean;
  conflict_strategy: ConflictStrategy;
  progress: ProgressEvent[];
  playable_checked?: boolean;
  reconstructed?: boolean;
  source_kind?: string;
}

export interface NativeDownloadTransferPlan {
  download_url: string;
  referer: string | null;
  output_path: string;
  total_bytes: number | null;
}

export interface NativeDownloadPlanResult extends DownloadResult {
  native_download: NativeDownloadTransferPlan | null;
}

export interface OfficialViewerPlanResult {
  ok: boolean;
  error_code: string | null;
  course_id: string | number;
  module_item_id: string | number;
  attendance_item_id: string | null;
  title: string | null;
  content_id: string | null;
  content_type: string | null;
  duration: number | null;
  viewer_url: string | null;
}

export interface OfficialViewerBridgeCookie {
  name: string;
  value: string;
  domain: string;
  path: string;
  secure: boolean;
  http_only: boolean;
  same_site: string | null;
}

export interface OfficialViewerSessionBridgeResult extends OfficialViewerPlanResult {
  launch_url: string | null;
  cookies: OfficialViewerBridgeCookie[];
  diagnostics: {
    cookie_count: number;
    cookie_names: string[];
    cookie_domains?: string[];
    http_trace?: unknown;
  };
}

export interface DownloadArtifactResult {
  ok: boolean;
  error_code: string | null;
  download: DownloadResult | BatchDownloadResult;
  artifacts: Array<Record<string, unknown> & { kind: string; ok?: boolean; output_path?: string | null; text_path?: string | null }>;
  progress: ProgressEvent[];
}

export type DownloadOutputFormat = 'mp4' | 'mp3' | 'txt';

export interface DownloadJobStatus {
  job_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  cancel_requested: boolean;
  progress: ProgressEvent[];
  result: DownloadResult | BatchDownloadResult | DownloadArtifactResult | null;
  error_code: string | null;
}

export interface BatchDownloadItemResult extends DownloadResult {
  completed: boolean | null;
}

export interface BatchProgressEvent extends ProgressEvent {}

export interface BatchDownloadResult {
  ok: boolean;
  error_code: string | null;
  dry_run: boolean;
  course_id: string | number;
  course_name: string;
  total_count: number;
  success_count: number;
  failure_count: number;
  items: BatchDownloadItemResult[];
  progress: BatchProgressEvent[];
}

export interface AudioExtractionResult {
  ok: boolean;
  input_path: string;
  output_path: string | null;
  file_size: number | null;
  error_code: string | null;
  message: string | null;
  install_hint: string | null;
}

export interface TranscriptSegment {
  start: number;
  end: number;
  text: string;
}

export interface TranscriptionResult {
  ok: boolean;
  audio_path: string;
  text_path: string | null;
  md_path: string | null;
  json_path: string | null;
  segments: TranscriptSegment[];
  error_code: string | null;
  message: string | null;
  install_hint: string | null;
}

export interface STTProviderStatus {
  provider: string;
  installed: boolean;
  available: boolean;
  install_hint: string | null;
  recommended_model?: string | null;
  quality_note?: string | null;
}

export interface STTProviderStatusResponse {
  ok: boolean;
  providers: STTProviderStatus[];
}


export interface RuntimeDependencyStatus {
  name: string;
  installed: boolean;
  available: boolean;
  source: string;
  path: string | null;
  install_hint: string | null;
}

export interface MediaDependencyStatusResponse {
  ok: boolean;
  packaging_mode: string;
  auto_install_available?: boolean;
  auto_install_unavailable_reason?: string | null;
  dependencies: RuntimeDependencyStatus[];
  providers: STTProviderStatus[];
}

export interface MediaDependencyInstallResult {
  ok: boolean;
  error_code?: string | null;
  installed_components: string[];
  missing_components: string[];
  message: string | null;
  failed_component?: string | null;
}

export interface PostprocessJobStatus<T = AudioExtractionResult | TranscriptionResult> {
  job_id: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
  cancel_requested: boolean;
  progress: ProgressEvent[];
  result: T | null;
  error_code: string | null;
}

export interface BackendSupervisionStatus {
  state: 'starting' | 'ready' | 'failed';
  /** external | managed | managed-sidecar | disabled | browser | starting | failed | ... */
  mode: string;
  error: string | null;
  log_path: string | null;
}

export interface BackendConnection {
  baseUrl: string;
  /** Per-launch token for a Tauri-managed backend; null for external/Vite backends. */
  token: string | null;
}

export type WatchDebugEvent = {
  stage: string;
  mode?: string;
  frame?: string;
  host?: string;
  ready_state?: string;
  videos?: string;
  video_state?: string;
  buttons?: string;
  selector?: string;
  error_name?: string;
  error_message?: string;
};

export type WatchQueueState = { active: boolean; index: number; total: number };
