import type { ListState } from '../hooks/useLmsSession';
import type { TermSummary } from '../lib/types';
import { watchAllHint, watchAllLabel } from '../lib/watch';
import { RefreshIcon, SettingsIcon } from './Icons';

/** LMS term names when the backend has them; otherwise this term vs. earlier ones. */
function termName(term: TermSummary, currentId: number | null): string {
  const label = term.label?.trim();
  if (label && !/^term\s*\d+$/i.test(label)) return term.is_current ? `${label} (이번 학기)` : label;
  if (term.is_current) return '이번 학기';
  if (currentId !== null && term.term_id < currentId) return `지난 학기 (${term.term_id})`;
  return `학기 ${term.term_id}`;
}

interface Props {
  terms: TermSummary[];
  termId: number | null;
  listState: ListState;
  busy: boolean;
  /** Items due today or within three days. */
  dueSoonCount: number;
  deadlinesOpen: boolean;
  /** Open, unwatched videos of every course that the all-course auto playback would play. */
  watchAllCount: number;
  watchAllLoading: boolean;
  queueActive: boolean;
  onWatchAll: () => void;
  onToggleDeadlines: () => void;
  onTermChange: (termId: number) => void;
  onRefresh: () => void;
  onOpenSettings: () => void;
}

export function TopBar({ terms, termId, listState, busy, dueSoonCount, deadlinesOpen, watchAllCount, watchAllLoading, queueActive, onWatchAll, onToggleDeadlines, onTermChange, onRefresh, onOpenSettings }: Props) {
  const currentTermId = terms.find((term) => term.is_current)?.term_id ?? null;
  const loading = listState === 'loading';
  return (
    <header className="topbar">
      <strong className="app-name">강의 다운로더</strong>
      {terms.length > 0 && (
        <select
          className="select"
          aria-label="학기"
          value={termId ?? ''}
          disabled={loading || busy}
          onChange={(event) => onTermChange(Number(event.target.value))}
        >
          {termId === null && <option value="" disabled>학기 선택</option>}
          {terms.map((term) => <option key={term.term_id} value={term.term_id}>{termName(term, currentTermId)}</option>)}
        </select>
      )}
      <span className="topbar-spacer" />
      <button
        type="button"
        className="btn btn-sm"
        disabled={queueActive || watchAllCount === 0}
        title={watchAllHint(watchAllCount, { queueActive, loading: watchAllLoading })}
        onClick={onWatchAll}
      >
        {watchAllLabel(watchAllCount, queueActive)}
      </button>
      <button
        type="button"
        className={`btn btn-ghost deadlines-toggle${deadlinesOpen ? ' is-active' : ''}`}
        aria-pressed={deadlinesOpen}
        aria-label={dueSoonCount > 0 ? `다가오는 기한, 3일 안에 ${dueSoonCount}건` : '다가오는 기한'}
        title="다가오는 기한 (강의·과제·퀴즈)"
        onClick={onToggleDeadlines}
      >
        기한{dueSoonCount > 0 && <span className="count-badge">{dueSoonCount}</span>}
      </button>
      <span className="signed-in"><i aria-hidden="true" />로그인됨</span>
      <button
        type="button"
        className="btn btn-icon btn-ghost"
        aria-label="과목 새로고침"
        title={busy ? '구성요소 설치가 끝나면 새로고침할 수 있어요' : '과목 새로고침'}
        disabled={loading || busy}
        onClick={onRefresh}
      >
        <span className={loading ? 'spin' : undefined}><RefreshIcon /></span>
      </button>
      <button type="button" className="btn btn-icon btn-ghost" aria-label="설정" title="설정" onClick={onOpenSettings}>
        <SettingsIcon />
      </button>
    </header>
  );
}
