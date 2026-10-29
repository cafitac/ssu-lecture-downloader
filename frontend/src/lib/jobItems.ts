import type { BatchDownloadItemResult, DownloadJobStatus, DownloadOutputFormat, DownloadResult, PipelineItemStatus, PipelineSummary, ProgressEvent } from './types';
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
  | 'waiting_audio'
  | 'extracting'
  | 'waiting_transcript'
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
  /** True when `percent` is a guess by the subtitle server, shown as "약 n%". */
  estimated?: boolean;
  /** This item's share of the whole job, 0-100, weighted by the steps that were asked for. */
  overall: number;
  written?: number;
  total?: number;
  /**
   * True when this state cannot change any more: an `artifact_item_*` event, the job's `items_status`,
   * the end of the job, or a step that never undoes itself (done, cancelled). A `failed` row of a
   * running job is not final: the backend retries after `download_failed` (signed-URL expiry).
   */
  final: boolean;
  /** The row waits only because an earlier step finished (`download_done`, `audio_done`); no waiting event said so. It never overrides a row that already got further. */
  derivedWait: boolean;
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
  waiting_audio: '음성 추출 대기 중',
  extracting: '음성 추출 중',
  waiting_transcript: '자막 대기 중',
  transcribing: '자막 만드는 중',
  done: '완료',
  failed: '실패',
  cancelled: '취소됨',
  skipped: '시작하지 못했어요',
};

const REMOTE_TRANSCRIBING_LABEL = '자막 만드는 중 (원격)';
/** `audio_waiting` with `step: "mp4"`: the lecture waits for its video check, not for audio (matches '영상 확인 중'). */
const WAITING_MP4_LABEL = '영상 확인 대기 중';
/** `transcript_waiting` with `reason: "media"`: the audio is still being converted or cut for the subtitles. */
const WAITING_MEDIA_LABEL = '자막 준비 대기 중';

const ACTIVE_PHASES = new Set<RowPhase>(['preparing', 'downloading', 'verifying', 'waiting_audio', 'extracting', 'waiting_transcript', 'transcribing']);

/** Share of one lecture's work per step; only the steps that were asked for count (design/09 § 화면 문구). */
const WEIGHT_DOWNLOAD = 40;
const WEIGHT_AUDIO = 20;
const WEIGHT_TRANSCRIPT = 40;
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
  /** The percent comes from an estimate, not from the server's own count. */
  estimated?: boolean;
  /** Why the subtitle step is waiting (`transcript_waiting.reason`), and what the server told us about the queue. */
  waitReason?: string;
  /** `audio_waiting.step`; only meaningful while the row is `waiting_audio`. */
  audioWaitStep?: string;
  waitAhead?: number;
  waitPosition?: number;
  /** The current `waiting_audio` / `waiting_transcript` was guessed from `download_done` / `audio_done`, not reported by a waiting event. */
  derived?: boolean;
  /** Set by `artifact_item_*` events and the bare `failed` result: later events of this lecture cannot change a final state. */
  locked: boolean;
  seen: Set<string>;
}

interface StepContext {
  /** The MP3 (or the subtitles that need it) was asked for, so the step after the download is audio extraction. */
  wantsAudio: boolean;
  wantsTranscript: boolean;
}

function positiveInt(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? Math.floor(value) : undefined;
}

/** Label and extra text for a row that waits for its subtitles. */
function waitingTranscriptText(state: Accumulated): { label: string; note?: string } {
  if (state.waitReason === 'media') return { label: WAITING_MEDIA_LABEL };
  if (state.waitPosition !== undefined) return { label: `${PHASE_LABEL.waiting_transcript} (${state.waitPosition}번째)` };
  if (state.waitReason === 'server_deferred') return { label: `${PHASE_LABEL.waiting_transcript} (서버가 잠시 미룸)` };
  if (state.waitReason === 'server_busy') {
    return {
      label: '자막 서버 응답 기다리는 중',
      note: state.waitAhead !== undefined ? `먼저 보낸 요청 ${state.waitAhead}개` : undefined,
    };
  }
  return { label: PHASE_LABEL.waiting_transcript };
}

const AUDIO_REACHED_STAGES = new Set(['extracting_audio', 'audio_probe_done', 'audio_progress', 'audio_done', 'audio_skipped', 'transcript_waiting', 'transcript_queued', 'transcript_done', 'transcript_skipped']);

/** True once audio extraction or any later step began, so an `audio_waiting` can only be stale. */
function hasReachedAudio(seen: Set<string>): boolean {
  for (const stage of seen) {
    if (AUDIO_REACHED_STAGES.has(stage) || TRANSCRIBE_STAGES.has(stage)) return true;
  }
  return false;
}

function applyEvent(state: Accumulated, event: ProgressEvent, ctx: StepContext): void {
  const stage = event.stage;
  state.seen.add(stage);
  if (event.output_path) state.outputPath = event.output_path;
  if (typeof event.total_bytes === 'number' && event.total_bytes > 0) state.total = event.total_bytes;
  if (typeof event.bytes_written === 'number') state.written = event.bytes_written;

  // A lecture that already reached its final state (artifact_item_*) ignores late or reordered events.
  if (state.locked) return;

  if (stage === 'artifact_item_done') {
    state.phase = 'done';
    state.percent = 100;
    state.locked = true;
    return;
  }
  if (stage === 'artifact_item_cancelled') {
    state.phase = 'cancelled';
    state.locked = true;
    return;
  }
  if (stage.includes('failed')) {
    state.phase = 'failed';
    state.percent = null;
    state.failure = event.error_code ?? state.failure;
    // `artifact_item_failed` and the batch download's bare `failed` result are final; `download_failed` and other `*_failed` may be retried.
    if (stage === 'artifact_item_failed' || stage === 'failed') state.locked = true;
    return;
  }
  if (stage === 'download_cancelled') {
    state.phase = 'cancelled';
    return;
  }
  if (stage === 'artifact_item_queued') return;
  if (stage === 'audio_waiting') {
    // A lecture never goes back: a late or reordered wait (the backend moves coalesced waits to the end) is stale once audio or subtitles started.
    if (hasReachedAudio(state.seen)) return;
    state.phase = 'waiting_audio';
    state.percent = null;
    state.audioWaitStep = event.step === 'mp4' ? 'mp4' : undefined;
    state.derived = false;
    return;
  }
  if (stage === 'transcript_waiting' || stage === 'transcript_queued') {
    state.phase = 'waiting_transcript';
    state.percent = null;
    state.note = undefined;
    state.estimated = false;
    state.derived = false;
    if (stage === 'transcript_queued') {
      state.waitPosition = positiveInt(event.queue_position);
    } else {
      state.waitReason = event.reason;
      state.waitAhead = typeof event.ahead_requests === 'number' && event.ahead_requests >= 0 ? Math.floor(event.ahead_requests) : undefined;
      state.waitPosition = undefined;
    }
    return;
  }
  if (stage === 'download_done' || stage === 'download_skipped') {
    if (typeof event.file_size === 'number') {
      state.written = event.file_size;
      state.total = event.file_size;
    }
    // The file is complete. When audio is wanted next, the lecture is waiting for its turn, not still downloading.
    state.phase = ctx.wantsAudio ? 'waiting_audio' : 'downloading';
    state.percent = ctx.wantsAudio ? null : 100;
    state.audioWaitStep = undefined;
    state.derived = ctx.wantsAudio;
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
    const checked = stage === 'video_playable_check_done' || stage === 'video_reconstructed' || stage === 'video_reconstruction_done';
    state.phase = checked && ctx.wantsAudio ? 'waiting_audio' : 'verifying';
    state.percent = null;
    state.audioWaitStep = undefined;
    state.derived = checked && ctx.wantsAudio;
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
    state.phase = ctx.wantsTranscript ? 'waiting_transcript' : 'extracting';
    state.percent = ctx.wantsTranscript ? null : 100;
    state.derived = ctx.wantsTranscript;
    return;
  }
  if (TRANSCRIBE_STAGES.has(stage)) {
    state.phase = 'transcribing';
    state.waitReason = undefined;
    state.waitAhead = undefined;
    state.waitPosition = undefined;
    if (event.remote) state.remote = true;
    if (typeof event.progress_percent === 'number') {
      state.percent = clampPercent(event.progress_percent);
      state.estimated = event.progress_estimated === true;
    }
    else if (stage === 'transcript_model_loading') state.percent = 5;
    else if (stage === 'transcribing' || stage === 'transcript_started') state.percent = 10;
    state.note = transcribeNote(event);
    return;
  }
  if (stage === 'transcript_done' || stage === 'transcript_skipped') {
    state.phase = 'transcribing';
    state.percent = 100;
    state.estimated = false;
    return;
  }
  if ((stage.includes('resolve') || stage.includes('plan')) && state.phase === 'waiting') {
    state.phase = 'preparing';
  }
}

/** True once every output the user asked for exists for this item. */
function isFinished(plan: JobPlan, native: boolean, seen: Set<string>): boolean {
  const has = (...stages: string[]) => stages.some((stage) => seen.has(stage));
  if (has('artifact_item_done')) return true;
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

/** Parsed `result.items_status` keyed by item id; entries with an unknown shape are ignored. */
function itemsStatusById(result: unknown): Map<string, PipelineItemStatus> {
  const found = new Map<string, PipelineItemStatus>();
  const list = pickResultField(result, 'items_status');
  if (!Array.isArray(list)) return found;
  for (const entry of list) {
    if (entry && typeof entry === 'object' && 'module_item_id' in entry && typeof (entry as PipelineItemStatus).state === 'string') {
      found.set(String((entry as PipelineItemStatus).module_item_id), entry as PipelineItemStatus);
    }
  }
  return found;
}

/** A pipeline field sits on the job result, or on its wrapped download result for MP3/TXT jobs. */
function pickResultField(result: unknown, key: string): unknown {
  if (!result || typeof result !== 'object') return undefined;
  const record = result as Record<string, unknown>;
  if (key in record) return record[key];
  const inner = record.download;
  return inner && typeof inner === 'object' ? (inner as Record<string, unknown>)[key] : undefined;
}

/** `result.summary` of a finished job, or `null` on an older backend or while the job still runs. */
export function readPipelineSummary(job: DownloadJobStatus | null): PipelineSummary | null {
  if (!job || !TERMINAL_DOWNLOAD_STATUSES.has(job.status)) return null;
  const raw = pickResultField(job.result, 'summary');
  if (!raw || typeof raw !== 'object') return null;
  const { total, succeeded, failed, cancelled } = raw as Record<string, unknown>;
  const counts = [total, succeeded, failed, cancelled];
  if (!counts.every((count) => typeof count === 'number' && Number.isInteger(count) && count >= 0)) return null;
  return { total: total as number, succeeded: succeeded as number, failed: failed as number, cancelled: cancelled as number };
}

const ITEM_DONE_STATES = new Set(['done', 'succeeded', 'ok']);

/**
 * Where a row is in the whole job, 0-100: download 40, audio 20, subtitles 40, counting only
 * the steps that were asked for. Waiting for a step means the steps before it are finished.
 */
function weightedProgress(ctx: StepContext, phase: RowPhase, percent: number | null): number {
  const audio = ctx.wantsAudio ? WEIGHT_AUDIO : 0;
  const transcript = ctx.wantsTranscript ? WEIGHT_TRANSCRIPT : 0;
  const whole = WEIGHT_DOWNLOAD + audio + transcript;
  const part = (weight: number) => (weight * (percent ?? 0)) / 100;
  let got = 0;
  switch (phase) {
    case 'downloading': got = part(WEIGHT_DOWNLOAD); break;
    case 'verifying':
    case 'waiting_audio': got = WEIGHT_DOWNLOAD; break;
    case 'extracting': got = WEIGHT_DOWNLOAD + part(audio); break;
    case 'waiting_transcript': got = WEIGHT_DOWNLOAD + audio; break;
    case 'transcribing': got = WEIGHT_DOWNLOAD + audio + part(transcript); break;
    case 'done':
    case 'failed':
    case 'cancelled': got = whole; break;
    default: got = 0;
  }
  return Math.min(100, Math.round((got / whole) * 100));
}

/** Phases that end a row (a `failed` one is final only when `JobRow.final` says so). */
const FINAL_PHASES = new Set<RowPhase>(['done', 'failed', 'cancelled', 'skipped']);
/** Order of the running phases; the video check and the wait that follows it are one tier because MP4 output is checked after download_done; waiting for subtitles and making them are one tier because a media wait can follow transcribing. */
const PHASE_RANK: Record<RowPhase, number> = {
  waiting: 0, preparing: 1, downloading: 2, verifying: 4, waiting_audio: 4, extracting: 5,
  waiting_transcript: 6, transcribing: 6, done: 7, failed: 7, cancelled: 7, skipped: 7,
};

/**
 * Events of one lecture in the order they happened, by `at`. Since backend 35a3352 the stored list is
 * already chronological (a coalesced stage is removed and appended at the end), so this only matters
 * for older backend builds (f367be8 moved waits to the end but replaced ticks in place) and as a guard.
 * Events without `at` keep their slot; ties keep list order.
 */
function inEventOrder(events: ProgressEvent[]): ProgressEvent[] {
  const slots: number[] = [];
  events.forEach((event, index) => {
    if (typeof event.at === 'number' && Number.isFinite(event.at)) slots.push(index);
  });
  if (slots.length < 2) return events;
  const timed = slots.map((index) => events[index]).map((event, order) => ({ event, order }));
  timed.sort((a, b) => (a.event.at as number) - (b.event.at as number) || a.order - b.order);
  const ordered = events.slice();
  slots.forEach((slot, i) => { ordered[slot] = timed[i].event; });
  return ordered;
}

/** One row per planned item. `job` is `null` while the job is still being prepared. */
export function buildJobRows(plan: JobPlan, job: DownloadJobStatus | null): JobRow[] {
  const events = job?.progress ?? [];
  const payload = downloadPayloadFromArtifact(job?.result);
  const batchItems = isBatchDownloadResult(payload) ? payload.items : [];
  const single = payload && !isBatchDownloadResult(payload) ? payload : null;
  const native = Boolean(job?.job_id.startsWith('native-'));
  const wantsTranscript = plan.kind === 'video' && plan.outputs.includes('txt');
  const ctx: StepContext = { wantsTranscript, wantsAudio: wantsTranscript || (plan.kind === 'video' && plan.outputs.includes('mp3')) };
  const finalStates = itemsStatusById(job?.result);

  return plan.items.map((item): JobRow => {
    const own = inEventOrder(events.filter((event) => (
      event.module_item_id !== undefined && event.module_item_id !== null
        ? String(event.module_item_id) === item.id
        : plan.items.length === 1
    )));
    const state: Accumulated = { phase: 'waiting', percent: null, failure: null, locked: false, seen: new Set() };
    for (const event of own) applyEvent(state, event, ctx);

    const result = findResultItem(plan, item.id, batchItems, single);
    // The job's own final list survives event compaction, so it wins over what the events imply.
    const final = finalStates.get(item.id);
    const finalState = final?.state === 'failed' ? 'failed' : final?.state === 'cancelled' ? 'cancelled' : final && ITEM_DONE_STATES.has(final.state) ? 'done' : null;
    const status = job?.status;
    let phase: RowPhase = state.phase;
    let reasonCode: string | null = null;

    if (finalState === 'failed') {
      phase = 'failed';
      reasonCode = final?.error_code ?? state.failure ?? result?.error_code ?? job?.error_code ?? null;
    } else if (finalState === 'cancelled') {
      phase = 'cancelled';
    } else if (finalState === 'done') {
      phase = 'done';
    } else if (state.phase === 'failed') {
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

    // A failure of a running job may still be retried by the backend; only the sources below are final.
    const jobEnded = job !== null && TERMINAL_DOWNLOAD_STATUSES.has(job.status);
    const isFinal = phase === 'failed' ? state.locked || finalState === 'failed' || jobEnded : FINAL_PHASES.has(phase);
    const outputPath = result?.output_path ?? state.outputPath;
    const percent = phase === 'done' ? 100 : phase === 'failed' ? null : state.percent;
    const waiting = phase === 'waiting_transcript' ? waitingTranscriptText(state) : null;
    const row: JobRow = {
      id: item.id,
      rawId: item.rawId,
      title: item.title,
      phase,
      label: waiting ? waiting.label : phase === 'waiting_audio' && state.audioWaitStep === 'mp4' ? WAITING_MP4_LABEL : phase === 'transcribing' && state.remote ? REMOTE_TRANSCRIBING_LABEL : PHASE_LABEL[phase],
      percent,
      final: isFinal,
      derivedWait: phase === state.phase && Boolean(state.derived) && (phase === 'waiting_audio' || phase === 'waiting_transcript'),
      // Only a final row reads 100: a finished download of an MP4-only job still has its check ahead.
      overall: isFinal ? weightedProgress(ctx, phase, percent) : phase === 'failed' ? 0 : Math.min(99, weightedProgress(ctx, phase, percent)),
      written: state.written,
      total: state.total ?? (item.sizeBytes ?? undefined),
      outputPath: outputPath ?? undefined,
    };
    if (phase === 'transcribing' && state.estimated && percent !== null) row.estimated = true;
    if (waiting?.note) row.note = waiting.note;
    if (phase === 'transcribing' && state.note) row.note = state.note;
    if (phase === 'failed') {
      row.reason = describeErrorCode(reasonCode);
      if (reasonCode) row.reasonCode = reasonCode;
    }
    return row;
  });
}

/**
 * Keeps each row at its furthest point between polls. The backend caps the event list, so old
 * events can disappear and a running row would fall back to "대기" or 0%. `marks` is keyed by
 * job id + row id and holds the last row shown; it is updated in place and is safe to call twice
 * for the same poll. A final row (`JobRow.final`: `artifact_item_*`, `items_status`, job end) always wins and is never undone; a `failed` row of a running job is not final.
 */
export function holdFurthestRows(jobId: string | null | undefined, rows: JobRow[], marks: Map<string, JobRow>): JobRow[] {
  if (!jobId) return rows;
  return rows.map((row) => {
    const key = `${jobId}:${row.id}`;
    const before = marks.get(key);
    let shown = row;
    if (before && !row.final) {
      if (before.final) shown = before;
      // A failure the backend may retry is not pinned: show what happens next, but keep the bar where it was.
      else if (row.phase === 'failed' || before.phase === 'failed') shown = { ...row, overall: Math.max(row.overall, before.overall) };
      else if (PHASE_RANK[row.phase] < PHASE_RANK[before.phase]) shown = before;
      // A guessed wait (only audio_done is left after eviction) must not pull a transcribing row back.
      else if (row.derivedWait && PHASE_RANK[row.phase] <= PHASE_RANK[before.phase]) shown = before;
      else if (row.overall < before.overall) shown = { ...row, overall: before.overall };
    }
    marks.set(key, shown);
    return shown;
  });
}

export interface JobSummary {
  total: number;
  done: number;
  failed: number;
  /** Items that did not finish and are not running: failed, cancelled or never started. */
  unfinished: number;
  running: boolean;
  /** Overall progress, 0-100, weighted by step (see `weightedProgress`). */
  percent: number;
}

/**
 * `reported` is the backend's own count of a finished job (`result.summary`); it is used
 * only when it describes exactly these rows and nothing is still running.
 */
export function summarizeRows(rows: JobRow[], reported: PipelineSummary | null = null): JobSummary {
  let done = rows.filter((row) => row.phase === 'done').length;
  let failed = rows.filter((row) => row.phase === 'failed').length;
  const running = rows.some((row) => ACTIVE_PHASES.has(row.phase));
  let unfinished = rows.filter((row) => row.phase === 'failed' || row.phase === 'cancelled' || row.phase === 'skipped').length;
  if (reported && !running && reported.total === rows.length && reported.succeeded + reported.failed + reported.cancelled <= rows.length) {
    done = reported.succeeded;
    failed = reported.failed;
    unfinished = rows.length - reported.succeeded;
  }
  const progress = rows.reduce((sum, row) => sum + row.overall, 0);
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
