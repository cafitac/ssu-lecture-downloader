/**
 * Attendance deadlines for unwatched lectures. LearningX counts attendance until
 * `due_at`; after it, `late_at` (or `lock_at`) closes a late window.
 */

export type DeadlineTone = 'muted' | 'normal' | 'soon' | 'urgent' | 'late' | 'missed';

export interface DeadlineInfo {
  /** Short cell text such as `D-3` or `기한 지남`. */
  badge: string;
  /** Date text such as `10/22(목) 23:59`, or `null`. */
  when: string | null;
  tone: DeadlineTone;
  /** Full sentence for tooltips and screen readers. */
  title: string;
}

interface DeadlineFields {
  completed?: boolean | null;
  unlock_at?: string | null;
  due_at?: string | null;
  late_at?: string | null;
  lock_at?: string | null;
}

const DAY_MS = 24 * 60 * 60 * 1000;
const WEEKDAYS = ['일', '월', '화', '수', '목', '금', '토'];

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function pad(value: number): string {
  return String(value).padStart(2, '0');
}

/** `10/22(목) 23:59` in the viewer's local time. */
export function formatDeadline(date: Date): string {
  return `${date.getMonth() + 1}/${date.getDate()}(${WEEKDAYS[date.getDay()]}) ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** Calendar-day countdown: `오늘 마감`, `D-1`, ... */
export function dDay(target: Date, now: Date): string {
  const days = Math.round((startOfDay(target) - startOfDay(now)) / DAY_MS);
  return days <= 0 ? '오늘 마감' : `D-${days}`;
}

function toneFor(target: Date, now: Date): DeadlineTone {
  const left = target.getTime() - now.getTime();
  if (left <= DAY_MS) return 'urgent';
  if (left <= 3 * DAY_MS) return 'soon';
  return 'normal';
}

/** Deadline state of one lecture, or `null` when there is nothing to show (watched or no dates). */
export function lectureDeadline(item: DeadlineFields, now: Date = new Date()): DeadlineInfo | null {
  if (item.completed === true) return null;
  const unlock = parse(item.unlock_at);
  const due = parse(item.due_at);
  const closing = [parse(item.late_at), parse(item.lock_at)]
    .filter((value): value is Date => value !== null && (due === null || value > due))
    .sort((a, b) => a.getTime() - b.getTime())[0] ?? null;

  if (unlock && now < unlock) {
    return { badge: '열리기 전', when: formatDeadline(unlock), tone: 'muted', title: `${formatDeadline(unlock)}부터 볼 수 있어요.` };
  }
  if (due && now < due) {
    return { badge: dDay(due, now), when: formatDeadline(due), tone: toneFor(due, now), title: `${formatDeadline(due)}까지 보면 출석으로 인정돼요.` };
  }
  if (closing && now < closing) {
    return { badge: '지각', when: formatDeadline(closing), tone: 'late', title: `출석 기한은 지났어요. ${formatDeadline(closing)}까지 보면 지각으로 인정돼요.` };
  }
  if (due || closing) {
    return { badge: '기한 지남', when: null, tone: 'missed', title: '시청 기한이 모두 지났어요.' };
  }
  return null;
}

/** Sidebar hint for a course's nearest open deadline. */
export function courseDeadline(nextAt: string | null | undefined, isLate: boolean | undefined, now: Date = new Date()): DeadlineInfo | null {
  const at = parse(nextAt);
  if (!at || at <= now) return null;
  if (isLate) return { badge: `지각 ${dDay(at, now)}`, when: formatDeadline(at), tone: 'late', title: `지각 인정 기한 ${formatDeadline(at)}` };
  return { badge: dDay(at, now), when: formatDeadline(at), tone: toneFor(at, now), title: `가장 가까운 출석 기한 ${formatDeadline(at)}` };
}
