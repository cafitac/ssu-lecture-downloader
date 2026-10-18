/**
 * Texts and helpers for the "순서대로 시청" status strip. The queue state itself comes from the
 * desktop shell; this module is pure so Node can load it in the contract tests.
 */
import { splitCourseName } from './content.ts';
import { isOpened, nextDeadline } from './deadline.ts';
import type { CourseContentItem, DeadlineEntryDto, WatchQueueItemInput, WatchQueueState } from './types';

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

// ------------------------------------------------------------ all-course auto playback

/** A queue item of the all-course playback: the lecture plus the course it is opened in. */
export type AllCoursesQueueItem = WatchQueueItemInput & { course_id: string; course_name: string };

/**
 * Lectures for "전체 순서대로 시청" from the term's deadline entries: every video that is open
 * and not watched, across all courses, the nearest attendance deadline first (a late window
 * counts by its closing time), then the order the entries came in (course list, then week order).
 *
 * Left out: weeks that are not open yet and videos whose every window has closed (a missed
 * one cannot count for attendance any more), watched ones, ids already played in this
 * session (`watched`, the LMS may not have counted them yet), and duplicates. The state is
 * recomputed from the times, so it stays right as the clock moves.
 */
export function buildAllCoursesQueue(
  entries: DeadlineEntryDto[],
  now: Date = new Date(),
  watched: ReadonlySet<string> = new Set(),
): AllCoursesQueueItem[] {
  const seen = new Set<string>();
  const ranked: Array<{ input: AllCoursesQueueItem; at: number; index: number }> = [];
  entries.forEach((entry, index) => {
    if (entry.kind !== 'video' || entry.completed === true) return;
    if (entry.module_item_id === null || entry.module_item_id === undefined) return;
    const moduleItemId = String(entry.module_item_id);
    const courseId = String(entry.course_id);
    if (watched.has(moduleItemId)) return;
    const key = `${courseId}:${moduleItemId}`;
    if (seen.has(key) || !isOpened(entry, now)) return;
    const next = nextDeadline(entry, now);
    if (!next) return;
    seen.add(key);
    const courseTitle = splitCourseName(entry.course_name).title || entry.course_name;
    const title = entry.title.trim() || '강의';
    ranked.push({
      input: {
        module_item_id: moduleItemId,
        course_id: courseId,
        course_name: entry.course_name,
        title: courseTitle ? `${courseTitle} · ${title}` : title,
        duration: typeof entry.duration === 'number' && Number.isFinite(entry.duration) && entry.duration > 0 ? entry.duration : null,
      },
      at: next.at.getTime(),
      index,
    });
  });
  return ranked.sort((a, b) => a.at - b.at || a.index - b.index).map(({ input }) => input);
}

export interface AllCoursesSummary {
  count: number;
  courseCount: number;
  /** Sum of the known lengths, in seconds. */
  totalSeconds: number;
  /** Lectures whose length the LMS did not tell (not part of the total). */
  unknownLengthCount: number;
}

export function summarizeAllCoursesQueue(items: AllCoursesQueueItem[]): AllCoursesSummary {
  return {
    count: items.length,
    courseCount: new Set(items.map((item) => item.course_id)).size,
    totalSeconds: items.reduce((sum, item) => sum + (item.duration ?? 0), 0),
    unknownLengthCount: items.filter((item) => item.duration === null).length,
  };
}

/** `약 1시간 12분`, `약 35분`, `1분 미만`; `-` for nothing. */
export function formatTotalTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return '-';
  if (seconds < 60) return '1분 미만';
  const totalMinutes = Math.ceil(seconds / 60);
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (hours === 0) return `약 ${minutes}분`;
  return minutes === 0 ? `약 ${hours}시간` : `약 ${hours}시간 ${minutes}분`;
}

/** Button text of the all-course entry points (top bar and 다가오는 기한). */
export function watchAllLabel(count: number, queueActive: boolean): string {
  return queueActive ? '순서대로 시청 중' : `전체 순서대로 시청 (${count})`;
}

/** Tooltip of the all-course button: what it does, or why it is disabled. */
export function watchAllHint(count: number, state: { queueActive: boolean; loading: boolean }): string {
  if (state.queueActive) return '자동 재생 중이에요. 위쪽 상태 줄에서 멈출 수 있어요.';
  if (count === 0) {
    return state.loading
      ? '기한을 불러오는 중이에요. 잠시 후 다시 눌러 주세요.'
      : '지금 볼 수 있는데 아직 안 본 강의가 없어요. (열리기 전이거나 기한이 지난 강의는 빼요.)';
  }
  return `모든 과목의 열린 안 본 강의 ${count}개를 출석 기한이 가까운 순서로 자동 재생해요.`;
}

/** Text lines of the confirmation before an all-course run starts. */
export function watchAllConfirmText(summary: AllCoursesSummary): { headline: string; lines: string[] } {
  const length = summary.totalSeconds > 0
    ? `총 ${formatTotalTime(summary.totalSeconds)}${summary.unknownLengthCount > 0 ? ` (길이를 모르는 ${summary.unknownLengthCount}개는 빼고)` : ''}`
    : '길이는 아직 알 수 없어요';
  return {
    headline: `강의 ${summary.count}개 (과목 ${summary.courseCount}개) · ${length}`,
    lines: [
      '출석 기한이 가까운 순서로 한 편씩 자동 재생해요. 기한이 모두 지난 강의와 아직 열리지 않은 강의는 빼요.',
      '화면 구석에 작은 재생 창이 항상 위에 떠서 소리 없이 재생돼요. 이 창을 가리거나 최소화하면 재생이 멈추니 계속 보이게 두세요.',
      '끝나면 과목 목록과 기한을 다시 불러와요. 출석 반영은 LMS가 정해요.',
    ],
  };
}
