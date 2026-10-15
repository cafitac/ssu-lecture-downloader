import type { CourseContentItem, LectureItem } from './types';

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
