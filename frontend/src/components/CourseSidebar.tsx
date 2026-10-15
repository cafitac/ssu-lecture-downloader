import { useEffect, useRef } from 'react';
import type { ListState } from '../hooks/useLmsSession';
import { courseUpcomingText, courseWatchSummary, splitCourseName } from '../lib/content';
import { courseMissedCount, courseNextDeadline } from '../lib/deadline';
import type { CourseSummary } from '../lib/types';
import { Spinner } from './Screens';

interface Props {
  courses: CourseSummary[];
  selectedId: string | null;
  listState: ListState;
  listError: string | null;
  now: Date;
  onSelect: (course: CourseSummary) => void;
  onRetry: () => void;
}

export function CourseSidebar({ courses, selectedId, listState, listError, now, onSelect, onRetry }: Props) {
  const selectedRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: 'nearest' });
  }, [selectedId, courses]);

  return (
    <nav className="sidebar" aria-label="과목">
      <h2 className="sidebar-title">과목 {courses.length}개</h2>
      {listState === 'loading' && courses.length === 0 && (
        <p className="side-note" role="status"><Spinner />불러오는 중…</p>
      )}
      {listState === 'error' && (
        <div className="side-note side-note-error" role="alert">
          <p>{listError}</p>
          <button type="button" className="btn btn-sm" onClick={onRetry}>다시 시도</button>
        </div>
      )}
      {listState === 'ready' && courses.length === 0 && <p className="side-note">이 학기에는 과목이 없어요.</p>}
      <ul className={`course-list${listState === 'loading' ? ' is-stale' : ''}`}>
        {courses.map((course) => {
          const selected = String(course.id) === selectedId;
          const { title, code } = splitCourseName(course.name);
          const deadline = courseNextDeadline(course, now);
          const missed = courseMissedCount(course);
          const upcoming = courseUpcomingText(course);
          return (
            <li key={course.id}>
              <button
                type="button"
                ref={selected ? selectedRef : undefined}
                className={`course-item${selected ? ' is-selected' : ''}`}
                aria-current={selected ? 'true' : undefined}
                title={code ? `${title} (${code})` : title}
                onClick={() => onSelect(course)}
              >
                <span className="course-name">{title}</span>
                <span className="course-meta">
                  {courseWatchSummary(course)}
                  {upcoming && <span className="course-upcoming">{upcoming}</span>}
                  {deadline && <span className={`course-deadline tone-${deadline.tone}`} title={deadline.title}>{deadline.badge}</span>}
                  {missed > 0 && <span className="course-deadline tone-missed" title="기한이 모두 지난 강의와 과제·퀴즈">놓침 {missed}</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
