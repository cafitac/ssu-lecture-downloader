import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ApiError, cancelDownloadJob, deleteCredentials, fetchAudioExtractionJob, fetchCourseDetail, fetchCourses, fetchCredentialStatus, fetchDownloadJob, fetchMediaDependencyInstallJob, fetchMediaDependencyStatus, fetchSttJob, fetchSttProviderStatus, fetchTerms, isAuthenticationError, planOfficialViewer, planOfficialViewerSessionBridge, saveCredentials, startArtifactDownloadJob, startAudioExtractionJob, startBatchDownloadJob, startDownloadJob, startMediaDependencyInstallJob, startSttJob } from './lib/api';
import { defaultDownloadDirectory, isTauriRuntime, openAssistedOfficialViewer, openDownloadedFolder, openOfficialViewer, pickDownloadDirectory, backendSupervisionStatus } from './lib/desktop';
import { listen } from '@tauri-apps/api/event';
import type { AudioExtractionResult, BatchDownloadResult, ConflictStrategy, CourseContentItem, CourseSummary, CredentialStatus, DownloadArtifactResult, DownloadJobStatus, DownloadOutputFormat, DownloadResult, LectureItem, MediaDependencyInstallResult, PostprocessJobStatus, ProgressEvent, RuntimeDependencyStatus, STTProviderStatus, TermSummary, TranscriptionResult } from './lib/types';
import './styles.css';

type LoadState = 'idle' | 'loading' | 'ready' | 'error';

type WatchDebugEvent = {
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

function formatBytes(value: number | null | undefined): string {
  if (!value) return '-';
  const mb = value / 1024 / 1024;
  return `${mb.toFixed(1)} MB`;
}


function formatDuration(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-';
  const totalSeconds = Math.max(0, Math.round(value));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function contentKindLabel(kind: string): string {
  switch (kind) {
    case 'video': return '영상';
    case 'material': return '자료';
    case 'assignment': return '과제';
    case 'quiz': return '퀴즈';
    case 'board': return '게시판';
    case 'page': return '페이지';
    case 'section': return '구분';
    default: return '기타';
  }
}

function legacyLectureToContentItem(lecture: LectureItem): CourseContentItem {
  return {
    module_id: lecture.module_id,
    module_name: lecture.module_name,
    week_position: null,
    lesson_position: null,
    item_id: lecture.item_id,
    module_item_id: lecture.module_item_id,
    title: lecture.title,
    content_type: 'attendance_item',
    inner_content_type: lecture.content_type,
    kind: 'video',
    completed: lecture.completed,
    attendance_status: null,
    duration: lecture.duration,
    size_bytes: null,
    file_name: null,
    url: null,
    published: null,
    unlock_at: null,
    due_at: null,
    lock_at: null,
    late_at: null,
    downloadable: lecture.module_item_id !== null,
    resolvable: lecture.module_item_id !== null,
  };
}

const DEFAULT_DOWNLOAD_DIR = '~/Downloads';
const TERMINAL_DOWNLOAD_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);

function latestByteProgress(events: Array<{ bytes_written?: number; total_bytes?: number }>): { written?: number; total?: number; percent: number } {
  const latest = [...events].reverse().find((event) => typeof event.bytes_written === 'number');
  const written = latest?.bytes_written;
  const total = latest?.total_bytes;
  const percent = typeof written === 'number' && typeof total === 'number' && total > 0
    ? Math.min(100, Math.max(0, Math.round((written / total) * 100)))
    : 0;
  return { written, total, percent };
}

function latestResponseDiagnostics(events: ProgressEvent[]): ProgressEvent | undefined {
  return [...events].reverse().find((event) =>
    typeof event.http_status === 'number'
    || typeof event.content_type === 'string'
    || typeof event.content_length === 'number'
    || typeof event.response_host === 'string'
  );
}

function responseDiagnosticText(event: ProgressEvent | undefined): string | null {
  if (!event) return null;
  const parts = [
    event.response_host ? `host ${event.response_host}` : null,
    event.http_status ? `HTTP ${event.http_status}` : null,
    event.content_type ? event.content_type : null,
    typeof event.content_length === 'number' ? formatBytes(event.content_length) : null,
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : null;
}

function downloadStageLabel(status: string | undefined, events: Array<{ stage: string }> = []): string {
  const latest = events.at(-1)?.stage;
  if (status === 'running') {
    if (latest?.includes('skip')) return '기존 파일 확인 중';
    if (latest?.includes('audio')) return 'MP3 변환 중';
    if (latest?.includes('transcrib') || latest?.includes('transcript')) return 'TXT 생성 중';
    if (latest === 'batch_downloads_started' || latest === 'download_started' || latest === 'download_progress') return '동시 다운로드 중';
    if (latest?.includes('resolve') || latest?.includes('prepare') || latest?.includes('plan')) return '다운로드 준비 중';
    return '작업 중';
  }
  switch (status) {
    case 'queued':
      return '준비 중';
    case 'running':
      return '다운로드 중';
    case 'succeeded':
      return '완료';
    case 'failed':
      return '실패';
    case 'cancelled':
      return '취소됨';
    default:
      return '대기 중';
  }
}

function latestUserStage(events: Array<{ stage: string; download_count?: number; max_parallel_downloads?: number }>): string {
  const latestEvent = events.at(-1);
  const latest = latestEvent?.stage;
  if (!latest) return '강의를 선택하고 다운로드를 시작하세요.';
  if (latest === 'batch_prepare_done') return '강의 파일 위치 확인이 끝났습니다. 동시 다운로드를 시작합니다.';
  if (latest === 'batch_downloads_started') return `실제 파일 다운로드를 최대 ${latestEvent?.max_parallel_downloads ?? 4}개씩 동시에 진행합니다.`;
  if (latest === 'extracting_audio' || latest === 'audio_probe_done') return 'MP3 파일을 만들 준비를 하는 중입니다.';
  if (latest === 'audio_progress') return 'MP3 파일로 변환하는 중입니다.';
  if (latest === 'audio_done') return 'MP3 파일 생성이 끝났습니다.';
  if (latest === 'audio_skipped') return '이미 있는 MP3 파일을 사용합니다.';
  if (latest === 'transcript_skipped') return '이미 생성된 TXT 파일을 사용합니다.';
  if (latest.includes('transcrib') || latest.includes('transcript')) return latest === 'transcript_model_loading' ? 'TXT 생성 모델을 준비하는 중입니다.' : 'TXT 자막을 만드는 중입니다.';
  if (latest.includes('skip')) return '이미 저장된 파일은 건너뛰고 필요한 작업만 진행합니다.';
  if (latest.includes('resolve') || latest.includes('plan')) return '강의 파일 위치를 확인하는 중입니다.';
  if (latest === 'video_playable_check_started') return '재생 가능한 MP4인지 확인하는 중입니다.';
  if (latest === 'video_reconstructed') return '재생 가능한 MP4로 변환이 끝났습니다.';
  if (latest === 'video_playable_check_done') return '재생 가능한 MP4 저장이 끝났습니다.';
  if (latest.includes('download')) return '강의 파일을 저장하는 중입니다.';
  if (latest.includes('write') || latest.includes('chunk')) return '파일을 쓰는 중입니다.';
  if (latest.includes('complete') || latest.includes('success')) return '저장이 끝났습니다.';
  return '작업을 처리하는 중입니다.';
}

function isArtifactDownloadResult(result: DownloadResult | BatchDownloadResult | DownloadArtifactResult | null | undefined): result is DownloadArtifactResult {
  return Boolean(result && 'artifacts' in result && 'download' in result);
}

function isBatchDownloadResult(result: DownloadResult | BatchDownloadResult | DownloadArtifactResult | null | undefined): result is BatchDownloadResult {
  return Boolean(result && !isArtifactDownloadResult(result) && 'items' in result);
}

function downloadPayloadFromArtifact(result: DownloadResult | BatchDownloadResult | DownloadArtifactResult | null | undefined): DownloadResult | BatchDownloadResult | null | undefined {
  return isArtifactDownloadResult(result) ? result.download : result;
}

function latestBatchPosition(events: Array<{ index?: number; total?: number }>): { index?: number; total?: number } {
  const latest = [...events].reverse().find((event) => typeof event.index === 'number' && typeof event.total === 'number');
  return { index: latest?.index, total: latest?.total };
}

function batchPreparationProgress(events: Array<{ stage: string; index?: number; total?: number; resolved_count?: number }>): { resolved: number; total: number; percent: number; done: boolean } {
  const total = [...events].reverse().find((event) => typeof event.total === 'number')?.total ?? 0;
  const prepareDone = [...events].reverse().find((event) => event.stage === 'batch_prepare_done');
  const latestResolve = [...events].reverse().find((event) => event.stage.includes('resolve') && typeof event.index === 'number');
  const resolved = prepareDone?.resolved_count ?? latestResolve?.index ?? 0;
  const percent = total > 0 ? Math.min(100, Math.max(0, Math.round((resolved / total) * 100))) : 0;
  return { resolved, total, percent, done: Boolean(prepareDone) };
}

function batchDownloadStart(events: Array<{ stage: string; download_count?: number; max_parallel_downloads?: number }>): { started: boolean; downloadCount?: number; parallelism?: number; skipped: boolean } {
  const event = [...events].reverse().find((item) => item.stage === 'batch_downloads_started');
  const skipped = events.some((item) => item.stage === 'artifact_downloads_skipped');
  return { started: Boolean(event), downloadCount: event?.download_count, parallelism: event?.max_parallel_downloads, skipped };
}

function artifactAudioProgress(events: Array<{ stage: string; index?: number; total?: number; progress_percent?: number | null }>): { current: number; total: number; percent: number; started: boolean; done: boolean } {
  const audioEvents = events.filter((event) => event.stage.includes('audio'));
  const total = [...audioEvents].reverse().find((event) => typeof event.total === 'number')?.total ?? 0;
  const latestByIndex = new Map<number, number>();
  for (const event of audioEvents) {
    if (typeof event.index !== 'number') continue;
    if (event.stage === 'audio_done' || event.stage === 'audio_skipped') latestByIndex.set(event.index, 100);
    else if (typeof event.progress_percent === 'number') latestByIndex.set(event.index, Math.min(100, Math.max(0, event.progress_percent)));
    else if (!latestByIndex.has(event.index)) latestByIndex.set(event.index, 0);
  }
  const completed = [...latestByIndex.values()].filter((value) => value >= 100).length;
  const percent = total > 0 ? Math.min(100, Math.max(0, Math.round(([...latestByIndex.values()].reduce((sum, value) => sum + value, 0) / total)))) : 0;
  const latest = audioEvents.at(-1);
  return { current: Math.min(total, Math.max(completed, latest?.index ?? completed)), total, percent, started: audioEvents.length > 0, done: total > 0 && completed >= total };
}

function artifactTranscriptProgress(events: Array<{ stage: string; index?: number; total?: number; progress_percent?: number | null }>): { current: number; total: number; percent: number; started: boolean; done: boolean } {
  const transcriptEvents = events.filter((event) => event.stage.includes('transcript') || event.stage === 'transcribing');
  const total = [...transcriptEvents].reverse().find((event) => typeof event.total === 'number')?.total ?? 0;
  const latestByIndex = new Map<number, number>();
  for (const event of transcriptEvents) {
    if (typeof event.index !== 'number') continue;
    if (event.stage === 'transcript_done' || event.stage === 'transcript_skipped') latestByIndex.set(event.index, 100);
    else if (event.stage === 'transcript_model_loading') latestByIndex.set(event.index, Math.max(latestByIndex.get(event.index) ?? 0, 5));
    else if (event.stage === 'transcript_started') latestByIndex.set(event.index, Math.max(latestByIndex.get(event.index) ?? 0, 10));
    else if (typeof event.progress_percent === 'number') latestByIndex.set(event.index, Math.min(100, Math.max(0, event.progress_percent)));
    else if (!latestByIndex.has(event.index)) latestByIndex.set(event.index, 0);
  }
  const completed = [...latestByIndex.values()].filter((value) => value >= 100).length;
  const percent = total > 0 ? Math.min(100, Math.max(0, Math.round(([...latestByIndex.values()].reduce((sum, value) => sum + value, 0) / total)))) : 0;
  const latest = transcriptEvents.at(-1);
  return { current: Math.min(total, Math.max(completed, latest?.index ?? completed)), total, percent, started: transcriptEvents.length > 0, done: total > 0 && completed >= total };
}

type FileProgressRow = {
  key: string;
  title: string;
  status: string;
  written?: number;
  total?: number;
  percent: number;
  outputPath?: string;
  errorCode?: string;
};

function fileProgressRows(events: Array<{ stage: string; module_item_id?: string | number; title?: string; output_path?: string; bytes_written?: number; total_bytes?: number; file_size?: number; error_code?: string; progress_percent?: number | null }>, result?: BatchDownloadResult | null): FileProgressRow[] {
  const rows = new Map<string, FileProgressRow>();
  const ensure = (key: string): FileProgressRow => {
    const existing = rows.get(key);
    if (existing) return existing;
    const created: FileProgressRow = { key, title: `파일 ${rows.size + 1}`, status: '대기 중', percent: 0 };
    rows.set(key, created);
    return created;
  };
  for (const item of result?.items ?? []) {
    const key = String(item.module_item_id);
    const row = ensure(key);
    row.title = item.title ?? row.title;
    row.outputPath = item.output_path ?? row.outputPath;
    row.errorCode = item.error_code ?? undefined;
    row.total = item.file_size ?? item.download_size ?? row.total;
    row.written = item.written || item.skipped ? (item.file_size ?? item.download_size ?? row.total ?? 1) : row.written;
    row.status = item.skipped ? '스킵됨' : item.ok ? (item.written ? '완료' : '준비됨') : '실패';
    if (item.written || item.skipped) row.percent = 100;
  }
  for (const event of events) {
    if (event.module_item_id === undefined) continue;
    const key = String(event.module_item_id);
    const row = ensure(key);
    row.title = event.title ?? row.title;
    row.outputPath = event.output_path ?? row.outputPath;
    row.errorCode = event.error_code ?? row.errorCode;
    if (typeof event.total_bytes === 'number' && event.total_bytes > 0) row.total = event.total_bytes;
    if (typeof event.bytes_written === 'number') row.written = event.bytes_written;
    const isArtifactStage = event.stage === 'extracting_audio' || event.stage === 'audio_probe_done' || event.stage === 'audio_progress' || event.stage === 'audio_done' || event.stage === 'audio_skipped' || event.stage === 'transcribing' || event.stage === 'transcript_model_loading' || event.stage === 'transcript_started' || event.stage === 'transcript_progress' || event.stage === 'transcript_done' || event.stage === 'transcript_skipped';
    if (event.stage === 'extracting_audio' || event.stage === 'audio_probe_done') {
      row.status = 'MP3 준비 중';
      row.percent = 0;
    }
    if (event.stage === 'audio_progress') {
      row.status = 'MP3 변환 중';
      if (typeof event.progress_percent === 'number') row.percent = Math.min(100, Math.max(0, Math.round(event.progress_percent)));
    }
    if (event.stage.includes('resolve') || event.stage.includes('plan')) row.status = '준비 중';
    if (event.stage === 'download_started' || event.stage === 'download_progress') row.status = '다운로드 중';
    if (event.stage === 'audio_done') row.status = 'MP3 완료';
    if (event.stage === 'audio_skipped') row.status = '기존 MP3 사용';
    if (event.stage === 'transcribing' || event.stage === 'transcript_model_loading' || event.stage === 'transcript_started') {
      row.status = event.stage === 'transcript_model_loading' ? 'TXT 모델 준비 중' : 'TXT 생성 중';
      row.percent = event.stage === 'transcript_model_loading' ? 5 : 10;
    }
    if (event.stage === 'transcript_progress') {
      row.status = 'TXT 생성 중';
      if (typeof event.progress_percent === 'number') row.percent = Math.min(100, Math.max(0, Math.round(event.progress_percent)));
    }
    if (event.stage === 'transcript_done') row.status = 'TXT 완료';
    if (event.stage === 'transcript_skipped') row.status = '기존 TXT 사용';
    if (event.stage === 'download_done') row.status = '다운로드 완료';
    if (event.stage === 'video_playable_check_started') row.status = 'MP4 확인/변환 중';
    if (event.stage === 'video_reconstructed') row.status = 'MP4 변환 완료';
    if (event.stage === 'video_playable_check_done') row.status = 'MP4 확인 완료';
    if (event.stage.includes('failed')) row.status = '실패';
    if (event.stage === 'download_done' || event.stage === 'video_reconstructed' || event.stage === 'video_playable_check_done' || event.stage === 'audio_done' || event.stage === 'audio_skipped' || event.stage === 'transcript_done' || event.stage === 'transcript_skipped') {
      row.percent = 100;
      if (typeof event.file_size === 'number') {
        row.written = event.file_size;
        row.total = event.file_size;
      }
    } else if (!isArtifactStage && typeof row.written === 'number' && typeof row.total === 'number' && row.total > 0) {
      row.percent = Math.min(100, Math.max(0, Math.round((row.written / row.total) * 100)));
    }
  }
  return Array.from(rows.values());
}

function aggregateFileProgress(rows: FileProgressRow[], fallbackPercent: number): { percent: number; written?: number; total?: number } {
  if (rows.length === 0) return { percent: fallbackPercent };
  const totals = rows.map((row) => ({ written: row.percent === 100 && row.total === undefined ? 1 : row.written, total: row.total ?? (row.percent === 100 ? 1 : undefined) }));
  if (totals.every((row) => typeof row.written === 'number' && typeof row.total === 'number' && row.total > 0)) {
    const written = totals.reduce((sum, row) => sum + (row.written ?? 0), 0);
    const total = totals.reduce((sum, row) => sum + (row.total ?? 0), 0);
    return { written, total, percent: total > 0 ? Math.min(100, Math.max(0, Math.round((written / total) * 100))) : fallbackPercent };
  }
  return { percent: Math.round(rows.reduce((sum, row) => sum + row.percent, 0) / rows.length) };
}

function progressLabelPercent(percent: number): string {
  return `${Math.min(100, Math.max(0, percent))}%`;
}

function App() {
  const credentialFormRef = useRef<HTMLFormElement | null>(null);
  const [credentialStatus, setCredentialStatus] = useState<CredentialStatus | null>(null);
  const [backendMode, setBackendMode] = useState('checking');
  const [courses, setCourses] = useState<CourseSummary[]>([]);
  const [terms, setTerms] = useState<TermSummary[]>([]);
  const [selectedTermId, setSelectedTermId] = useState<number | null>(null);
  const [selectedCourse, setSelectedCourse] = useState<CourseSummary | null>(null);
  const [lectures, setLectures] = useState<LectureItem[]>([]);
  const [contentItems, setContentItems] = useState<CourseContentItem[]>([]);
  const [selectedLecture, setSelectedLecture] = useState<CourseContentItem | null>(null);
  const [contentKindFilter, setContentKindFilter] = useState('downloadable');
  const [weekFilter, setWeekFilter] = useState('all');
  const [completionFilter, setCompletionFilter] = useState('all');
  const [selectedLectureIds, setSelectedLectureIds] = useState<Array<string | number>>([]);
  const [selectedOutputFormats, setSelectedOutputFormats] = useState<DownloadOutputFormat[]>(['mp4']);
  const [downloadResult, setDownloadResult] = useState<DownloadResult | null>(null);
  const [downloadJob, setDownloadJob] = useState<DownloadJobStatus | null>(null);
  const [downloadStartError, setDownloadStartError] = useState<string | null>(null);
  const [downloadDiagnostics, setDownloadDiagnostics] = useState<string[]>([]);
  const [watchQueueState, setWatchQueueState] = useState({ active: false, index: 0, total: 0 });
  const watchQueueRef = useRef<CourseContentItem[]>([]);
  const watchQueueIndexRef = useRef(0);
  const watchQueueActiveRef = useRef(false);
  const selectedCourseRef = useRef<CourseSummary | null>(null);
  const [batchResult, setBatchResult] = useState<BatchDownloadResult | null>(null);
  const [state, setState] = useState<LoadState>('idle');
  const [downloadDir, setDownloadDir] = useState(DEFAULT_DOWNLOAD_DIR);
  const [message, setMessage] = useState('숭실 LMS 계정으로 로그인하면 강의 목록을 불러옵니다.');
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [isSessionVerified, setIsSessionVerified] = useState(false);
  const [isCourseDetailLoading, setIsCourseDetailLoading] = useState(false);
  const [isStartingDownload, setIsStartingDownload] = useState(false);
  const [isOpeningViewer, setIsOpeningViewer] = useState(false);
  const [isOpeningFolder, setIsOpeningFolder] = useState(false);
  const [watchDebugEvents, setWatchDebugEvents] = useState<WatchDebugEvent[]>([]);
  const [postprocessInputPath, setPostprocessInputPath] = useState('');
  const [audioJob, setAudioJob] = useState<PostprocessJobStatus<AudioExtractionResult> | null>(null);
  const [sttJob, setSttJob] = useState<PostprocessJobStatus<TranscriptionResult> | null>(null);
  const [sttProviders, setSttProviders] = useState<STTProviderStatus[]>([]);
  const [runtimeDependencies, setRuntimeDependencies] = useState<RuntimeDependencyStatus[]>([]);
  const [dependencyAutoInstallAvailable, setDependencyAutoInstallAvailable] = useState(false);
  const [dependencyAutoInstallUnavailableReason, setDependencyAutoInstallUnavailableReason] = useState<string | null>(null);
  const [dependencyInstallJob, setDependencyInstallJob] = useState<PostprocessJobStatus<MediaDependencyInstallResult> | null>(null);

  async function loadCredentialStatus(): Promise<CredentialStatus | null> {
    try {
      const status = await fetchCredentialStatus();
      setCredentialStatus(status);
      setIsSessionVerified(false);
      if (!status.username_present || !status.password_present) setIsSessionVerified(false);
      return status;
    } catch {
      setCredentialStatus(null);
      return null;
    }
  }

  async function initializeDownloadDir() {
    try {
      setDownloadDir(await defaultDownloadDirectory());
    } catch {
      setDownloadDir(DEFAULT_DOWNLOAD_DIR);
    }
  }

  async function loadBackendMode() {
    try {
      setBackendMode(await backendSupervisionStatus());
    } catch {
      setBackendMode('unknown');
    }
  }

  function clearSessionUi() {
    setTerms([]);
    setCourses([]);
    setLectures([]);
    setContentItems([]);
    setSelectedCourse(null);
    setSelectedLecture(null);
    setSelectedLectureIds([]);
    setDownloadResult(null);
    setDownloadJob(null);
    setBatchResult(null);
    setAudioJob(null);
    setSttJob(null);
    setPostprocessInputPath('');
    setIsSessionVerified(false);
    setState('idle');
  }

  async function returnToLoginAfterAuthenticationFailure(error: unknown) {
    clearSessionUi();
    try {
      const status = await deleteCredentials();
      setCredentialStatus(status);
    } catch {
      setCredentialStatus({
        ok: true,
        source: 'app',
        username_present: false,
        password_present: false,
        can_write: true,
      });
      setIsSessionVerified(false);
    }
    setState('error');
    setMessage(error instanceof Error ? error.message : '로그인에 실패했습니다.');
  }

  async function handlePickDownloadDir() {
    const selected = await pickDownloadDirectory();
    if (selected) {
      setDownloadDir(selected);
      setMessage('다운로드 폴더를 Finder에서 선택했습니다.');
    }
  }

  async function handleCredentialSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isAuthenticating) return;
    const formData = new FormData(event.currentTarget);
    const username = String(formData.get('username') ?? '');
    const password = String(formData.get('password') ?? '');
    setIsAuthenticating(true);
    setState('loading');
    setMessage('로그인하고 강의 목록을 불러오는 중입니다...');
    try {
      const status = await saveCredentials(username, password);
      setCredentialStatus(status);
      credentialFormRef.current?.reset();
      setMessage('로그인했습니다. 강의 목록을 불러오는 중입니다...');
      const currentTermId = await loadTerms();
      await loadCourses(currentTermId);
    } catch (error) {
      if (isAuthenticationError(error)) {
        await returnToLoginAfterAuthenticationFailure(error);
      } else {
        setState('error');
        setMessage(error instanceof Error ? error.message : '로그인 정보 저장 실패');
      }
    } finally {
      setIsAuthenticating(false);
    }
  }

  function clearLectureSelection() {
    setSelectedLecture(null);
    setSelectedLectureIds([]);
    setDownloadResult(null);
    setDownloadJob(null);
    setDownloadStartError(null);
    setDownloadDiagnostics([]);
    setBatchResult(null);
  }

  function appendDownloadDiagnostic(stage: string, details?: Record<string, unknown>) {
    const safeDetails = details ? Object.fromEntries(
      Object.entries(details).filter(([key]) => !/password|token|cookie|credential|url/i.test(key))
    ) : null;
    const timestamp = new Date().toISOString();
    const line = safeDetails && Object.keys(safeDetails).length > 0
      ? `${timestamp} ${stage} ${JSON.stringify(safeDetails)}`
      : `${timestamp} ${stage}`;
    setDownloadDiagnostics((current) => {
      const next = [line, ...current].slice(0, 30);
      try {
        window.localStorage.setItem('ssu-download-diagnostics', JSON.stringify(next));
      } catch {
        // Diagnostics are best-effort; never break the download UI because storage is unavailable.
      }
      return next;
    });
    console.info('[ssu-download]', line);
  }

  function handleWeekFilterChange(value: string) {
    setWeekFilter(value);
    clearLectureSelection();
  }

  function handleContentKindFilterChange(value: string) {
    setContentKindFilter(value);
    clearLectureSelection();
  }

  function handleCompletionFilterChange(value: string) {
    setCompletionFilter(value);
    clearLectureSelection();
  }

  async function handleCredentialDelete() {
    if (isLoggingOut) return;
    setIsLoggingOut(true);
    try {
      const status = await deleteCredentials();
      setCredentialStatus(status);
      clearSessionUi();
      setMessage('저장된 로그인 정보를 삭제했습니다.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '로그인 정보 삭제 실패');
    } finally {
      setIsLoggingOut(false);
    }
  }

  async function loadCourses(termId: number | null = selectedTermId) {
    setState('loading');
    setMessage(termId === null ? '현재 학기 과목 조회 중...' : `선택한 학기 ${termId} 과목 조회 중...`);
    setDownloadResult(null);
    setDownloadJob(null);
    setBatchResult(null);
    setAudioJob(null);
    setSttJob(null);
    setPostprocessInputPath('');
    try {
      const response = await fetchCourses(termId);
      setCourses(response.courses);
      setSelectedCourse(response.courses[0] ?? null);
      setIsSessionVerified(true);
      setState('ready');
      setMessage(termId === null ? `현재 학기 과목 ${response.course_count}개를 불러왔습니다.` : `선택한 학기 ${termId} 과목 ${response.course_count}개를 불러왔습니다.`);
    } catch (error) {
      if (isAuthenticationError(error)) {
        await returnToLoginAfterAuthenticationFailure(error);
        return;
      }
      setState('error');
      setMessage(error instanceof Error ? error.message : '과목 조회 실패');
    }
  }

  async function loadTerms(): Promise<number | null> {
    try {
      const response = await fetchTerms();
      setTerms(response.terms);
      const current = response.terms.find((term) => term.is_current);
      const currentTermId = current?.term_id ?? null;
      setSelectedTermId(currentTermId);
      return currentTermId;
    } catch (error) {
      if (isAuthenticationError(error)) {
        await returnToLoginAfterAuthenticationFailure(error);
        throw error;
      }
      setTerms([]);
      return null;
    }
  }

  async function handleLoadSelectedTerm() {
    if (state === 'loading' || isCourseDetailLoading) return;
    await loadCourses(selectedTermId);
  }

  async function selectCourse(course: CourseSummary) {
    if (state === 'loading' || isCourseDetailLoading) return;
    setIsCourseDetailLoading(true);
    setSelectedCourse(course);
    setSelectedLecture(null);
    setSelectedLectureIds([]);
    setDownloadResult(null);
    setDownloadJob(null);
    setBatchResult(null);
    setMessage(`${course.name} 강의 목록 조회 중...`);
    try {
      const detail = await fetchCourseDetail(course.id, course.name);
      const normalizedContentItems = detail.content_items?.length ? detail.content_items : detail.lectures.map(legacyLectureToContentItem);
      setLectures(detail.lectures);
      setContentItems(normalizedContentItems);
      const firstDownloadableVideo = normalizedContentItems.find((item) => item.downloadable && item.kind === 'video' && item.module_item_id !== null) ?? null;
      const firstDownloadable = firstDownloadableVideo ?? normalizedContentItems.find((item) => item.downloadable && item.module_item_id !== null) ?? null;
      setSelectedLecture(firstDownloadable ?? normalizedContentItems[0] ?? null);
      setSelectedLectureIds([]);
      setMessage(firstDownloadableVideo ? `${course.name} 콘텐츠 ${normalizedContentItems.length}개를 불러왔습니다. 다운로드할 영상을 직접 선택하세요.` : firstDownloadable ? `${course.name} 콘텐츠 ${normalizedContentItems.length}개를 불러왔습니다. 다운로드 가능한 자료가 있지만 영상 항목이 없습니다.` : `${course.name} 콘텐츠 ${normalizedContentItems.length}개를 불러왔습니다. 다운로드할 항목을 선택하세요.`);
    } catch (error) {
      if (isAuthenticationError(error)) {
        await returnToLoginAfterAuthenticationFailure(error);
        return;
      }
      setMessage(error instanceof Error ? error.message : '강의 목록 조회 실패');
    } finally {
      setIsCourseDetailLoading(false);
    }
  }

  function resolveConflictStrategy(selectedIds: Array<string | number>): ConflictStrategy {
    appendDownloadDiagnostic('conflict_check.skipped_default_skip', { selected_count: selectedIds.length });
    return 'skip';
  }

  async function handleDownloadJobStart() {
    if (isStartingDownload || isDownloadActive) return;
    if (!selectedCourse) {
      setMessage('먼저 과목을 선택해 주세요.');
      return;
    }
    if (selectedLectureIds.length === 0) {
      setMessage('다운로드할 강의를 선택해 주세요.');
      return;
    }
    const selectedIds = [...effectiveSelectedIds];
    if (selectedIds.length === 0) {
      setMessage('영상/음성/자막 파일은 영상 강의에서만 만들 수 있습니다. 영상 항목을 선택해 주세요.');
      return;
    }
    setIsStartingDownload(true);
    setDownloadResult(null);
    setBatchResult(null);
    setDownloadJob(null);
    setDownloadStartError(null);
    setDownloadDiagnostics([]);
    setMessage('저장 경로를 확인하는 중입니다.');
    try {
      const normalizedOutputs = selectedOutputFormats.length === 0 ? ['mp4'] as DownloadOutputFormat[] : selectedOutputFormats;
      appendDownloadDiagnostic('click.accepted', {
        outputs: normalizedOutputs.join('+'),
        selected_count: selectedIds.length,
        course_id: selectedCourse.id,
        output_dir_present: Boolean(downloadDir.trim()),
        tauri: isTauriRuntime(),
      });
      if ((normalizedOutputs.includes('mp3') || normalizedOutputs.includes('txt')) && ffmpegDependency && !ffmpegDependency.available) {
        const text = `MP3/TXT 생성에는 ffmpeg가 필요합니다. ${ffmpegDependency.install_hint ?? '앱을 재설치한 뒤 다시 시도하세요.'}`;
        appendDownloadDiagnostic('dependency.blocked', { component: 'ffmpeg' });
        setDownloadStartError(text);
        setMessage(text);
        return;
      }
      if (normalizedOutputs.includes('txt') && whisperStatus && !whisperStatus.available) {
        const text = `TXT 생성에는 STT 엔진 설치가 필요합니다. ${whisperStatus.install_hint ?? '앱을 재설치한 뒤 다시 시도하세요.'}`;
        appendDownloadDiagnostic('dependency.blocked', { component: 'stt' });
        setDownloadStartError(text);
        setMessage(text);
        return;
      }
      const conflictStrategy = resolveConflictStrategy(selectedIds);
      appendDownloadDiagnostic('conflict_check.done', { strategy: conflictStrategy });
      setMessage(conflictStrategy === 'skip' ? '기존 파일은 건너뛰고 다운로드를 시작했습니다.' : '다운로드를 시작했습니다. 진행률은 아래에서 확인할 수 있습니다.');
      appendDownloadDiagnostic('job_start.start', {
        mode: normalizedOutputs.length === 1 && normalizedOutputs[0] === 'mp4' ? (selectedIds.length === 1 ? 'single_mp4' : 'batch_mp4') : 'artifact',
      });
      const job = normalizedOutputs.length === 1 && normalizedOutputs[0] === 'mp4'
        ? selectedIds.length === 1
          ? await startDownloadJob(selectedCourse.id, selectedIds[0], selectedCourse.name, downloadDir, conflictStrategy)
          : await startBatchDownloadJob(selectedCourse.id, selectedCourse.name, selectedIds, downloadDir, conflictStrategy)
        : await startArtifactDownloadJob(selectedCourse.id, selectedCourse.name, selectedIds, downloadDir, normalizedOutputs, conflictStrategy);
      appendDownloadDiagnostic('job_start.done', { job_id: job.job_id, status: job.status });
      setDownloadJob(job);
      setMessage(`${downloadStageLabel(job.status)} 상태입니다.`);
    } catch (error) {
      if (isAuthenticationError(error)) {
        appendDownloadDiagnostic('job_start.auth_error', { code: error.code });
        await returnToLoginAfterAuthenticationFailure(error);
        return;
      }
      const text = error instanceof Error ? error.message : '다운로드 시작 실패';
      appendDownloadDiagnostic('job_start.failed', { error: text });
      setDownloadStartError(text);
      setMessage(text);
    } finally {
      setIsStartingDownload(false);
    }
  }

  async function handleCancelDownloadJob() {
    if (!downloadJob) return;
    const job = await cancelDownloadJob(downloadJob.job_id);
    setDownloadJob(job);
    setMessage('취소를 요청했습니다. 잠시 후 상태가 바뀝니다.');
  }

  async function handleOpenOfficialViewer(lecture: CourseContentItem | null = selectedLecture) {
    if (!selectedCourse || lecture?.module_item_id === null || lecture?.module_item_id === undefined || isOpeningViewer) return;
    setSelectedLecture(lecture);
    setWatchDebugEvents([]);
    setIsOpeningViewer(true);
    setMessage('공식 시청 창을 준비하는 중입니다...');
    try {
      if (isTauriRuntime()) {
        const plan = await planOfficialViewerSessionBridge(selectedCourse.id, lecture.module_item_id, selectedCourse.name);
        if (!plan.viewer_url) throw new Error('공식 시청 주소를 찾지 못했습니다.');
        await openOfficialViewer(plan.viewer_url, plan.title ?? lecture.title ?? '숭실 LMS 공식 시청 창', plan);
      } else {
        const plan = await planOfficialViewer(selectedCourse.id, lecture.module_item_id, selectedCourse.name);
        if (!plan.viewer_url) throw new Error('공식 시청 주소를 찾지 못했습니다.');
        await openOfficialViewer(plan.viewer_url, plan.title ?? lecture.title ?? '숭실 LMS 공식 시청 창');
      }
      setMessage(isTauriRuntime() ? '시청 창을 앞으로 가져왔습니다. 재생/진도 처리는 숭실 LMS 공식 플레이어가 그대로 수행합니다.' : '브라우저 개발 모드라 공식 시청 창을 새 탭으로 열었습니다. 앱 내부 시청은 Tauri 데스크톱 앱에서 동작합니다.');
    } catch (error) {
      if (isAuthenticationError(error)) {
        await returnToLoginAfterAuthenticationFailure(error);
        return;
      }
      setMessage(error instanceof Error ? error.message : '공식 시청 창을 열 수 없습니다.');
    } finally {
      setIsOpeningViewer(false);
    }
  }

  async function openWatchQueueItem(index: number) {
    const course = selectedCourseRef.current;
    const lecture = watchQueueRef.current[index];
    if (!course || !lecture || lecture.module_item_id === null || lecture.module_item_id === undefined) return;
    watchQueueIndexRef.current = index;
    setWatchDebugEvents([]);
    setWatchQueueState({ active: true, index, total: watchQueueRef.current.length });
    setSelectedLecture(lecture);
    setMessage(`순차 시청 ${index + 1}/${watchQueueRef.current.length}: 공식 플레이어를 열고 자동 재생을 시도합니다.`);
    try {
      const plan = await planOfficialViewerSessionBridge(course.id, lecture.module_item_id, course.name);
      if (!plan.viewer_url) throw new Error('공식 시청 주소를 찾지 못했습니다.');
      await openAssistedOfficialViewer(plan.viewer_url, plan.title ?? lecture.title ?? '숭실 LMS 공식 시청 창', plan);
    } catch (error) {
      watchQueueActiveRef.current = false;
      setWatchQueueState((current) => ({ ...current, active: false }));
      if (isAuthenticationError(error)) {
        await returnToLoginAfterAuthenticationFailure(error);
        return;
      }
      setMessage(error instanceof Error ? error.message : '순차 시청 창을 열 수 없습니다.');
    }
  }

  async function handleStartSequentialWatch() {
    if (!selectedCourse || watchQueueState.active || isOpeningViewer || isDownloadActive || isListLoading) return;
    const queue = filteredContentItems.filter((lecture) => lecture.kind === 'video' && lecture.module_item_id !== null && lecture.module_item_id !== undefined);
    if (queue.length === 0) {
      setMessage('표시된 목록에 순차 시청할 영상이 없습니다. 필터를 영상/미완료 등으로 조정해 주세요.');
      return;
    }
    watchQueueRef.current = queue;
    watchQueueActiveRef.current = true;
    selectedCourseRef.current = selectedCourse;
    await openWatchQueueItem(0);
  }

  function handleStopSequentialWatch() {
    watchQueueActiveRef.current = false;
    setWatchQueueState((current) => ({ ...current, active: false }));
    setMessage('순차 시청 모드를 중지했습니다. 현재 열린 공식 시청 창은 직접 닫아 주세요.');
  }

  function toggleLectureSelection(lecture: CourseContentItem) {
    if (!lecture.downloadable || lecture.module_item_id === null) return;
    setSelectedLectureIds((current) => (
      current.some((id) => String(id) === String(lecture.module_item_id))
        ? current.filter((id) => String(id) !== String(lecture.module_item_id))
        : [...current, lecture.module_item_id!]
    ));
  }

  function handleSelectAllLectures(checked?: boolean) {
    const selectableIds = selectedOutputRequiresVideo
      ? selectableVideoIds
      : filteredContentItems
        .filter((lecture) => lecture.downloadable && lecture.module_item_id !== null)
        .map((lecture) => lecture.module_item_id!);
    const selectedKeys = new Set(selectedLectureIds.map((id) => String(id)));
    const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selectedKeys.has(String(id)));
    const shouldSelectAll = checked ?? !allSelected;
    setSelectedLectureIds(shouldSelectAll ? selectableIds : []);
    if (shouldSelectAll && selectableIds.length > 0) {
      setSelectedLecture(filteredContentItems.find((lecture) => lecture.downloadable && lecture.module_item_id !== null) ?? null);
    }
  }

  useEffect(() => {
    selectedCourseRef.current = selectedCourse;
  }, [selectedCourse]);

  useEffect(() => {
    if (!isTauriRuntime()) return;
    let unlisten: (() => void) | null = null;
    void listen<{ stage: string }>('official-watch-ended', () => {
      if (!watchQueueActiveRef.current) return;
      const nextIndex = watchQueueIndexRef.current + 1;
      if (nextIndex >= watchQueueRef.current.length) {
        watchQueueActiveRef.current = false;
        setWatchQueueState((current) => ({ ...current, active: false, index: current.total }));
        setMessage('순차 시청 모드가 끝났습니다. 공식 플레이어가 종료 신호를 보낸 항목까지만 다음으로 이동했습니다.');
        return;
      }
      void openWatchQueueItem(nextIndex);
    }).then((handler) => {
      unlisten = handler;
    });
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  useEffect(() => {
    if (!isTauriRuntime()) return;
    let unlisten: (() => void) | null = null;
    void listen<WatchDebugEvent>('official-watch-debug', (event) => {
      setWatchDebugEvents((current) => [event.payload, ...current].slice(0, 40));
    }).then((handler) => {
      unlisten = handler;
    });
    return () => {
      if (unlisten) unlisten();
    };
  }, []);

  async function loadMediaDependencies() {
    try {
      const status = await fetchMediaDependencyStatus();
      setRuntimeDependencies(status.dependencies);
      setSttProviders(status.providers);
      setDependencyAutoInstallAvailable(Boolean(status.auto_install_available));
      setDependencyAutoInstallUnavailableReason(status.auto_install_unavailable_reason ?? null);
    } catch {
      setRuntimeDependencies([]);
      setDependencyAutoInstallAvailable(false);
      setDependencyAutoInstallUnavailableReason('구성요소 상태 API에 연결할 수 없습니다. 앱/API 서버를 재시작해 주세요.');
      await fetchSttProviderStatus().then((response) => setSttProviders(response.providers)).catch(() => setSttProviders([]));
    }
  }

  useEffect(() => {
    void initializeDownloadDir();
    void loadBackendMode();
    void loadMediaDependencies();
    void (async () => {
      const credential = await loadCredentialStatus();
      if (!credential?.username_present || !credential?.password_present) {
        setIsSessionVerified(false);
        setMessage('숭실 LMS 계정으로 로그인하면 강의 목록을 불러옵니다.');
        return;
      }
      setIsSessionVerified(false);
      setState('loading');
      setMessage('저장된 로그인 정보로 LMS 세션을 확인하는 중입니다...');
      try {
        const currentTermId = await loadTerms();
        void loadCourses(currentTermId);
      } catch {
        // Authentication failures are already routed back to the login screen by loadTerms.
      }
    })();
  }, []);

  useEffect(() => {
    if (selectedCourse) {
      void selectCourse(selectedCourse);
    }
  }, [selectedCourse?.id]);

  useEffect(() => {
    if (!downloadJob || TERMINAL_DOWNLOAD_STATUSES.has(downloadJob.status)) return;
    const timer = window.setInterval(() => {
      fetchDownloadJob(downloadJob.job_id)
        .then(async (job) => {
          setDownloadJob(job);
          if (job.status === 'failed' && job.error_code && isAuthenticationError(new ApiError(job.error_code, job.error_code))) {
            await returnToLoginAfterAuthenticationFailure(new ApiError(job.error_code, '로그인 세션이 만료되었습니다. 다시 로그인해 주세요.'));
            return;
          }
          const payload = downloadPayloadFromArtifact(job.result);
          if (isBatchDownloadResult(payload)) {
            setBatchResult(payload);
          } else if (payload) {
            setDownloadResult(payload);
          }
          if (TERMINAL_DOWNLOAD_STATUSES.has(job.status)) setMessage(`${downloadStageLabel(job.status)} 상태입니다.`);
        })
        .catch(async (error) => {
          if (isAuthenticationError(error)) {
            await returnToLoginAfterAuthenticationFailure(error);
            return;
          }
          setMessage(error instanceof Error ? error.message : '작업 상태 조회 실패');
        });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [downloadJob?.job_id, downloadJob?.status]);

  useEffect(() => {
    if (!audioJob || TERMINAL_DOWNLOAD_STATUSES.has(audioJob.status)) return;
    const timer = window.setInterval(() => {
      fetchAudioExtractionJob(audioJob.job_id)
        .then((job) => {
          setAudioJob(job);
          if (TERMINAL_DOWNLOAD_STATUSES.has(job.status)) setMessage(job.status === 'succeeded' ? '음성 파일 추출이 끝났습니다.' : '음성 추출 작업이 종료되었습니다.');
        })
        .catch((error) => setMessage(error instanceof Error ? error.message : '음성 추출 상태 조회 실패'));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [audioJob?.job_id, audioJob?.status]);

  useEffect(() => {
    if (!sttJob || TERMINAL_DOWNLOAD_STATUSES.has(sttJob.status)) return;
    const timer = window.setInterval(() => {
      fetchSttJob(sttJob.job_id)
        .then((job) => {
          setSttJob(job);
          if (TERMINAL_DOWNLOAD_STATUSES.has(job.status)) setMessage(job.status === 'succeeded' ? 'STT 파일 생성이 끝났습니다.' : 'STT 작업이 종료되었습니다.');
        })
        .catch((error) => setMessage(error instanceof Error ? error.message : 'STT 상태 조회 실패'));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [sttJob?.job_id, sttJob?.status]);

  useEffect(() => {
    if (!dependencyInstallJob || TERMINAL_DOWNLOAD_STATUSES.has(dependencyInstallJob.status)) return;
    const timer = window.setInterval(() => {
      fetchMediaDependencyInstallJob(dependencyInstallJob.job_id)
        .then(async (job) => {
          setDependencyInstallJob(job);
          if (job.status === 'succeeded') {
            setMessage(job.result?.message ?? '구성요소 설치가 끝났습니다.');
            await loadMediaDependencies();
          } else if (job.status === 'failed') {
            setMessage(job.result?.message ?? '구성요소 자동 설치에 실패했습니다.');
          }
        })
        .catch((error) => setMessage(error instanceof Error ? error.message : '구성요소 설치 상태 조회 실패'));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [dependencyInstallJob?.job_id, dependencyInstallJob?.status]);

  const availableWeeks = useMemo(() => Array.from(new Set(contentItems.map((item) => String(item.week_position ?? item.module_name ?? 'unknown')))), [contentItems]);
  const filteredContentItems = useMemo(() => contentItems.filter((item) => {
    const weekKey = String(item.week_position ?? item.module_name ?? 'unknown');
    if (weekFilter !== 'all' && weekKey !== weekFilter) return false;
    if (contentKindFilter === 'downloadable' && !item.downloadable) return false;
    if (contentKindFilter !== 'all' && contentKindFilter !== 'downloadable' && item.kind !== contentKindFilter) return false;
    if (completionFilter === 'completed' && item.completed !== true) return false;
    if (completionFilter === 'uncompleted' && item.completed !== false) return false;
    if (completionFilter === 'unknown' && item.completed !== null) return false;
    return true;
  }), [contentItems, contentKindFilter, completionFilter, weekFilter]);
  const completedCount = useMemo(() => contentItems.filter((lecture) => lecture.completed === true).length, [contentItems]);
  const selectableLectureIds = useMemo(() => filteredContentItems.filter((lecture) => lecture.downloadable && lecture.module_item_id !== null).map((lecture) => lecture.module_item_id!), [filteredContentItems]);
  const selectedKeys = useMemo(() => new Set(selectedLectureIds.map((id) => String(id))), [selectedLectureIds]);
  const selectedContentItems = useMemo(() => contentItems.filter((item) => item.module_item_id !== null && selectedKeys.has(String(item.module_item_id))), [contentItems, selectedKeys]);
  const selectedVideoItems = useMemo(() => selectedContentItems.filter((item) => item.kind === 'video'), [selectedContentItems]);
  const selectedOutputRequiresVideo = selectedOutputFormats.some((format) => format === 'mp4' || format === 'mp3' || format === 'txt');
  const selectableVideoIds = useMemo(() => filteredContentItems.filter((lecture) => lecture.downloadable && lecture.kind === 'video' && lecture.module_item_id !== null).map((lecture) => lecture.module_item_id!), [filteredContentItems]);
  const visibleSelectableIds = selectedOutputRequiresVideo ? selectableVideoIds : selectableLectureIds;
  const selectableCount = visibleSelectableIds.length;
  const selectedCount = selectedOutputRequiresVideo ? selectedVideoItems.length : selectedLectureIds.length;
  const allLecturesSelected = selectableCount > 0 && visibleSelectableIds.every((id) => selectedKeys.has(String(id)));
  const activeJobResult = downloadJob?.result;
  const activeDownloadPayload = downloadPayloadFromArtifact(activeJobResult);
  const activeArtifactResult = isArtifactDownloadResult(activeJobResult) ? activeJobResult : null;
  const activeBatchResult = isBatchDownloadResult(activeDownloadPayload) ? activeDownloadPayload : batchResult;
  const activeSingleResult = activeDownloadPayload && !isBatchDownloadResult(activeDownloadPayload) ? activeDownloadPayload : downloadResult;
  const batchPosition = latestBatchPosition(downloadJob?.progress ?? activeBatchResult?.progress ?? []);
  const batchPrepareProgress = batchPreparationProgress(downloadJob?.progress ?? activeBatchResult?.progress ?? []);
  const batchDownloadInfo = batchDownloadStart(downloadJob?.progress ?? activeBatchResult?.progress ?? []);
  const artifactAudioInfo = artifactAudioProgress(downloadJob?.progress ?? activeArtifactResult?.progress ?? []);
  const artifactTranscriptInfo = artifactTranscriptProgress(downloadJob?.progress ?? activeArtifactResult?.progress ?? []);
  const activeProgressEvents = downloadJob?.progress ?? activeSingleResult?.progress ?? activeBatchResult?.progress ?? [];
  const perFileProgress = fileProgressRows(downloadJob?.progress ?? activeBatchResult?.progress ?? [], activeBatchResult);
  const isDownloadActive = Boolean(downloadJob && !TERMINAL_DOWNLOAD_STATUSES.has(downloadJob.status));
  const effectiveSelectedIds = selectedOutputRequiresVideo ? selectedVideoItems.map((item) => item.module_item_id!).filter((id) => id !== null && id !== undefined) : selectedLectureIds;
  const selectedOutputLabel = selectedOutputFormats.map((item) => item.toUpperCase()).join('+') || 'MP4';
  const downloadPrimaryActionLabel = isStartingDownload ? '다운로드 준비 중...' : isDownloadActive ? '취소하기' : selectedOutputRequiresVideo && effectiveSelectedIds.length === 0 ? '영상을 선택하세요' : selectedCount === 0 ? '강의를 선택하세요' : effectiveSelectedIds.length > 1 ? `${effectiveSelectedIds.length}개 ${selectedOutputLabel} 다운로드 시작` : `${selectedOutputLabel} 다운로드 시작`;
  const downloadProgress = latestByteProgress(activeProgressEvents);
  const responseDiagnostics = responseDiagnosticText(latestResponseDiagnostics(activeProgressEvents));
  const aggregateProgress = aggregateFileProgress(perFileProgress, downloadProgress.percent);
  const downloadProgressPercent = downloadJob?.status === 'succeeded'
    ? 100
    : perFileProgress.length > 0
      ? aggregateProgress.percent
      : downloadJob
        ? downloadProgress.percent
        : activeSingleResult?.written || activeBatchResult?.items.some((item) => item.written || item.skipped)
          ? 100
          : downloadProgress.percent;
  const batchDownloadPhasePercent = batchDownloadInfo.skipped ? 100 : batchDownloadInfo.started ? downloadProgress.percent : 0;
  const batchDownloadPhaseLabel = batchDownloadInfo.skipped ? '기존 파일 사용' : batchDownloadInfo.started ? `최대 ${batchDownloadInfo.parallelism ?? 4}개 동시` : '대기 중';
  const downloadStatusLabel = downloadStartError && !downloadJob
    ? '실패'
    : isStartingDownload && !downloadJob
    ? '준비 중'
    : downloadStageLabel(downloadJob?.status, activeProgressEvents);
  const downloadProgressMessage = downloadStartError && !downloadJob
    ? '다운로드를 시작하지 못했습니다. 아래 진단 로그의 마지막 단계를 확인해 주세요.'
    : isStartingDownload && !downloadJob
    ? '저장 위치와 파일 정보를 확인하는 중입니다. 잠시만 기다려 주세요.'
    : downloadJob
    ? `${latestUserStage(downloadJob.progress)}${batchPosition.total ? ` (${batchPosition.index}/${batchPosition.total})` : ''}`
    : activeSingleResult?.written || activeBatchResult?.items.some((item) => item.written)
      ? '저장이 끝났습니다.'
      : '강의를 선택하고 다운로드를 시작하세요.';
  const artifactOutputPath = activeArtifactResult?.artifacts.find((item) => typeof item.output_path === 'string')?.output_path ?? activeArtifactResult?.artifacts.find((item) => typeof item.text_path === 'string')?.text_path;
  const artifactError = activeArtifactResult?.artifacts.find((item) => item && item.ok === false);
  const artifactErrorText = artifactError ? [artifactError.message, artifactError.error_code].filter(Boolean).join(' · ') : null;
  const downloadOutputPath = artifactOutputPath ?? activeSingleResult?.output_path ?? activeBatchResult?.items.find((item) => item.output_path)?.output_path ?? downloadDir;
  const shouldShowDownloadProgress = Boolean(isStartingDownload || downloadStartError || downloadJob || activeSingleResult || activeBatchResult) || Boolean(activeArtifactResult);
  const canOpenDownloadedFolder = Boolean(activeSingleResult?.written || activeBatchResult?.items.some((item) => item.written) || downloadJob?.status === 'succeeded');
  const isCourseListLoading = state === 'loading';
  const isListLoading = isCourseListLoading || isCourseDetailLoading;
  const canOpenLectureOfficialViewer = (lecture: CourseContentItem | null | undefined) => Boolean(selectedCourse && lecture?.kind === 'video' && lecture.module_item_id !== null && lecture.module_item_id !== undefined && !isDownloadActive && !isListLoading);
  const postprocessVideoPath = postprocessInputPath || (activeSingleResult?.written ? activeSingleResult.output_path ?? '' : '');
  const extractedAudioPath = audioJob?.result?.output_path ?? '';
  const transcriptTextPath = sttJob?.result?.text_path ?? '';
  const whisperStatus = sttProviders.find((provider) => provider.provider === 'faster_whisper');
  const ffmpegDependency = runtimeDependencies.find((dependency) => dependency.name === 'ffmpeg');
  const whisperInstallHint = whisperStatus && !whisperStatus.available ? whisperStatus.install_hint : null;
  const ffmpegInstallHint = ffmpegDependency && !ffmpegDependency.available ? ffmpegDependency.install_hint : null;
  const missingMediaComponents = [
    ffmpegDependency && !ffmpegDependency.available ? 'ffmpeg' : null,
    whisperStatus && !whisperStatus.available ? 'faster_whisper' : null,
  ].filter((component): component is string => Boolean(component));
  const selectedOutputRequiresMediaDependencies = selectedOutputFormats.some((format) => format === 'mp3' || format === 'txt');
  const missingSelectedOutputComponents = selectedOutputRequiresMediaDependencies ? missingMediaComponents : [];
  const isInstallingDependencies = dependencyInstallJob?.status === 'queued' || dependencyInstallJob?.status === 'running';
  const canInstallMissingDependencies = missingMediaComponents.length > 0 && dependencyAutoInstallAvailable && !isInstallingDependencies && !isStartingDownload && !isDownloadActive;
  const mediaDependenciesReady = missingMediaComponents.length === 0;
  const mediaReadinessTitle = mediaDependenciesReady ? '영상·음성·자막 저장 준비 완료' : '음성/자막 저장 준비가 필요합니다';
  const mediaReadinessDetail = mediaDependenciesReady
    ? 'MP4, MP3, TXT를 바로 만들 수 있어요.'
    : dependencyAutoInstallAvailable
      ? '필요한 항목만 앱에서 자동으로 준비할 수 있어요.'
      : '앱을 다시 설치하거나 개발 환경을 동기화한 뒤 다시 확인해 주세요.';
  const canStartSelectedDownload = Boolean(selectedCourse) && effectiveSelectedIds.length > 0 && !isStartingDownload && !downloadJob?.cancel_requested && missingSelectedOutputComponents.length === 0;

  async function handleInstallMissingDependencies() {
    if (!canInstallMissingDependencies) return;
    setMessage('필요한 MP3/TXT 구성요소만 자동 설치를 시작합니다.');
    try {
      const job = await startMediaDependencyInstallJob(missingMediaComponents);
      setDependencyInstallJob(job);
      if (job.status === 'succeeded') {
        setMessage(job.result?.message ?? '구성요소 설치가 끝났습니다.');
        await loadMediaDependencies();
      } else if (job.status === 'failed') {
        setMessage(job.result?.message ?? '구성요소 자동 설치에 실패했습니다.');
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '구성요소 자동 설치를 시작하지 못했습니다.');
    }
  }

  function toggleOutputFormat(format: DownloadOutputFormat) {
    setSelectedOutputFormats((current) => {
      const next = current.includes(format) ? current.filter((item) => item !== format) : [...current, format];
      const normalized = next.length === 0 ? ['mp4'] as DownloadOutputFormat[] : next;
      const requiresVideo = normalized.some((item) => item === 'mp3' || item === 'txt');
      if (requiresVideo) {
        const selectedVideoIds = selectedContentItems
          .filter((item) => item.kind === 'video' && item.module_item_id !== null)
          .map((item) => item.module_item_id!);
        if (selectedVideoIds.length > 0) {
          setSelectedLectureIds(selectedVideoIds);
        } else if (selectableVideoIds.length > 0) {
          setSelectedLectureIds([selectableVideoIds[0]]);
          const firstVideo = filteredContentItems.find((item) => item.kind === 'video' && item.module_item_id !== null);
          if (firstVideo) setSelectedLecture(firstVideo);
        }
      }
      return normalized;
    });
  }

  async function handleDownloadPrimaryAction() {
    if (isDownloadActive) {
      await handleCancelDownloadJob();
      return;
    }
    await handleDownloadJobStart();
  }

  async function handleOpenDownloadedFolder() {
    if (isOpeningFolder) return;
    setIsOpeningFolder(true);
    try {
      await openDownloadedFolder(downloadOutputPath);
      setMessage('다운로드 폴더를 열었습니다.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '다운로드 폴더를 열 수 없습니다.');
    } finally {
      setIsOpeningFolder(false);
    }
  }

  async function handleExtractAudio() {
    const inputPath = postprocessVideoPath.trim();
    if (!inputPath) {
      setMessage('음성을 추출할 영상 파일 경로가 필요합니다.');
      return;
    }
    setMessage('영상 파일에서 음성 파일을 추출하는 중입니다.');
    const job = await startAudioExtractionJob(inputPath, 'wav', false);
    setAudioJob(job);
    if (job.status === 'failed' && job.result?.install_hint) setMessage(job.result.install_hint);
  }

  async function handleRunStt() {
    const audioPath = extractedAudioPath.trim();
    if (!audioPath) {
      setMessage('먼저 음성 추출을 완료해 주세요.');
      return;
    }
    setMessage('정밀 STT를 실행하는 중입니다. 처음 실행이면 Whisper large-v3 모델 준비가 오래 걸릴 수 있습니다.');
    const job = await startSttJob(audioPath, 'faster_whisper', 'large-v3', false);
    setSttJob(job);
    if (job.status === 'failed' && job.result?.install_hint) setMessage(job.result.install_hint);
  }

  async function handleOpenTranscriptFolder() {
    const path = transcriptTextPath || extractedAudioPath || postprocessVideoPath;
    if (!path) return;
    await openDownloadedFolder(path);
    setMessage('파일 위치를 Finder에서 열었습니다.');
  }

  const hasStoredCredential = Boolean(credentialStatus?.username_present && credentialStatus?.password_present);
  const shouldShowLogin = !hasStoredCredential || !isSessionVerified;

  if (shouldShowLogin) {
    return (
      <main className="app-shell login-shell">
        <section className="login-card panel">
          <div className="panel-header">
            <h2>숭실 LMS 로그인</h2>
            <span>{credentialStatus?.source ?? 'app'}</span>
          </div>
          <p className="hint">
            처음 실행하면 계정 정보만 입력하면 됩니다. 계정 정보는 이 기기의 앱 저장소에 보관하고, 로그인 세션은 앱 실행 중에만 재사용합니다.
          </p>
          <form ref={credentialFormRef} className="credential-form" onSubmit={handleCredentialSubmit}>
            <input name="username" autoComplete="username" placeholder="SSU LMS ID" aria-label="SSU LMS ID" disabled={isAuthenticating} />
            <input name="password" type="password" autoComplete="current-password" placeholder="SSU LMS password" aria-label="SSU LMS password" disabled={isAuthenticating} />
            <button type="submit" disabled={credentialStatus?.can_write === false || isAuthenticating}>{isAuthenticating ? '로그인 중...' : '로그인'}</button>
          </form>
          <p className={`status login-status ${state}`} role={state === 'error' ? 'alert' : 'status'}>{message}</p>
          <p className="hint">
            비밀번호는 저장 여부만 확인합니다. 화면에는 다시 표시하지 않습니다.
          </p>
        </section>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <section className="hero-card">
        <div>
          <p className="eyebrow">Soongsil LMS local downloader</p>
          <h1>현재 학기 강의 다운로드</h1>
          <p className="hero-copy">
            기본 화면은 LearningX의 최신 활성 학기만 보여줍니다. 과거 학기는 명시 선택 UX로 따로 열어야 합니다.
          </p>
        </div>
        <div className="hero-actions">
          <span>{backendMode === 'unknown' ? '앱 연결 확인 중' : '앱 연결됨'}</span>
          <button className="primary" onClick={() => loadCourses(selectedTermId)} disabled={isListLoading || isLoggingOut}>
            현재 학기 새로고침
          </button>
          <button className="secondary-action" type="button" onClick={handleCredentialDelete} disabled={!credentialStatus?.can_write || isLoggingOut || isListLoading || isDownloadActive}>
            {isLoggingOut ? '로그아웃 중...' : '로그아웃'}
          </button>
        </div>
      </section>

      <p className={`status ${state}`}>{message}</p>

      <section className="grid" aria-busy={isCourseListLoading}>
        <aside className="panel course-panel">
          <div className="panel-header">
            <h2>현재 학기 과목</h2>
            <span>{courses.length}개</span>
          </div>
          <div className="term-selector">
            <label htmlFor="term-select">학기 선택</label>
            <select id="term-select" value={selectedTermId ?? ''} onChange={(event) => setSelectedTermId(event.target.value ? Number(event.target.value) : null)} disabled={isListLoading || isDownloadActive}>
              <option value="">현재 학기 자동</option>
              {terms.map((term) => (
                <option key={term.term_id} value={term.term_id}>
                  {term.is_current ? '현재 ' : '과거 '}학기 {term.term_id} · 과목 {term.course_count}개
                </option>
              ))}
            </select>
            <button onClick={handleLoadSelectedTerm} disabled={isListLoading || isDownloadActive}>선택한 학기 불러오기</button>
          </div>
          <p className="hint">과거 학기는 학기 선택에서 명시적으로 불러옵니다.</p>
          <div className="course-list">
            {courses.map((course) => (
              <button
                key={course.id}
                className={`course-card ${selectedCourse?.id === course.id ? 'selected' : ''}`}
                onClick={() => setSelectedCourse(course)}
                disabled={isListLoading || isDownloadActive}
              >
                <strong>{course.name}</strong>
                <span>modules {course.module_count} · lectures {course.lecture_count}</span>
                <span>미완료 {course.uncompleted_lecture_count}</span>
              </button>
            ))}
          </div>
        </aside>

        <section className="panel lecture-panel" aria-busy={isCourseDetailLoading}>
          <div className="panel-header">
            <h2>강의 선택</h2>
            <span>{isCourseDetailLoading ? '불러오는 중...' : `${completedCount}/${contentItems.length} 완료`}</span>
          </div>
          <div className="lecture-filters">
            <select aria-label="주차 필터" value={weekFilter} onChange={(event) => handleWeekFilterChange(event.target.value)} disabled={isDownloadActive || isListLoading}>
              <option value="all">전체 주차</option>
              {availableWeeks.map((week) => <option key={week} value={week}>{week === 'unknown' ? '주차 정보 없음' : `${week}주차`}</option>)}
            </select>
            <select aria-label="타입 필터" value={contentKindFilter} onChange={(event) => handleContentKindFilterChange(event.target.value)} disabled={isDownloadActive || isListLoading}>
              <option value="downloadable">다운로드 가능</option>
              <option value="all">전체 타입</option>
              <option value="video">영상</option>
              <option value="material">PDF/PPT 자료</option>
              <option value="assignment">과제</option>
              <option value="quiz">퀴즈</option>
              <option value="board">게시판</option>
              <option value="page">페이지</option>
            </select>
            <select aria-label="완료 상태 필터" value={completionFilter} onChange={(event) => handleCompletionFilterChange(event.target.value)} disabled={isDownloadActive || isListLoading}>
              <option value="all">전체 완료 상태</option>
              <option value="completed">완료</option>
              <option value="uncompleted">미완료</option>
              <option value="unknown">완료 정보 없음</option>
            </select>
          </div>
          <div className="lecture-toolbar">
            <span>{selectedCount}/{selectableCount}개 선택됨 · 표시 {filteredContentItems.length}개</span>
            <label className="select-all-check">
              <input
                type="checkbox"
                checked={allLecturesSelected}
                disabled={selectableCount === 0 || isDownloadActive || isListLoading}
                onChange={(event) => handleSelectAllLectures(event.currentTarget.checked)}
              />
              전체 선택
            </label>
            {watchQueueState.active ? (
              <button type="button" className="secondary-action" onClick={handleStopSequentialWatch}>
                순차 시청 중지 ({Math.min(watchQueueState.index + 1, watchQueueState.total)}/{watchQueueState.total})
              </button>
            ) : (
              <button
                type="button"
                className="secondary-action"
                disabled={!selectedCourse || filteredContentItems.every((lecture) => lecture.kind !== 'video' || lecture.module_item_id === null || lecture.module_item_id === undefined) || isDownloadActive || isListLoading || isOpeningViewer}
                onClick={() => { void handleStartSequentialWatch(); }}
              >
                표시된 영상 순차 시청
              </button>
            )}
          </div>
          <div className="lecture-list">
            {isCourseDetailLoading && (
              <div className="inline-loading" role="status" aria-live="polite">
                <span className="mini-spinner" />
                <span>선택한 과목의 강의 목록을 불러오는 중입니다.</span>
              </div>
            )}
            {filteredContentItems.map((lecture) => {
              const checked = lecture.module_item_id !== null && selectedLectureIds.some((id) => String(id) === String(lecture.module_item_id));
              const canWatchLecture = canOpenLectureOfficialViewer(lecture);
              return (
                <div
                  key={`${lecture.module_item_id}-${lecture.title}`}
                  className={`lecture-row ${selectedLecture?.module_item_id === lecture.module_item_id ? 'selected' : ''} ${checked ? 'checked' : ''}`}
                  onClick={() => {
                    if (isDownloadActive || isListLoading) return;
                    setSelectedLecture(lecture);
                    toggleLectureSelection(lecture);
                  }}
                >
                  <label className="lecture-check" onClick={(event) => event.stopPropagation()}>
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={!lecture.downloadable || lecture.module_item_id === null || (selectedOutputRequiresVideo && lecture.kind !== 'video') || isDownloadActive || isListLoading}
                      onChange={() => toggleLectureSelection(lecture)}
                    />
                    <span>{lecture.downloadable ? selectedOutputRequiresVideo && lecture.kind !== 'video' ? '영상만' : '선택' : '보기 전용'}</span>
                  </label>
                  <span className="module-name">{lecture.module_name || '주차 정보 없음'}</span>
                  <strong>{lecture.title}</strong>
                  <span>{contentKindLabel(lecture.kind)} · {lecture.completed === null ? '완료 정보 없음' : lecture.completed ? '완료' : '미완료'} · {lecture.duration ? `길이 ${formatDuration(lecture.duration)} · ` : ''}{lecture.size_bytes ? `크기 ${formatBytes(lecture.size_bytes)} · ` : ''}{lecture.inner_content_type ?? lecture.content_type ?? 'type unknown'}</span>
                  {lecture.kind === 'video' && (
                    <button
                      className="inline-watch-button"
                      type="button"
                      disabled={!canWatchLecture || isOpeningViewer}
                      onClick={(event) => {
                        event.stopPropagation();
                        void handleOpenOfficialViewer(lecture);
                      }}
                    >
                      시청
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        <section className="panel action-panel">
          <div className="panel-header">
            <h2>다운로드</h2>
            <span>{selectedLecture ? contentKindLabel(selectedLecture.kind) : 'media'}</span>
          </div>
          <div className="selected-box">
            <p className="label">다운로드할 강의</p>
            <strong>{selectedCount === 0 ? '강의를 선택하세요' : selectedCount === 1 ? '1개 선택됨' : `${selectedCount}개 선택됨`}</strong>
            <span>{selectedCourse?.name ?? '-'} · 하나만 선택하면 단일 다운로드, 2개 이상이면 최대 4개씩 동시 다운로드합니다.</span>
          </div>
          <div className="output-format-box">
            <p className="label">저장할 파일 선택</p>
            <div className="output-format-options" aria-label="저장할 파일 형식 다중 선택">
              {([
                { format: 'mp4', title: '영상', detail: 'MP4' },
                { format: 'mp3', title: '음성', detail: 'MP3' },
                { format: 'txt', title: '자막', detail: 'TXT' },
              ] as Array<{ format: DownloadOutputFormat; title: string; detail: string }>).map(({ format, title, detail }) => (
                <label key={format} className="output-format-option">
                  <input
                    type="checkbox"
                    checked={selectedOutputFormats.includes(format)}
                    disabled={isStartingDownload || isDownloadActive}
                    onChange={() => toggleOutputFormat(format)}
                  />
                  <span>{title}</span>
                  <small>{detail}</small>
                </label>
              ))}
            </div>
            <p className="hint">필요한 파일만 고르면 됩니다. 음성이나 자막을 고르면 영상 다운로드 후 자동으로 이어서 만들어 줍니다.</p>
            <div className={`media-readiness-card ${mediaDependenciesReady ? 'ready' : 'needs-action'}`}>
              <div className="media-readiness-copy">
                <strong>{mediaReadinessTitle}</strong>
                <span>{mediaReadinessDetail}</span>
              </div>
              <div className="media-readiness-actions">
                <button type="button" onClick={() => void loadMediaDependencies()} disabled={isStartingDownload || isDownloadActive || isInstallingDependencies}>다시 확인</button>
                {missingMediaComponents.length > 0 && (
                  <button type="button" className="install-action" onClick={() => void handleInstallMissingDependencies()} disabled={!canInstallMissingDependencies}>
                    {isInstallingDependencies ? '준비 중...' : '자동 준비'}
                  </button>
                )}
              </div>
            </div>
            {!mediaDependenciesReady && (
              <details className="dependency-details">
                <summary>자세한 준비 상태</summary>
                <div>
                  <strong>음성 변환</strong>
                  <span>{ffmpegDependency?.available ? '사용 가능' : '준비 필요'}</span>
                </div>
                <div>
                  <strong>자막 생성</strong>
                  <span>{whisperStatus?.available ? '사용 가능' : '준비 필요'}</span>
                </div>
              </details>
            )}
            {missingSelectedOutputComponents.length > 0 && <p className="progress-error">선택한 파일을 만들려면 먼저 음성/자막 저장 준비를 완료해 주세요.</p>}
            {dependencyInstallJob && <p className="save-location"><b>자동 준비</b> {dependencyInstallJob.status === 'succeeded' ? '완료' : dependencyInstallJob.status === 'running' ? '진행 중' : dependencyInstallJob.status} · {dependencyInstallJob.result?.message ?? dependencyInstallJob.error_code ?? latestUserStage(dependencyInstallJob.progress)}</p>}
          </div>
          <label className="download-dir-field">
            <span>다운로드 폴더 <small>자동 기본값</small></span>
            <div className="download-dir-control">
              <input
                name="downloadDir"
                value={downloadDir}
                onChange={(event) => setDownloadDir(event.target.value)}
                placeholder="~/Downloads"
                disabled={isStartingDownload || isDownloadActive}
              />
              <button type="button" onClick={handlePickDownloadDir} disabled={!isTauriRuntime() || isStartingDownload || isDownloadActive}>Finder에서 선택</button>
            </div>
          </label>
          <p className="hint">선택하지 않아도 다운로드할 수 있습니다. Tauri 앱은 기본으로 macOS 다운로드 폴더인 ~/Downloads를 사용하고, 필요할 때 Finder에서 바꿀 수 있습니다.</p>
          <div className="actions">
            <button
              className={`download-primary ${isDownloadActive ? 'cancel' : ''}`}
              onClick={handleDownloadPrimaryAction}
              disabled={isDownloadActive ? Boolean(downloadJob?.cancel_requested) : !canStartSelectedDownload}
            >
              {missingSelectedOutputComponents.length > 0 ? '구성요소 확인 필요' : downloadPrimaryActionLabel}
            </button>
          </div>
          <p className="hint">각 영상 행의 시청 버튼은 숭실/Commons 공식 플레이어 창을 열고, 이미 열린 시청 창이 있으면 새로 만들지 않고 앞으로 가져옵니다.</p>
          {watchDebugEvents.length > 0 && (
            <details className="watch-debug-panel" open>
              <summary>공식 플레이어 재생 진단 로그 {watchDebugEvents.length}개</summary>
              <div className="watch-debug-list">
                {watchDebugEvents.slice(0, 12).map((event, index) => (
                  <div className="watch-debug-row" key={`${event.stage}-${index}`}>
                    <strong>{event.stage}</strong>
                    <span>{[event.mode, event.frame, event.host, event.ready_state ? `ready=${event.ready_state}` : null, event.videos ? `videos=${event.videos}` : null, event.buttons ? `buttons=${event.buttons}` : null].filter(Boolean).join(' · ')}</span>
                    {event.video_state && <code>{event.video_state}</code>}
                    {event.selector && <code>{event.selector}</code>}
                    {(event.error_name || event.error_message) && <em>{[event.error_name, event.error_message].filter(Boolean).join(': ')}</em>}
                  </div>
                ))}
              </div>
            </details>
          )}
          {shouldShowDownloadProgress && (
            <div className="download-progress-card" aria-live="polite">
              <div className="progress-card-header">
                <div>
                  <p className="label">다운로드 진행률</p>
                  <strong>{downloadStatusLabel}</strong>
                </div>
                <span>{progressLabelPercent(downloadProgressPercent)}</span>
              </div>
              <div
                className="progress-track"
                role="progressbar"
                aria-label="다운로드 진행률"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={downloadProgressPercent}
              >
                <div className="progress-fill" style={{ width: `${downloadProgressPercent}%` }} />
              </div>
              <p className="progress-message">{downloadProgressMessage}</p>
              {(batchPrepareProgress.total > 1 || artifactAudioInfo.started || artifactTranscriptInfo.started) && (
                <div className="phase-progress-list" aria-label="다운로드 단계">
                  {batchPrepareProgress.total > 1 && (
                    <div className={`phase-progress-row ${batchPrepareProgress.done ? 'done' : 'active'}`}>
                      <div className="phase-progress-title">
                        <strong>1. 파일 위치 확인</strong>
                        <span>{batchPrepareProgress.resolved}/{batchPrepareProgress.total}</span>
                      </div>
                      <div className="progress-track phase-track" role="progressbar" aria-label="파일 위치 확인 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={batchPrepareProgress.percent}>
                        <div className="progress-fill" style={{ width: progressLabelPercent(batchPrepareProgress.percent) }} />
                      </div>
                    </div>
                  )}
                  {batchPrepareProgress.total > 1 && (
                    <div className={`phase-progress-row ${batchDownloadInfo.skipped ? 'done' : batchDownloadInfo.started ? 'active' : ''}`}>
                      <div className="phase-progress-title">
                        <strong>2. 실제 파일 동시 다운로드</strong>
                        <span>{batchDownloadPhaseLabel}</span>
                      </div>
                      <div className="progress-track phase-track" role="progressbar" aria-label="실제 파일 다운로드 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={batchDownloadPhasePercent}>
                        <div className="progress-fill" style={{ width: progressLabelPercent(batchDownloadPhasePercent) }} />
                      </div>
                    </div>
                  )}
                  {artifactAudioInfo.started && (
                    <div className={`phase-progress-row ${artifactAudioInfo.done ? 'done' : 'active'}`}>
                      <div className="phase-progress-title">
                        <strong>{batchPrepareProgress.total > 1 ? '3' : '2'}. MP3 변환</strong>
                        <span>{artifactAudioInfo.current}/{artifactAudioInfo.total}</span>
                      </div>
                      <div className="progress-track phase-track" role="progressbar" aria-label="MP3 변환 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={artifactAudioInfo.percent}>
                        <div className="progress-fill" style={{ width: progressLabelPercent(artifactAudioInfo.percent) }} />
                      </div>
                    </div>
                  )}
                  {artifactTranscriptInfo.started && (
                    <div className={`phase-progress-row ${artifactTranscriptInfo.done ? 'done' : 'active'}`}>
                      <div className="phase-progress-title">
                        <strong>{batchPrepareProgress.total > 1 ? '4' : artifactAudioInfo.started ? '3' : '2'}. TXT 생성</strong>
                        <span>{artifactTranscriptInfo.current}/{artifactTranscriptInfo.total}</span>
                      </div>
                      <div className="progress-track phase-track" role="progressbar" aria-label="TXT 생성 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={artifactTranscriptInfo.percent}>
                        <div className="progress-fill" style={{ width: progressLabelPercent(artifactTranscriptInfo.percent) }} />
                      </div>
                    </div>
                  )}
                </div>
              )}
              <p className="save-location"><b>저장 위치</b> {downloadOutputPath}</p>
              {(aggregateProgress.written || downloadProgress.written || activeSingleResult?.download_size) && (
                <p className="progress-size">
                  {formatBytes(aggregateProgress.written ?? downloadProgress.written ?? activeSingleResult?.download_size)} / {formatBytes(aggregateProgress.total ?? downloadProgress.total ?? activeSingleResult?.download_size)}
                </p>
              )}
              {responseDiagnostics && <p className="progress-diagnostics">응답 진단: {responseDiagnostics}</p>}
              {perFileProgress.length > 0 && (
                <details className="file-progress-details" open={perFileProgress.length <= 5}>
                  <summary>파일별 진행률 {perFileProgress.filter((row) => row.percent >= 100).length}/{perFileProgress.length}</summary>
                  <div className="file-progress-list">
                    {perFileProgress.map((row) => (
                      <div className={`file-progress-row ${row.status === '실패' ? 'failed' : ''}`} key={row.key}>
                        <div className="file-progress-title">
                          <strong>{row.title}</strong>
                          <span>{row.status} · {progressLabelPercent(row.percent)}</span>
                        </div>
                        <div
                          className="progress-track file-track"
                          role="progressbar"
                          aria-label={`${row.title} 진행률`}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={row.percent}
                        >
                          <div className="progress-fill" style={{ width: progressLabelPercent(row.percent) }} />
                        </div>
                        <p className="file-progress-meta">
                          {formatBytes(row.written)} / {formatBytes(row.total)}{row.outputPath ? ` · ${row.outputPath}` : ''}{row.errorCode ? ` · ${row.errorCode}` : ''}
                        </p>
                      </div>
                    ))}
                  </div>
                </details>
              )}
              {artifactErrorText && <p className="progress-error">파일 생성 실패: {artifactErrorText}</p>}
              {downloadStartError && !downloadJob && <p className="progress-error">다운로드 시작 실패: {downloadStartError}</p>}
              {downloadJob?.error_code && <p className="progress-error">다운로드에 실패했습니다. 다시 시도하거나 저장 위치를 확인하세요. ({downloadJob.error_code})</p>}
              {downloadDiagnostics.length > 0 && (
                <details className="file-progress-details" open={Boolean(downloadStartError && !downloadJob)}>
                  <summary>다운로드 진단 로그 {downloadDiagnostics.length}개</summary>
                  <div className="file-progress-list">
                    {downloadDiagnostics.map((line, index) => (
                      <p className="file-progress-meta" key={`${line}-${index}`}>{line}</p>
                    ))}
                  </div>
                </details>
              )}
              {canOpenDownloadedFolder && (
                <button type="button" className="open-folder-button" onClick={handleOpenDownloadedFolder} disabled={!isTauriRuntime() || isOpeningFolder}>
                  {isOpeningFolder ? '여는 중...' : '다운로드 폴더 열기'}
                </button>
              )}
            </div>
          )}
        </section>
      </section>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
