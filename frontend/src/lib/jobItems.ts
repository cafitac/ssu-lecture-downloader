import type { BatchDownloadItemResult, DownloadJobStatus, DownloadOutputFormat, DownloadResult, ProgressEvent } from './types';
import { describeErrorCode } from './errors.ts';
import { TERMINAL_DOWNLOAD_STATUSES, downloadPayloadFromArtifact, isBatchDownloadResult } from './progress.ts';

/**
 * Turns the raw job (progress events + final result) into one row per selected item for
 * the progress drawer. Pure, so the state machine can be unit-tested from Node.
 */

export interface PlannedItem {
  /** String key (LMS module item id) used to match progress events and results. */
  id: string;
  /** Original id, sent to the API untouched. */
  rawId: string | number;
  title: string;
  sizeBytes: number | null;
}

export interface JobPlan {
  courseId: string | number;
  courseName: string;
  /** Videos can produce MP4/MP3/TXT; materials are plain file downloads. */
  kind: 'video' | 'material';
  outputs: DownloadOutputFormat[];
  items: PlannedItem[];
}

export type RowPhase =
  | 'waiting'
  | 'preparing'
  | 'downloading'
  | 'verifying'
  | 'extracting'
  | 'transcribing'
  | 'done'
  | 'failed'
  | 'cancelled'
  | 'skipped';

export interface JobRow {
  id: string;
  rawId: string | number;
  title: string;
  phase: RowPhase;
  label: string;
  /** 0-100, or `null` when the step has no measurable progress. */
  percent: number | null;
  written?: number;
  total?: number;
  /** Plain-Korean failure reason for `failed` rows. */
  reason?: string;
  /** Raw failure code behind `reason`, for retry decisions (never shown). */
  reasonCode?: string;
  outputPath?: string;
  /** Extra text for the current step, such as which loop repair of a remote transcription is running. */
  note?: string;
}

const PHASE_LABEL: Record<RowPhase, string> = {
  waiting: '대기',
  preparing: '준비 중',
  downloading: '받는 중',
  verifying: '영상 확인 중',
  extracting: '음성 추출 중',
  transcribing: '자막 만드는 중',
  done: '완료',
  failed: '실패',
  cancelled: '취소됨',
  skipped: '시작하지 못했어요',
};

const REMOTE_TRANSCRIBING_LABEL = '자막 만드는 중 (원격)';

const ACTIVE_PHASES = new Set<RowPhase>(['preparing', 'downloading', 'verifying', 'extracting', 'transcribing']);
const TRANSCRIBE_STAGES = new Set(['transcribing', 'transcript_model_loading', 'transcript_connecting', 'transcript_started', 'transcript_progress', 'transcript_repair']);

/** Text for steps that only a remote transcription has: connecting and re-reading looped sections. */
function transcribeNote(event: ProgressEvent): string | undefined {
  if (event.stage === 'transcript_connecting') return '서버에 연결하는 중';
  if (event.stage === 'transcript_repair') {
    const where = event.repair_total ? ` ${event.repair_index ?? 1}/${event.repair_total}` : '';
    return `반복 구간 다시 인식 중${where}`;
  }
  return undefined;
}

function clampPercent(value: number): number {
  return Math.min(100, Math.max(0, Math.round(value)));
}

interface Accumulated {
  phase: RowPhase;
  percent: number | null;
  written?: number;
  total?: number;
  failure: string | null;
  outputPath?: string;
  /** Subtitles are made by the remote server (changes the label). */
  remote?: boolean;
  note?: string;
  seen: Set<string>;
}

function applyEvent(state: Accumulated, event: ProgressEvent, wantsTranscript: boolean): void {
  const stage = event.stage;
  state.seen.add(stage);
  if (event.output_path) state.outputPath = event.output_path;
  if (typeof event.total_bytes === 'number' && event.total_bytes > 0) state.total = event.total_bytes;
  if (typeof event.bytes_written === 'number') state.written = event.bytes_written;

  if (stage.includes('failed')) {
    state.phase = 'failed';
    state.percent = null;
    state.failure = event.error_code ?? state.failure;
    return;
  }
  if (stage === 'download_cancelled') {
    state.phase = 'cancelled';
    return;
  }
  if (stage === 'download_done' || stage === 'download_skipped') {
    state.phase = 'downloading';
    state.percent = 100;
    if (typeof event.file_size === 'number') {
      state.written = event.file_size;
      state.total = event.file_size;
    }
    return;
  }
  if (stage.startsWith('download_')) {
    state.phase = 'downloading';
    state.percent = typeof state.written === 'number' && typeof state.total === 'number' && state.total > 0
      ? clampPercent((state.written / state.total) * 100)
      : null;
    return;
  }
  if (stage.startsWith('video_')) {
    state.phase = 'verifying';
    state.percent = null;
    return;
  }
  if (stage === 'extracting_audio' || stage === 'audio_probe_done') {
    state.phase = 'extracting';
    state.percent = 0;
    return;
  }
  if (stage === 'audio_progress') {
    state.phase = 'extracting';
    state.percent = typeof event.progress_percent === 'number' ? clampPercent(event.progress_percent) : state.percent;
    return;
  }
  if (stage === 'audio_done' || stage === 'audio_skipped') {
    state.phase = wantsTranscript ? 'transcribing' : 'extracting';
    state.percent = wantsTranscript ? 0 : 100;
    return;
  }
  if (TRANSCRIBE_STAGES.has(stage)) {
    state.phase = 'transcribing';
    if (event.remote) state.remote = true;
    if (typeof event.progress_percent === 'number') state.percent = clampPercent(event.progress_percent);
    else if (stage === 'transcript_model_loading') state.percent = 5;
    else if (stage === 'transcribing' || stage === 'transcript_started') state.percent = 10;
    state.note = transcribeNote(event);
    return;
  }
  if (stage === 'transcript_done' || stage === 'transcript_skipped') {
    state.phase = 'transcribing';
    state.percent = 100;
    return;
  }
  if ((stage.includes('resolve') || stage.includes('plan')) && state.phase === 'waiting') {
    state.phase = 'preparing';
  }
}

/** True once every output the user asked for exists for this item. */
function isFinished(plan: JobPlan, native: boolean, seen: Set<string>): boolean {
  const has = (...stages: string[]) => stages.some((stage) => seen.has(stage));
  if (plan.kind === 'video' && plan.outputs.includes('txt')) return has('transcript_done', 'transcript_skipped');
  if (plan.kind === 'video' && plan.outputs.includes('mp3')) return has('audio_done', 'audio_skipped');
  if (has('video_playable_check_done', 'video_reconstructed', 'video_reconstruction_done')) return true;
  if (plan.kind === 'material') return has('download_done', 'download_skipped');
  // A native MP4 is only finished after the playable check, which `has(...)` above covers.
  return !native && has('download_done', 'download_skipped');
}

function findResultItem(plan: JobPlan, id: string, batchItems: BatchDownloadItemResult[], single: DownloadResult | null): BatchDownloadItemResult | DownloadResult | null {
  const fromBatch = batchItems.find((candidate) => String(candidate.module_item_id) === id);
  if (fromBatch) return fromBatch;
  if (single && (String(single.module_item_id) === id || plan.items.length === 1)) return single;
  return null;
}

/** One row per planned item. `job` is `null` while the job is still being prepared. */
export function buildJobRows(plan: JobPlan, job: DownloadJobStatus | null): JobRow[] {
  const events = job?.progress ?? [];
  const payload = downloadPayloadFromArtifact(job?.result);
  const batchItems = isBatchDownloadResult(payload) ? payload.items : [];
  const single = payload && !isBatchDownloadResult(payload) ? payload : null;
  const native = Boolean(job?.job_id.startsWith('native-'));
  const wantsTranscript = plan.kind === 'video' && plan.outputs.includes('txt');

  return plan.items.map((item): JobRow => {
    const own = events.filter((event) => (
      event.module_item_id !== undefined && event.module_item_id !== null
        ? String(event.module_item_id) === item.id
        : plan.items.length === 1
    ));
    const state: Accumulated = { phase: 'waiting', percent: null, failure: null, seen: new Set() };
    for (const event of own) applyEvent(state, event, wantsTranscript);

    const result = findResultItem(plan, item.id, batchItems, single);
    const status = job?.status;
    let phase: RowPhase = state.phase;
    let reasonCode: string | null = null;

    if (state.phase === 'failed') {
      phase = 'failed';
      reasonCode = state.failure ?? result?.error_code ?? job?.error_code ?? null;
    } else if (result && result.ok === false) {
      phase = 'failed';
      reasonCode = result.error_code ?? job?.error_code ?? null;
    } else if (isFinished(plan, native, state.seen) || status === 'succeeded' || (result?.ok === true && job !== null && TERMINAL_DOWNLOAD_STATUSES.has(job.status))) {
      phase = 'done';
    } else if (state.phase === 'cancelled' || status === 'cancelled') {
      phase = 'cancelled';
    } else if (status === 'failed') {
      if (state.phase === 'waiting') {
        phase = 'skipped';
      } else {
        phase = 'failed';
        reasonCode = job?.error_code ?? null;
      }
    }

    const outputPath = result?.output_path ?? state.outputPath;
    const row: JobRow = {
      id: item.id,
      rawId: item.rawId,
      title: item.title,
      phase,
      label: phase === 'transcribing' && state.remote ? REMOTE_TRANSCRIBING_LABEL : PHASE_LABEL[phase],
      percent: phase === 'done' ? 100 : phase === 'failed' ? null : state.percent,
      written: state.written,
      total: state.total ?? (item.sizeBytes ?? undefined),
      outputPath: outputPath ?? undefined,
    };
    if (phase === 'transcribing' && state.note) row.note = state.note;
    if (phase === 'failed') {
      row.reason = describeErrorCode(reasonCode);
      if (reasonCode) row.reasonCode = reasonCode;
    }
    return row;
  });
}

export interface JobSummary {
  total: number;
  done: number;
  failed: number;
  /** Items that did not finish and are not running: failed, cancelled or never started. */
  unfinished: number;
  running: boolean;
  /** Overall progress, 0-100. */
  percent: number;
}

export function summarizeRows(rows: JobRow[]): JobSummary {
  const done = rows.filter((row) => row.phase === 'done').length;
  const failed = rows.filter((row) => row.phase === 'failed').length;
  const running = rows.some((row) => ACTIVE_PHASES.has(row.phase));
  const unfinished = rows.filter((row) => row.phase === 'failed' || row.phase === 'cancelled' || row.phase === 'skipped').length;
  const progress = rows.reduce((sum, row) => sum + (row.phase === 'done' ? 100 : row.phase === 'failed' ? 100 : row.percent ?? 0), 0);
  return {
    total: rows.length,
    done,
    failed,
    unfinished,
    running,
    percent: rows.length ? Math.round(progress / rows.length) : 0,
  };
}

/**
 * A native transfer rejected by the media CDN (401/403/410) usually means the signed
 * media URL expired, not that the LMS session did. Planning again fetches a fresh URL,
 * and the backend re-logs in by itself when the LMS session really expired.
 */
const EXPIRED_MEDIA_URL_CODES = new Set(['HTTP 401', 'HTTP 403', 'HTTP 410']);

export function isExpiredMediaUrlCode(code: string | null | undefined): boolean {
  return Boolean(code && EXPIRED_MEDIA_URL_CODES.has(code));
}

export function isRetryable(row: JobRow): boolean {
  return row.phase === 'failed' || row.phase === 'cancelled' || row.phase === 'skipped';
}

export function isJobTerminal(job: DownloadJobStatus | null): boolean {
  return Boolean(job && TERMINAL_DOWNLOAD_STATUSES.has(job.status));
}
