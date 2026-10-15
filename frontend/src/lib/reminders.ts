import { splitCourseName } from './content.ts';
import { nextDeadline } from './deadline.ts';
import type { DeadlineEntryDto } from './types';

/**
 * What the app keeps on disk so reminders also work while it is closed: no URLs, cookies or
 * credentials, only enough to word a notification. Mirrors `CachedItem` in `reminders.rs`.
 */
export interface CachedDeadline {
  /** Stable id: `course_id:kind:module_item_id` (or a hash of the title when there is none). */
  id: string;
  /** Short course name. */
  course: string;
  kind: DeadlineEntryDto['kind'];
  title: string;
  /** Next open deadline, in unix seconds. */
  deadline_at: number;
  /** `deadline_at` ends a late window (the regular due time has passed). */
  late: boolean;
}

function titleHash(text: string): string {
  let hash = 5381;
  for (let index = 0; index < text.length; index += 1) {
    hash = ((hash * 33) ^ text.charCodeAt(index)) >>> 0;
  }
  return hash.toString(36);
}

export function deadlineCacheId(item: Pick<DeadlineEntryDto, 'course_id' | 'kind' | 'module_item_id' | 'title'>): string {
  const tail = item.module_item_id !== null && item.module_item_id !== undefined && item.module_item_id !== ''
    ? String(item.module_item_id)
    : `t${titleHash(item.title)}`;
  return `${item.course_id}:${item.kind}:${tail}`;
}

/**
 * The reminder cache of a deadline list. Done and missed items have no open deadline and are
 * left out; items whose week has not opened yet stay, because they will need a reminder later.
 */
export function buildDeadlineCache(items: DeadlineEntryDto[], now: Date = new Date()): CachedDeadline[] {
  const cached: CachedDeadline[] = [];
  for (const item of items) {
    const next = nextDeadline(item, now);
    if (!next) continue;
    cached.push({
      id: deadlineCacheId(item),
      course: splitCourseName(item.course_name).title,
      kind: item.kind,
      title: item.title,
      deadline_at: Math.floor(next.at.getTime() / 1000),
      late: next.late,
    });
  }
  return cached;
}
