import type { ListState } from '../hooks/useLmsSession';
import type { TermSummary } from '../lib/types';
import { RefreshIcon, SettingsIcon } from './Icons';

function termName(term: TermSummary): string {
  const label = term.label?.trim();
  const base = label && !/^term\s*\d+$/i.test(label) ? label : `학기 ${term.term_id}`;
  return term.is_current ? `${base} (현재)` : base;
}

interface Props {
  terms: TermSummary[];
  termId: number | null;
  listState: ListState;
  busy: boolean;
  onTermChange: (termId: number) => void;
  onRefresh: () => void;
  onOpenSettings: () => void;
}

export function TopBar({ terms, termId, listState, busy, onTermChange, onRefresh, onOpenSettings }: Props) {
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
          {terms.map((term) => <option key={term.term_id} value={term.term_id}>{termName(term)}</option>)}
        </select>
      )}
      <span className="topbar-spacer" />
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
