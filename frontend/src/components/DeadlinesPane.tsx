import type { DeadlinesState } from '../hooks/useDeadlines';
import { splitCourseName } from '../lib/content';
import { groupDeadlines } from '../lib/deadline';
import type { DeadlineEntry } from '../lib/deadline';
import type { DeadlineEntryDto } from '../lib/types';
import { DeadlineCell } from './DeadlineCell';
import { ExternalIcon } from './Icons';
import { Spinner } from './Screens';

const KIND_LABEL: Record<string, string> = { video: '강의', assignment: '과제', quiz: '퀴즈' };

interface Props {
  items: DeadlineEntryDto[];
  state: DeadlinesState;
  error: string | null;
  now: Date;
  /** A lecture goes to its course; an assignment or quiz opens in an app window. */
  onSelect: (item: DeadlineEntryDto) => void;
  /** Secondary action of assignment/quiz rows: the system browser. */
  onOpenInBrowser: (item: DeadlineEntryDto) => void;
  onReload: () => void;
}

interface RowProps {
  entry: DeadlineEntry<DeadlineEntryDto>;
  onSelect: (item: DeadlineEntryDto) => void;
  onOpenInBrowser: (item: DeadlineEntryDto) => void;
}

function DeadlineRow({ entry, onSelect, onOpenInBrowser }: RowProps) {
  const { item, info } = entry;
  const course = splitCourseName(item.course_name).title;
  const isVideo = item.kind === 'video';
  return (
    <li className="deadline-item">
      <button
        type="button"
        className={`deadline-row${entry.upcoming ? ' is-upcoming' : ''}`}
        title={isVideo ? '이 과목의 강의 목록으로 이동' : '앱 안에서 열기'}
        onClick={() => onSelect(item)}
      >
        <DeadlineCell info={{ ...info, when: null }} />
        <span className="deadline-date">{info.when ?? ''}</span>
        <span className="chip">{KIND_LABEL[item.kind] ?? item.kind}</span>
        <span className="deadline-course" title={item.course_name}>{course}</span>
        <span className="deadline-title" title={item.title}>{item.title}</span>
        {entry.upcoming && <span className="chip">열리기 전</span>}
      </button>
      {!isVideo && (
        <button
          type="button"
          className="btn btn-icon btn-ghost btn-sm deadline-external"
          aria-label={`${item.title} 브라우저로 열기`}
          title="브라우저로 열기"
          onClick={() => onOpenInBrowser(item)}
        >
          <ExternalIcon />
        </button>
      )}
    </li>
  );
}

/** "다가오는 기한": lectures, assignments and quizzes of the term grouped by how soon they close. */
export function DeadlinesPane({ items, state, error, now, onSelect, onOpenInBrowser, onReload }: Props) {
  const groups = groupDeadlines(items, now);
  return (
    <section className="lecture-pane deadlines-pane" aria-busy={state === 'loading'}>
      <header className="pane-head">
        <h1 className="pane-title">다가오는 기한</h1>
        <p className="pane-sub">아직 끝내지 않은 강의·과제·퀴즈를 기한 순서로 모았어요.</p>
      </header>
      <p className="deadline-note">과제의 완료 표시는 LMS 기준이라 실제 제출 여부와 다를 수 있어요.</p>
      <div className="lecture-scroll">
        {state === 'loading' && items.length === 0 && <p className="pane-empty" role="status"><Spinner />기한을 불러오는 중…</p>}
        {state === 'error' && (
          <div className="pane-empty" role="alert">
            <p>{error}</p>
            <button type="button" className="btn btn-sm" onClick={onReload}>다시 시도</button>
          </div>
        )}
        {state === 'ready' && groups.length === 0 && <p className="pane-empty">다가오는 기한이 없어요.</p>}
        {groups.map((group) => (
          <section key={group.key} className="deadline-group" aria-label={group.title}>
            <h2 className="deadline-group-title">{group.title}<span className="week-count">{group.entries.length}</span></h2>
            <ul>
              {group.entries.map((entry) => (
                <DeadlineRow key={`${entry.item.course_id}-${entry.item.kind}-${entry.item.module_item_id ?? entry.item.title}`} entry={entry} onSelect={onSelect} onOpenInBrowser={onOpenInBrowser} />
              ))}
            </ul>
          </section>
        ))}
      </div>
    </section>
  );
}
