/**
 * Every deadline rule of the UI lives here.
 *
 * Lectures: LearningX counts attendance until `due_at`; after it, `late_at` (or `lock_at`)
 * closes a late window. Assignments and quizzes: submission is due at `due_at`; `lock_at`
 * is the last moment a late submission is still accepted. A week that has not opened yet
 * (`unlock_at` in the future) is "not open", never "not watched".
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

export interface DeadlineFields {
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

/** `10/22(목)` in the viewer's local time. */
export function formatDay(date: Date): string {
  return `${date.getMonth() + 1}/${date.getDate()}(${WEEKDAYS[date.getDay()]})`;
}

/** `10/22(목) 23:59` in the viewer's local time. */
export function formatDeadline(date: Date): string {
  return `${formatDay(date)} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** Calendar days from `now` to `target` (0 = same day, negative = past). */
export function dayDistance(target: Date, now: Date): number {
  return Math.round((startOfDay(target) - startOfDay(now)) / DAY_MS);
}

/** Calendar-day countdown: `오늘 마감`, `D-1`, ... */
export function dDay(target: Date, now: Date): string {
  const days = dayDistance(target, now);
  return days <= 0 ? '오늘 마감' : `D-${days}`;
}

function toneFor(target: Date, now: Date): DeadlineTone {
  const left = target.getTime() - now.getTime();
  if (left <= DAY_MS) return 'urgent';
  if (left <= 3 * DAY_MS) return 'soon';
  return 'normal';
}

// ---------------------------------------------------------------- state of one item

/** Whether the item's week has opened (no unlock time, or it has passed). */
export function isOpened(item: DeadlineFields, now: Date = new Date()): boolean {
  const unlock = parse(item.unlock_at);
  return unlock === null || unlock <= now;
}

/**
 * The next deadline still ahead (mirrors the backend): `due_at` first, then the earliest
 * `late_at`/`lock_at` after it. `late` is true when only a late window is left.
 */
export function nextDeadline(item: DeadlineFields, now: Date = new Date()): { at: Date; late: boolean } | null {
  if (item.completed === true) return null;
  const due = parse(item.due_at);
  if (due && due > now) return { at: due, late: false };
  const closing = [parse(item.late_at), parse(item.lock_at)]
    .filter((value): value is Date => value !== null && value > now && (due === null || value > due))
    .sort((a, b) => a.getTime() - b.getTime())[0];
  return closing ? { at: closing, late: due !== null } : null;
}

/** The latest known closing time (due, late or lock), or `null` when the item has none. */
export function closedAt(item: DeadlineFields): Date | null {
  const known = [parse(item.due_at), parse(item.late_at), parse(item.lock_at)].filter((value): value is Date => value !== null);
  return known.length > 0 ? new Date(Math.max(...known.map((value) => value.getTime()))) : null;
}

/** Incomplete, with at least one known time, and every window has closed. */
export function isMissed(item: DeadlineFields, now: Date = new Date()): boolean {
  if (item.completed === true) return false;
  const end = closedAt(item);
  return end !== null && end <= now;
}

interface Copy {
  until: (when: string) => string;
  late: (when: string) => string;
  missed: string;
}

const LECTURE_COPY: Copy = {
  until: (when) => `${when}까지 보면 출석으로 인정돼요.`,
  late: (when) => `출석 기한은 지났어요. ${when}까지 보면 지각으로 인정돼요.`,
  missed: '시청 기한이 모두 지났어요.',
};

const TASK_COPY: Copy = {
  until: (when) => `${when}까지 제출해야 해요.`,
  late: (when) => `기한은 지났어요. ${when}까지는 제출할 수 있어요.`,
  missed: '제출 기한이 모두 지났어요.',
};

function describe(item: DeadlineFields, now: Date, copy: Copy, { honorUnlock }: { honorUnlock: boolean }): DeadlineInfo | null {
  if (item.completed === true) return null;
  const unlock = parse(item.unlock_at);
  if (honorUnlock && unlock && now < unlock) {
    return { badge: `${formatDay(unlock)}부터`, when: null, tone: 'muted', title: `${formatDeadline(unlock)}부터 볼 수 있어요.` };
  }
  const next = nextDeadline(item, now);
  if (next) {
    const when = formatDeadline(next.at);
    if (next.late) return { badge: '지각', when, tone: 'late', title: copy.late(when) };
    return { badge: dDay(next.at, now), when, tone: toneFor(next.at, now), title: copy.until(when) };
  }
  if (isMissed(item, now)) return { badge: '기한 지남', when: null, tone: 'missed', title: copy.missed };
  return null;
}

/** Deadline state of one lecture, or `null` when there is nothing to show (watched or no dates). */
export function lectureDeadline(item: DeadlineFields, now: Date = new Date()): DeadlineInfo | null {
  return describe(item, now, LECTURE_COPY, { honorUnlock: true });
}

/** Deadline state of one assignment or quiz: due first, then the lock time. */
export function taskDeadline(item: DeadlineFields, now: Date = new Date()): DeadlineInfo | null {
  return describe(item, now, TASK_COPY, { honorUnlock: true });
}

export type TaskStatus = 'done' | 'pending' | 'overdue';

/** 완료 (LMS says so) / 기한 지남 (incomplete, every time passed) / 미완료. */
export function taskStatus(item: DeadlineFields, now: Date = new Date()): TaskStatus {
  if (item.completed === true) return 'done';
  return isMissed(item, now) ? 'overdue' : 'pending';
}

/** The course-level fields the backend summarizes (lectures and assignments/quizzes). */
export interface CourseDeadlineFields {
  next_deadline_at?: string | null;
  next_deadline_is_late?: boolean;
  next_task_deadline_at?: string | null;
  missed_lecture_count?: number;
  overdue_task_count?: number;
}

/** Sidebar hint for a course's nearest open lecture deadline. */
export function courseDeadline(nextAt: string | null | undefined, isLate: boolean | undefined, now: Date = new Date()): DeadlineInfo | null {
  const at = parse(nextAt);
  if (!at || at <= now) return null;
  if (isLate) return { badge: `지각 ${dDay(at, now)}`, when: formatDeadline(at), tone: 'late', title: `지각 인정 기한 ${formatDeadline(at)}` };
  return { badge: dDay(at, now), when: formatDeadline(at), tone: toneFor(at, now), title: `가장 가까운 출석 기한 ${formatDeadline(at)}` };
}

/** Sidebar hint for a course: the nearer of its lecture and assignment/quiz deadlines. */
export function courseNextDeadline(course: CourseDeadlineFields, now: Date = new Date()): DeadlineInfo | null {
  const lecture = courseDeadline(course.next_deadline_at, course.next_deadline_is_late, now);
  const taskAt = parse(course.next_task_deadline_at);
  const task = taskAt && taskAt > now
    ? { badge: `과제 ${dDay(taskAt, now)}`, when: formatDeadline(taskAt), tone: toneFor(taskAt, now), title: `가장 가까운 과제·퀴즈 기한 ${formatDeadline(taskAt)}` } satisfies DeadlineInfo
    : null;
  if (lecture && task) {
    const lectureAt = parse(course.next_deadline_at) as Date;
    return lectureAt <= (taskAt as Date) ? lecture : task;
  }
  return lecture ?? task;
}

/** Lectures whose attendance has closed plus assignments/quizzes whose time has run out. */
export function courseMissedCount(course: CourseDeadlineFields): number {
  return (course.missed_lecture_count ?? 0) + (course.overdue_task_count ?? 0);
}

// ------------------------------------------------------------- deadline overview

/** A row of the "다가오는 기한" overview (a lecture, assignment or quiz). */
export interface DeadlineSubject extends DeadlineFields {
  kind: string;
}

export interface DeadlineEntry<T extends DeadlineSubject> {
  item: T;
  /** The next deadline still ahead, or the moment everything closed (missed items). */
  at: Date;
  missed: boolean;
  /** The week has not opened yet: it cannot be done, whatever the deadline is. */
  upcoming: boolean;
  info: DeadlineInfo;
}

export type DeadlineGroupKey = 'today' | 'soon' | 'week' | 'later' | 'missed';

export interface DeadlineGroup<T extends DeadlineSubject> {
  key: DeadlineGroupKey;
  title: string;
  entries: DeadlineEntry<T>[];
}

const GROUP_TITLES: Record<DeadlineGroupKey, string> = {
  today: '오늘',
  soon: '3일 이내',
  week: '이번 주',
  later: '이후',
  missed: '놓침',
};
const GROUP_ORDER: DeadlineGroupKey[] = ['today', 'soon', 'week', 'later', 'missed'];

/** The overview entry of one item, or `null` when it is done or has no usable dates. */
export function deadlineEntry<T extends DeadlineSubject>(item: T, now: Date = new Date()): DeadlineEntry<T> | null {
  if (item.completed === true) return null;
  const copy = item.kind === 'video' ? LECTURE_COPY : TASK_COPY;
  const upcoming = !isOpened(item, now);
  const next = nextDeadline(item, now);
  if (next) {
    // The overview shows the deadline itself; "not open yet" only mutes the row.
    const info = describe(item, now, copy, { honorUnlock: false }) as DeadlineInfo;
    const unlock = parse(item.unlock_at);
    return {
      item,
      at: next.at,
      missed: false,
      upcoming,
      info: upcoming && unlock ? { ...info, tone: 'muted', title: `${formatDeadline(unlock)}부터 볼 수 있어요. ${info.title}` } : info,
    };
  }
  const end = closedAt(item);
  if (end && end <= now && !upcoming) {
    return { item, at: end, missed: true, upcoming: false, info: { badge: '기한 지남', when: formatDeadline(end), tone: 'missed', title: copy.missed } };
  }
  return null;
}

function groupKeyFor(entry: DeadlineEntry<DeadlineSubject>, now: Date): DeadlineGroupKey {
  if (entry.missed) return 'missed';
  const days = dayDistance(entry.at, now);
  if (days <= 0) return 'today';
  if (days <= 3) return 'soon';
  return days <= 7 ? 'week' : 'later';
}

/** Overview rows grouped 오늘 / 3일 이내 / 이번 주 / 이후 / 놓침; empty groups are left out. */
export function groupDeadlines<T extends DeadlineSubject>(items: T[], now: Date = new Date()): DeadlineGroup<T>[] {
  const groups = new Map<DeadlineGroupKey, DeadlineEntry<T>[]>();
  for (const item of items) {
    const entry = deadlineEntry(item, now);
    if (!entry) continue;
    const key = groupKeyFor(entry, now);
    groups.set(key, [...(groups.get(key) ?? []), entry]);
  }
  return GROUP_ORDER.filter((key) => groups.has(key)).map((key) => {
    const entries = (groups.get(key) as DeadlineEntry<T>[]).slice();
    // Ahead: nearest first. Missed: the most recently closed first.
    entries.sort((a, b) => (key === 'missed' ? b.at.getTime() - a.at.getTime() : a.at.getTime() - b.at.getTime()));
    return { key, title: GROUP_TITLES[key], entries };
  });
}

/** Items that can be done now and are due today or within the next three calendar days. */
export function dueSoonCount<T extends DeadlineSubject>(items: T[], now: Date = new Date()): number {
  let count = 0;
  for (const item of items) {
    const entry = deadlineEntry(item, now);
    if (entry && !entry.missed && !entry.upcoming && dayDistance(entry.at, now) <= 3) count += 1;
  }
  return count;
}
