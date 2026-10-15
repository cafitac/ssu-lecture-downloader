import type { BatchDownloadResult, DownloadArtifactResult, DownloadJobStatus, DownloadResult, ProgressEvent } from './types';
import { formatBytes } from './format.ts';

export const TERMINAL_DOWNLOAD_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);

export function latestByteProgress(events: Array<{ bytes_written?: number; total_bytes?: number }>): { written?: number; total?: number; percent: number } {
  const latest = [...events].reverse().find((event) => typeof event.bytes_written === 'number');
  const written = latest?.bytes_written;
  const total = latest?.total_bytes;
  const percent = typeof written === 'number' && typeof total === 'number' && total > 0
    ? Math.min(100, Math.max(0, Math.round((written / total) * 100)))
    : 0;
  return { written, total, percent };
}

export function latestResponseDiagnostics(events: ProgressEvent[]): ProgressEvent | undefined {
  return [...events].reverse().find((event) =>
    typeof event.http_status === 'number'
    || typeof event.content_type === 'string'
    || typeof event.content_length === 'number'
    || typeof event.response_host === 'string'
  );
}

export function responseDiagnosticText(event: ProgressEvent | undefined): string | null {
  if (!event) return null;
  const parts = [
    event.response_host ? `host ${event.response_host}` : null,
    event.http_status ? `HTTP ${event.http_status}` : null,
    event.content_type ? event.content_type : null,
    typeof event.content_length === 'number' ? formatBytes(event.content_length) : null,
  ].filter(Boolean);
  return parts.length ? parts.join(' · ') : null;
}

export function downloadStageLabel(status: string | undefined, events: Array<{ stage: string }> = []): string {
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

export function latestUserStage(events: Array<{ stage: string; download_count?: number; max_parallel_downloads?: number }>): string {
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

export function isArtifactDownloadResult(result: DownloadResult | BatchDownloadResult | DownloadArtifactResult | null | undefined): result is DownloadArtifactResult {
  return Boolean(result && 'artifacts' in result && 'download' in result);
}

export function isBatchDownloadResult(result: DownloadResult | BatchDownloadResult | DownloadArtifactResult | null | undefined): result is BatchDownloadResult {
  return Boolean(result && !isArtifactDownloadResult(result) && 'items' in result);
}

export function downloadPayloadFromArtifact(result: DownloadResult | BatchDownloadResult | DownloadArtifactResult | null | undefined): DownloadResult | BatchDownloadResult | null | undefined {
  return isArtifactDownloadResult(result) ? result.download : result;
}

export function latestBatchPosition(events: Array<{ index?: number; total?: number }>): { index?: number; total?: number } {
  const latest = [...events].reverse().find((event) => typeof event.index === 'number' && typeof event.total === 'number');
  return { index: latest?.index, total: latest?.total };
}

export function batchPreparationProgress(events: Array<{ stage: string; index?: number; total?: number; resolved_count?: number }>): { resolved: number; total: number; percent: number; done: boolean } {
  const total = [...events].reverse().find((event) => typeof event.total === 'number')?.total ?? 0;
  const prepareDone = [...events].reverse().find((event) => event.stage === 'batch_prepare_done');
  const latestResolve = [...events].reverse().find((event) => event.stage.includes('resolve') && typeof event.index === 'number');
  const resolved = prepareDone?.resolved_count ?? latestResolve?.index ?? 0;
  const percent = total > 0 ? Math.min(100, Math.max(0, Math.round((resolved / total) * 100))) : 0;
  return { resolved, total, percent, done: Boolean(prepareDone) };
}

export function batchDownloadStart(events: Array<{ stage: string; download_count?: number; max_parallel_downloads?: number }>): { started: boolean; downloadCount?: number; parallelism?: number; skipped: boolean } {
  const event = [...events].reverse().find((item) => item.stage === 'batch_downloads_started');
  const skipped = events.some((item) => item.stage === 'artifact_downloads_skipped');
  return { started: Boolean(event), downloadCount: event?.download_count, parallelism: event?.max_parallel_downloads, skipped };
}

export function artifactAudioProgress(events: Array<{ stage: string; index?: number; total?: number; progress_percent?: number | null }>): { current: number; total: number; percent: number; started: boolean; done: boolean } {
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

export function artifactTranscriptProgress(events: Array<{ stage: string; index?: number; total?: number; progress_percent?: number | null }>): { current: number; total: number; percent: number; started: boolean; done: boolean } {
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

export type FileProgressRow = {
  key: string;
  title: string;
  status: string;
  written?: number;
  total?: number;
  percent: number;
  outputPath?: string;
  errorCode?: string;
};

function clampPercent(value: number): number {
  return Math.min(100, Math.max(0, Math.round(value)));
}

/** Per-file status text for exact stage names. */
const STAGE_STATUS: Record<string, string> = {
  extracting_audio: 'MP3 준비 중',
  audio_probe_done: 'MP3 준비 중',
  audio_progress: 'MP3 변환 중',
  download_started: '다운로드 중',
  download_progress: '다운로드 중',
  audio_done: 'MP3 완료',
  audio_skipped: '기존 MP3 사용',
  transcribing: 'TXT 생성 중',
  transcript_model_loading: 'TXT 모델 준비 중',
  transcript_started: 'TXT 생성 중',
  transcript_progress: 'TXT 생성 중',
  transcript_done: 'TXT 완료',
  transcript_skipped: '기존 TXT 사용',
  download_done: '다운로드 완료',
  video_playable_check_started: 'MP4 확인/변환 중',
  video_reconstructed: 'MP4 변환 완료',
  video_playable_check_done: 'MP4 확인 완료',
};

/** Stages that reset the file's percent to a fixed starting value. */
const STAGE_FIXED_PERCENT: Record<string, number> = {
  extracting_audio: 0,
  audio_probe_done: 0,
  transcribing: 10,
  transcript_model_loading: 5,
  transcript_started: 10,
};
const STAGE_REPORTS_PERCENT = new Set(['audio_progress', 'transcript_progress']);
const STAGE_COMPLETE = new Set(['download_done', 'video_reconstructed', 'video_playable_check_done', 'audio_done', 'audio_skipped', 'transcript_done', 'transcript_skipped']);
/** MP3/TXT stages track their own percent instead of the download byte ratio. */
const ARTIFACT_STAGES = new Set(['extracting_audio', 'audio_probe_done', 'audio_progress', 'audio_done', 'audio_skipped', 'transcribing', 'transcript_model_loading', 'transcript_started', 'transcript_progress', 'transcript_done', 'transcript_skipped']);

function stageStatusLabel(stage: string): string | undefined {
  if (stage.includes('failed')) return '실패';
  if (Object.hasOwn(STAGE_STATUS, stage)) return STAGE_STATUS[stage];
  if (stage.includes('resolve') || stage.includes('plan')) return '준비 중';
  return undefined;
}

export function fileProgressRows(events: Array<{ stage: string; module_item_id?: string | number; title?: string; output_path?: string; bytes_written?: number; total_bytes?: number; file_size?: number; error_code?: string; progress_percent?: number | null }>, result?: BatchDownloadResult | null): FileProgressRow[] {
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
    const progressPercent = typeof event.progress_percent === 'number' ? clampPercent(event.progress_percent) : undefined;
    const status = stageStatusLabel(event.stage);
    if (status) row.status = status;
    // Percent: stages that report their own progress win over byte ratios.
    if (Object.hasOwn(STAGE_FIXED_PERCENT, event.stage)) row.percent = STAGE_FIXED_PERCENT[event.stage];
    if (STAGE_REPORTS_PERCENT.has(event.stage) && progressPercent !== undefined) row.percent = progressPercent;
    if (STAGE_COMPLETE.has(event.stage)) {
      row.percent = 100;
      if (typeof event.file_size === 'number') {
        row.written = event.file_size;
        row.total = event.file_size;
      }
    } else if (!ARTIFACT_STAGES.has(event.stage) && typeof row.written === 'number' && typeof row.total === 'number' && row.total > 0) {
      row.percent = clampPercent((row.written / row.total) * 100);
    }
  }
  return Array.from(rows.values());
}

export function aggregateFileProgress(rows: FileProgressRow[], fallbackPercent: number): { percent: number; written?: number; total?: number } {
  if (rows.length === 0) return { percent: fallbackPercent };
  const totals = rows.map((row) => ({ written: row.percent === 100 && row.total === undefined ? 1 : row.written, total: row.total ?? (row.percent === 100 ? 1 : undefined) }));
  if (totals.every((row) => typeof row.written === 'number' && typeof row.total === 'number' && row.total > 0)) {
    const written = totals.reduce((sum, row) => sum + (row.written ?? 0), 0);
    const total = totals.reduce((sum, row) => sum + (row.total ?? 0), 0);
    return { written, total, percent: total > 0 ? Math.min(100, Math.max(0, Math.round((written / total) * 100))) : fallbackPercent };
  }
  return { percent: Math.round(rows.reduce((sum, row) => sum + row.percent, 0) / rows.length) };
}


export function isActiveJobStatus(status: string | undefined | null): boolean {
  return Boolean(status) && !TERMINAL_DOWNLOAD_STATUSES.has(status as string);
}

export interface DownloadViewInput {
  downloadJob: DownloadJobStatus | null;
  batchResult: BatchDownloadResult | null;
  downloadResult: DownloadResult | null;
  isStartingDownload: boolean;
  downloadStartError: string | null;
  downloadDir: string;
}

/** Everything the progress card renders, derived from the job and last known results. */
export function deriveDownloadView({ downloadJob, batchResult, downloadResult, isStartingDownload, downloadStartError, downloadDir }: DownloadViewInput) {
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
  return {
    activeArtifactResult,
    activeBatchResult,
    activeSingleResult,
    batchPosition,
    batchPrepareProgress,
    batchDownloadInfo,
    artifactAudioInfo,
    artifactTranscriptInfo,
    perFileProgress,
    downloadProgress,
    responseDiagnostics,
    aggregateProgress,
    downloadProgressPercent,
    batchDownloadPhasePercent,
    batchDownloadPhaseLabel,
    downloadStatusLabel,
    downloadProgressMessage,
    artifactErrorText,
    downloadOutputPath,
    shouldShowDownloadProgress,
    canOpenDownloadedFolder,
  };
}

export type DownloadView = ReturnType<typeof deriveDownloadView>;
