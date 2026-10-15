import type { CourseContentItem, CourseSummary, LectureItem } from './types';

export type LectureTab = 'video' | 'material';

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

export function courseWatchSummary(course: CourseSummary): string {
  if (course.lecture_count <= 0) return '영상 없음';
  if (course.uncompleted_lecture_count <= 0) return '모두 시청함';
  return `안 본 강의 ${course.uncompleted_lecture_count}개`;
}

export function countVideos(items: CourseContentItem[]): { total: number; watched: number } {
  const videos = items.filter((item) => item.kind === 'video');
  return { total: videos.length, watched: videos.filter((item) => item.completed === true).length };
}
