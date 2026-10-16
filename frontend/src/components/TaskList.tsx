import { taskDeadline, taskStatus } from '../lib/deadline';
import type { TaskStatus } from '../lib/deadline';
import { itemKey, itemPageUrl } from '../lib/content';
import type { CourseContentItem } from '../lib/types';
import { DeadlineCell } from './DeadlineCell';
import { ExternalIcon, WindowIcon } from './Icons';

const STATUS_CHIP: Record<TaskStatus, { className: string; text: string }> = {
  done: { className: 'chip chip-success', text: '완료' },
  pending: { className: 'chip chip-warning', text: '미완료' },
  overdue: { className: 'chip chip-danger', text: '미완료' },
};

interface Props {
  tasks: CourseContentItem[];
  courseId: string | number;
  now: Date;
  /** Opens the task in an app window (primary action). */
  onOpen: (item: CourseContentItem) => void;
  /** Opens the task in the system browser (secondary action). */
  onOpenInBrowser: (item: CourseContentItem) => void;
}

/** Assignments and quizzes of a course. Rows are not selectable: there is nothing to download. */
export function TaskList({ tasks, courseId, now, onOpen, onOpenInBrowser }: Props) {
  return (
    <div className="lecture-table is-task" role="group" aria-label="과제·퀴즈 목록">
      <div className="lecture-head">
        <span className="cell-title">제목</span>
        <span className="cell-kind">종류</span>
        <span className="cell-deadline">기한</span>
        <span className="cell-status">상태</span>
        <span className="cell-action" />
      </div>
      {tasks.map((item, index) => {
        const status = STATUS_CHIP[taskStatus(item, now)];
        const url = itemPageUrl(item, courseId);
        return (
          <div key={itemKey(item) ?? `task-${index}`} className="task-row">
            <span className="cell-title" title={item.title}>{item.title}</span>
            <span className="cell-kind">{item.kind === 'quiz' ? '퀴즈' : '과제'}</span>
            <DeadlineCell info={taskDeadline(item, now)} />
            <span className="cell-status"><span className={status.className}>{status.text}</span></span>
            <span className="cell-action">
              <button
                type="button"
                className="btn btn-icon btn-ghost btn-sm"
                aria-label={`${item.title} 앱에서 열기`}
                title={url ? '앱 안에서 열기' : 'LMS 주소를 알 수 없어요'}
                disabled={url === null}
                onClick={() => onOpen(item)}
              >
                <WindowIcon />
              </button>
              <button
                type="button"
                className="btn btn-icon btn-ghost btn-sm"
                aria-label={`${item.title} 브라우저로 열기`}
                title={url ? '브라우저로 열기' : 'LMS 주소를 알 수 없어요'}
                disabled={url === null}
                onClick={() => onOpenInBrowser(item)}
              >
                <ExternalIcon />
              </button>
            </span>
          </div>
        );
      })}
    </div>
  );
}
