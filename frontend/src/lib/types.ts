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
  /** Earliest deadline still ahead among unwatched lectures (backend, optional). */
  next_deadline_at?: string | null;
  next_deadline_is_late?: boolean;
  missed_lecture_count?: number;
  /** Unwatched lectures whose week has not opened yet (`uncompleted_lecture_count` counts open ones only). */
  upcoming_lecture_count?: number;
  /** Earliest due/lock still ahead among incomplete assignments and quizzes. */
  next_task_deadline_at?: string | null;
  /** Incomplete assignments/quizzes whose due and lock times have all passed. */
  overdue_task_count?: number;
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
  /** LMS id of the page/board/assignment behind the module item (pages and boards). */
  resource_id?: string | null;
  /** Wiki pages only: slug or numeric id the Canvas pages API takes. */
  page_ref?: string | null;
  /** Already-saved file in the download folder (backend lookup), or null. */
  downloaded_path?: string | null;
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

export interface DeadlineEntryDto {
  course_id: string | number;
  course_name: string;
  kind: 'video' | 'assignment' | 'quiz';
  title: string;
  module_item_id: string | number | null;
  url: string | null;
  unlock_at: string | null;
  due_at: string | null;
  late_at: string | null;
  lock_at: string | null;
  completed: boolean;
  /** Video length in seconds when the LMS gave one (videos only). */
  duration?: number | null;
  /** Backend's view when it answered; the UI recomputes it from the times. */
  state: 'open' | 'late' | 'missed' | 'upcoming';
}

export interface RemindersStatus {
  enabled: boolean;
  /** The background LaunchAgent is installed (reminders while the app is closed). */
  agent_installed: boolean;
  /** Unix seconds of the last deadline refresh, or `null` when nothing was saved yet. */
  last_saved_at: number | null;
  /** Whether this build can install the background agent (packaged release app only). */
  supported: boolean;
}

export interface DeadlinesResponse {
  ok: boolean;
  error_code: string | null;
  term_ids: number[] | null;
  deadline_count: number;
  deadlines: DeadlineEntryDto[];
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
  /** True when the subtitles are made by the remote STT server. */
  remote?: boolean;
  repair_index?: number;
  repair_total?: number;
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
  srt_path?: string | null;
  segments: TranscriptSegment[];
  error_code: string | null;
  message: string | null;
  install_hint: string | null;
  /** Non-fatal quality notes, for example a long silent stretch. */
  warnings?: string[];
}

export interface STTProviderStatus {
  provider: string;
  installed: boolean;
  available: boolean;
  install_hint: string | null;
  recommended_model?: string | null;
  quality_note?: string | null;
  /** False when this machine cannot run the provider at all; installing will not help. */
  platform_supported?: boolean;
}

export type RemoteSttKind = 'whisper_cpp_server' | 'openai_compatible';

export interface RemoteSttTunnel {
  host: string;
  remote_port: number;
  local_port: number | null;
}

/** Saved settings of the remote subtitle server. The API key is never returned, only whether one is set. */
export interface RemoteSttStatus {
  ok: boolean;
  enabled: boolean;
  kind: RemoteSttKind;
  base_url: string;
  model: string;
  api_key_set: boolean;
  tunnel: RemoteSttTunnel | null;
  /** `null` until a connection check ran since the last settings change. */
  reachable: boolean | null;
  last_error: string | null;
  error_code: string | null;
  checked_at: number | null;
}

/** Partial update; `api_key: ''` removes the key, omitting it keeps the saved one, `tunnel: null` removes the tunnel. */
export interface RemoteSttConfigPatch {
  enabled?: boolean;
  kind?: RemoteSttKind;
  base_url?: string;
  model?: string;
  api_key?: string;
  tunnel?: { host: string; remote_port: number; local_port?: number | null } | null;
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

export type WatchItemState = 'pending' | 'playing' | 'done' | 'skipped';
export type WatchPhase = 'idle' | 'opening' | 'playing' | 'stalled' | 'finished';

export interface WatchQueueItemState {
  module_item_id: string;
  title: string;
  state: WatchItemState;
  reason: string | null;
  duration_secs: number | null;
}

/** The sequential-watch queue as the desktop shell holds it (single source of truth). */
export interface WatchQueueState {
  active: boolean;
  phase: WatchPhase;
  /** Zero based index of the current lecture (`total` once finished). */
  index: number;
  total: number;
  done_count: number;
  skipped_count: number;
  current_module_item_id: string | null;
  current_title: string | null;
  position_secs: number | null;
  duration_secs: number | null;
  remaining_secs: number | null;
  total_remaining_secs: number;
  paused: boolean;
  /** False while the playback window is minimized or covered (the player stops then). */
  rendering: boolean;
  items: WatchQueueItemState[];
  /** Which playback mode runs this queue (the in-app window when left out). */
  source?: WatchMode;
  /** Course of the current lecture (shown by the Chrome mode strip). */
  current_course_name?: string | null;
  /** The Chrome window is still being opened (nothing is playing yet). */
  launching?: boolean;
}

/** How "순서대로 시청" plays: a dedicated Chrome window, or the small window inside the app. */
export type WatchMode = 'chrome' | 'app';

export type ChromeWatchState = 'idle' | 'starting' | 'playing' | 'finished' | 'stopped' | 'failed';

export interface ChromeWatchItemInput {
  course_id: string;
  course_name: string;
  module_item_id: string;
  title: string;
  duration: number | null;
}

export interface ChromeWatchNotice {
  id: number;
  /** `finished`, `skipped`, `stalled` or `failed`. */
  kind: string;
  level: 'info' | 'success' | 'error';
  text: string;
  item_title: string | null;
  reason: string | null;
}

/** `GET /api/watch/chrome/status`: the Chrome playback queue. */
export interface ChromeWatchStatus {
  ok: boolean;
  state: ChromeWatchState;
  run_id: string | null;
  active: boolean;
  chrome_available: boolean;
  index: number;
  total: number;
  done_count: number;
  skipped_count: number;
  current: {
    module_item_id: string;
    course_id: string;
    course_name: string;
    title: string;
    phase: 'opening' | 'playing' | 'stalled';
    position: number | null;
    duration: number | null;
    paused: boolean;
  } | null;
  items: Array<{
    module_item_id: string;
    course_id: string;
    course_name: string;
    title: string;
    state: WatchItemState;
    reason: string | null;
    duration: number | null;
  }>;
  error_code: string | null;
  error_message: string | null;
  notices: ChromeWatchNotice[];
}

/** What the desktop shell gets for the menu bar: the queue as shown plus the run and its notices. */
export interface ExternalWatchView extends WatchQueueState {
  run_id: string;
  notices: ChromeWatchNotice[];
}

/** One lecture handed to the desktop shell's queue. */
export interface WatchQueueItemInput {
  module_item_id: string;
  /** The lecture's own course (queues that mix courses); the request's course when left out. */
  course_id?: string;
  course_name?: string;
  title: string;
  /** Length in seconds when the lecture list knows it. */
  duration: number | null;
}

export interface WatchQueueNotice {
  kind: 'info' | 'success' | 'error';
  text: string;
  /** The LMS login is gone: back to the login screen. */
  auth_expired: boolean;
}

export interface LmsSessionBridgeResponse {
  ok: boolean;
  error_code: string | null;
  cookies: OfficialViewerBridgeCookie[];
  diagnostics: { cookie_count: number; cookie_names: string[] };
}

export interface PageFile {
  file_id: string;
  display_name: string;
  size: number | null;
  /** Signed Canvas address; null when Canvas refuses the file. */
  download_url: string | null;
  content_type: string | null;
  /** Already saved in the course folder. */
  downloaded_path: string | null;
}

export interface PageFilesResponse {
  ok: boolean;
  error_code: string | null;
  course_id: string;
  page_ref: string;
  title: string | null;
  file_count: number;
  files: PageFile[];
}

export interface CanvasFileDownloadResponse {
  ok: boolean;
  error_code: string | null;
  file_id: string;
  display_name: string | null;
  output_path: string | null;
  file_size: number | null;
  skipped: boolean;
  written: boolean;
}
