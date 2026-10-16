import { isMissed, isOpened, nextDeadline, taskStatus } from './deadline.ts';
import type { CourseContentItem, CourseSummary, LectureItem } from './types';

export type LectureTab = 'video' | 'material' | 'task';

export function legacyLectureToContentItem(lecture: LectureItem): CourseContentItem {
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

/** Stable string key of a row (the LMS module item id), or `null` for items that cannot be acted on. */
export function itemKey(item: CourseContentItem): string | null {
  return item.module_item_id === null || item.module_item_id === undefined ? null : String(item.module_item_id);
}

export function isSelectable(item: CourseContentItem): boolean {
  return item.downloadable && itemKey(item) !== null;
}

export interface WeekGroup {
  key: string;
  title: string;
  items: CourseContentItem[];
}

/** Groups rows under their week/module header, keeping the order the LMS returned. */
export function groupByWeek(items: CourseContentItem[]): WeekGroup[] {
  const groups = new Map<string, WeekGroup>();
  for (const item of items) {
    const key = `${item.module_id ?? ''}|${item.week_position ?? ''}|${item.module_name}`;
    let group = groups.get(key);
    if (!group) {
      const name = item.module_name?.trim();
      const title = name || (item.week_position !== null && item.week_position !== undefined && item.week_position !== '' ? `${item.week_position}주차` : '기타');
      group = { key, title, items: [] };
      groups.set(key, group);
    }
    group.items.push(item);
  }
  return Array.from(groups.values());
}

export type TriState = 'none' | 'some' | 'all';

export function triState(keys: string[], selected: ReadonlySet<string>): TriState {
  if (keys.length === 0) return 'none';
  const count = keys.filter((key) => selected.has(key)).length;
  if (count === 0) return 'none';
  return count === keys.length ? 'all' : 'some';
}

/** Summed size of the items; `unknown` counts rows without a known size. */
export function sumSizes(items: CourseContentItem[]): { bytes: number; unknown: number } {
  let bytes = 0;
  let unknown = 0;
  for (const item of items) {
    if (item.size_bytes && item.size_bytes > 0) bytes += item.size_bytes;
    else unknown += 1;
  }
  return { bytes, unknown };
}

const COURSE_CODE_SUFFIX = /\s*[(（［\[]([A-Za-z0-9_\-\s]{4,})[)）］\]]\s*$/;

/** Course name without the trailing course code, e.g. `자료구조(12345678-01)` -> name + code. */
export function splitCourseName(name: string): { title: string; code: string | null } {
  const match = COURSE_CODE_SUFFIX.exec(name);
  if (match && /\d/.test(match[1])) {
    const title = name.slice(0, match.index).trim();
    if (title) return { title, code: match[1].trim() };
  }
  return { title: name.trim(), code: null };
}

/** Sidebar line: count of unwatched open ones, or why there are none. */
export function courseWatchSummary(course: CourseSummary): string {
  if (course.lecture_count <= 0) return '영상 없음';
  if (course.uncompleted_lecture_count > 0) return `안 본 강의 ${course.uncompleted_lecture_count}개`;
  if (course.lecture_count === (course.upcoming_lecture_count ?? 0)) return '열린 강의 없음';
  return '모두 시청함';
}

/** Muted sidebar suffix for weeks that are not open yet, shown once everything open is watched. */
export function courseUpcomingText(course: CourseSummary): string | null {
  const upcoming = course.upcoming_lecture_count ?? 0;
  return upcoming > 0 && course.uncompleted_lecture_count <= 0 && course.lecture_count > upcoming ? `· 예정 ${upcoming}` : null;
}

/** Video counts of a course: all, watched, and not open yet. */
export function countVideos(items: CourseContentItem[], now: Date = new Date()): { total: number; watched: number; upcoming: number } {
  const videos = items.filter((item) => item.kind === 'video');
  return {
    total: videos.length,
    watched: videos.filter((item) => item.completed === true).length,
    upcoming: videos.filter((item) => item.completed !== true && !isOpened(item, now)).length,
  };
}

/** A video that is open and not watched yet (what "안 본 것만" keeps). */
export function isUnwatchedNow(item: CourseContentItem, now: Date = new Date()): boolean {
  return item.completed !== true && isOpened(item, now);
}

export function isTaskItem(item: CourseContentItem): boolean {
  return item.kind === 'assignment' || item.kind === 'quiz';
}

/** Rows of the 자료 tab: downloadable materials plus pages, boards and other LMS items. */
export function isLibraryItem(item: CourseContentItem): boolean {
  return item.kind === 'material' || item.kind === 'page' || item.kind === 'board' || item.kind === 'unknown';
}

/** Chip for the non-downloadable 자료 rows (`페이지`, `게시판`, `기타 · zip`), or `null` for materials. */
export function libraryKindChip(item: CourseContentItem): string | null {
  if (item.kind === 'page') return '페이지';
  if (item.kind === 'board') return '게시판';
  if (item.kind === 'unknown') {
    const inner = item.inner_content_type?.trim();
    return inner ? `기타 · ${inner}` : '기타';
  }
  return null;
}

function numericOrder(value: string | number | null | undefined): number {
  const number = typeof value === 'number' ? value : Number.parseFloat(String(value ?? ''));
  return Number.isFinite(number) ? number : Number.MAX_SAFE_INTEGER;
}

/**
 * Lectures for "순서대로 시청": videos that are open and not watched yet, the ones whose
 * attendance closes first at the front (a late window counts by its closing time), then week
 * order. Videos without a deadline follow, and videos whose every window has closed come
 * last. Not-yet-open weeks and duplicates are left out.
 */
export function buildWatchQueue(items: CourseContentItem[], now: Date = new Date()): CourseContentItem[] {
  const seen = new Set<string>();
  const entries: Array<{ item: CourseContentItem; index: number; rank: number; at: number }> = [];
  items.forEach((item, index) => {
    const key = itemKey(item);
    if (item.kind !== 'video' || key === null || item.completed === true || !isOpened(item, now) || seen.has(key)) return;
    seen.add(key);
    const next = nextDeadline(item, now);
    entries.push({ item, index, rank: next ? 0 : isMissed(item, now) ? 2 : 1, at: next ? next.at.getTime() : 0 });
  });
  return entries
    .sort((a, b) => a.rank - b.rank
      || a.at - b.at
      || numericOrder(a.item.week_position) - numericOrder(b.item.week_position)
      || numericOrder(a.item.lesson_position) - numericOrder(b.item.lesson_position)
      || a.index - b.index)
    .map(({ item }) => item);
}

/**
 * Assignments and quizzes by what to do next: nearest deadline first, then undated ones,
 * then overdue, then completed (each part keeps the LMS order for equal deadlines).
 */
export function sortTasks(items: CourseContentItem[], now: Date = new Date()): CourseContentItem[] {
  const rank = (item: CourseContentItem): [number, number] => {
    const status = taskStatus(item, now);
    if (status === 'done') return [3, 0];
    if (status === 'overdue') return [2, 0];
    const next = nextDeadline(item, now);
    return next ? [0, next.at.getTime()] : [1, 0];
  };
  return items
    .map((item, index) => ({ item, index, key: rank(item) }))
    .sort((a, b) => a.key[0] - b.key[0] || a.key[1] - b.key[1] || a.index - b.index)
    .map(({ item }) => item);
}

const LMS_ORIGIN = 'https://canvas.ssu.ac.kr';

/** Absolute LMS address of an item, or `null` when the LMS gave none we can open. */
export function lmsItemUrl(url: string | null | undefined): string | null {
  const value = url?.trim();
  if (!value) return null;
  if (value.startsWith('/')) return `${LMS_ORIGIN}${value}`;
  // Only open Soongsil hosts in the browser; anything else in LMS data is ignored.
  try {
    const parsed = new URL(value);
    const host = parsed.hostname.toLowerCase();
    const trusted = parsed.protocol === 'https:' && (host === 'ssu.ac.kr' || host.endsWith('.ssu.ac.kr'));
    return trusted ? parsed.toString() : null;
  } catch {
    return null;
  }
}

/** The Canvas page every module item can be reached through (the LMS redirects to the real page). */
export function moduleItemUrl(courseId: string | number | null | undefined, moduleItemId: string | number | null | undefined): string | null {
  const course = String(courseId ?? '').trim();
  const item = String(moduleItemId ?? '').trim();
  return /^\d+$/.test(course) && /^\d+$/.test(item) ? `${LMS_ORIGIN}/courses/${course}/modules/items/${item}` : null;
}

function isApiAddress(address: string): boolean {
  try {
    const path = new URL(address).pathname;
    return path.startsWith('/api/') || path.startsWith('/learningx/api/');
  } catch {
    return true;
  }
}

/**
 * Address to open an LMS item at: its own web address when the LMS gave one, else the module
 * item page. API addresses (JSON) are never opened. `null` when neither can be built.
 */
export function itemPageUrl(item: Pick<CourseContentItem, 'url' | 'module_item_id'>, courseId: string | number | null | undefined): string | null {
  const direct = lmsItemUrl(item.url);
  if (direct && !isApiAddress(direct)) return direct;
  return moduleItemUrl(courseId, item.module_item_id);
}

function earliestDeadline(course: CourseSummary, now: Date): number | null {
  const times = [course.next_deadline_at, course.next_task_deadline_at]
    .map((value) => (value ? new Date(value).getTime() : Number.NaN))
    .filter((value) => Number.isFinite(value) && value > now.getTime());
  return times.length > 0 ? Math.min(...times) : null;
}

/** Where to start: a course with open unwatched lectures, else the nearest deadline, else any with lectures. */
export function pickDefaultCourse(courses: CourseSummary[], now: Date = new Date()): CourseSummary | null {
  const unwatched = courses.find((course) => course.uncompleted_lecture_count > 0);
  if (unwatched) return unwatched;
  const dated = courses
    .map((course) => ({ course, at: earliestDeadline(course, now) }))
    .filter((entry): entry is { course: CourseSummary; at: number } => entry.at !== null)
    .sort((a, b) => a.at - b.at)[0];
  return dated?.course ?? courses.find((course) => course.lecture_count > 0) ?? courses[0] ?? null;
}
