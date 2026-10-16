/**
 * Texts and helpers for the "순서대로 시청" status strip. The queue state itself comes from the
 * desktop shell; this module is pure so Node can load it in the contract tests.
 */
import type { CourseContentItem, WatchQueueItemInput, WatchQueueState } from './types';

export const IDLE_WATCH_QUEUE: WatchQueueState = {
  active: false,
  phase: 'idle',
  index: 0,
  total: 0,
  done_count: 0,
  skipped_count: 0,
  current_module_item_id: null,
  current_title: null,
  position_secs: null,
  duration_secs: null,
  remaining_secs: null,
  total_remaining_secs: 0,
  paused: false,
  rendering: true,
  items: [],
};

/** Shown under the strip while a queue runs: the player only plays while its window is drawn. */
export const WATCH_STRIP_NOTE = '재생 창이 가려지거나 최소화되면 재생이 멈춰요. 화면 구석에 보이게 두세요.';

/** `01:09` or `1:02:05`. */
export function formatClock(secs: number | null | undefined): string {
  const total = typeof secs === 'number' && Number.isFinite(secs) ? Math.max(0, Math.floor(secs)) : 0;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const pair = (value: number) => String(value).padStart(2, '0');
  return hours > 0 ? `${hours}:${pair(minutes)}:${pair(seconds)}` : `${pair(minutes)}:${pair(seconds)}`;
}

export interface WatchStripText {
  /** `자동 재생 중 3/7 · 제목 · 01:09 / 07:30` */
  summary: string;
  /** Why nothing moves right now (covered window, stall, opening), or `null`. */
  detail: string | null;
}

/** Text of the status strip for a running queue (`null` when no queue is running). */
export function watchStripText(queue: WatchQueueState): WatchStripText | null {
  if (!queue.active) return null;
  const head = `자동 재생 중 ${Math.min(queue.index + 1, queue.total)}/${queue.total}`;
  const title = queue.current_title?.trim() || '강의';
  if (queue.phase === 'opening' || queue.phase === 'stalled') {
    return {
      summary: `${head} · ${title}`,
      detail: queue.phase === 'stalled' ? '재생이 멈춰 한 번 다시 불러오고 있어요.' : '강의를 여는 중이에요…',
    };
  }
  const clock = queue.position_secs !== null && queue.duration_secs !== null
    ? ` · ${formatClock(queue.position_secs)} / ${formatClock(queue.duration_secs)}`
    : '';
  let detail: string | null = null;
  if (!queue.rendering) detail = '재생 창이 가려져 있어서 멈춰 있어요. 창을 보이게 해 주세요.';
  else if (queue.paused) detail = '일시정지 상태예요.';
  return { summary: `${head} · ${title}${clock}`, detail };
}

/** One line for a queue that has just finished. */
export function watchFinishedText(queue: WatchQueueState): string {
  const skipped = queue.skipped_count > 0 ? ` · ${queue.skipped_count}개 건너뜀` : '';
  return `자동 재생을 마쳤어요. ${queue.done_count}개 시청${skipped}`;
}

/** Lectures as the desktop shell's queue takes them (ids as text, lengths in seconds). */
export function toQueueInputs(items: CourseContentItem[]): WatchQueueItemInput[] {
  return items.flatMap((item) => {
    if (item.module_item_id === null || item.module_item_id === undefined) return [];
    const duration = typeof item.duration === 'number' && Number.isFinite(item.duration) && item.duration > 0 ? item.duration : null;
    return [{ module_item_id: String(item.module_item_id), title: item.title, duration }];
  });
}
