import { useEffect, useRef, useState } from 'react';
import type { JobRow, JobSummary } from '../lib/jobItems';
import { isRetryable } from '../lib/jobItems';
import { formatBytes } from '../lib/format';
import { CloseIcon, FolderIcon } from './Icons';

interface Props {
  rows: JobRow[];
  summary: JobSummary;
  starting: boolean;
  active: boolean;
  cancelRequested: boolean;
  canOpenFolder: boolean;
  onClose: () => void;
  onCancel: () => void;
  onRetry: (ids?: string[]) => void;
  onOpenFolder: (path?: string) => void;
}

const ANNOUNCED_PHASES = new Set<JobRow['phase']>(['waiting_audio', 'extracting', 'waiting_transcript', 'transcribing', 'done', 'failed', 'cancelled', 'skipped']);
const MAX_ANNOUNCED_ROWS = 3;

/**
 * One polite sentence for the screen reader when rows change step, never for a percent tick,
 * so a long job does not read out every poll. A failure is announced the same way, not as an alert.
 */
function useStatusAnnouncement(rows: JobRow[]): string {
  const previous = useRef<Map<string, string> | null>(null);
  const [message, setMessage] = useState('');
  useEffect(() => {
    const next = new Map(rows.map((row) => [row.id, `${row.phase}|${row.label}|${row.note ?? ''}`]));
    const before = previous.current;
    previous.current = next;
    if (!before) return;
    const changed = rows.filter((row) => before.has(row.id) && before.get(row.id) !== next.get(row.id) && ANNOUNCED_PHASES.has(row.phase));
    if (changed.length === 0) return;
    const spoken = changed.slice(0, MAX_ANNOUNCED_ROWS).map((row) => `${row.title}: ${row.phase === 'failed' ? `${row.label}. ${row.reason ?? ''}` : row.note ? `${row.label}, ${row.note}` : row.label}`);
    if (changed.length > MAX_ANNOUNCED_ROWS) spoken.push(`그 밖에 ${changed.length - MAX_ANNOUNCED_ROWS}개 강의의 상태가 바뀌었어요`);
    setMessage(spoken.join('. '));
  }, [rows]);
  return message;
}

const ACTIVE_BAR_PHASES = new Set<JobRow['phase']>(['preparing', 'downloading', 'verifying', 'extracting', 'transcribing']);

function percentText(row: JobRow): string {
  return `${row.estimated ? '약 ' : ''}${row.percent}%`;
}

function RowDetail({ row }: { row: JobRow }) {
  if (row.phase === 'failed') return <span className="row-reason">{row.reason}</span>;
  const bytes = row.phase === 'downloading' && row.written !== undefined
    ? `${formatBytes(row.written)}${row.total ? ` / ${formatBytes(row.total)}` : ''}`
    : null;
  return (
    <span className="row-detail">
      {row.label}
      {row.note && <> · {row.note}</>}
      {bytes && <> · {bytes}</>}
      {row.percent !== null && ACTIVE_BAR_PHASES.has(row.phase) && <> · {percentText(row)}</>}
    </span>
  );
}

export function DownloadDrawer({ rows, summary, starting, active, cancelRequested, canOpenFolder, onClose, onCancel, onRetry, onOpenFolder }: Props) {
  const finished = !active && rows.length > 0;
  const title = starting && rows.every((row) => row.phase === 'waiting')
    ? '다운로드를 준비하는 중'
    : active
      ? `진행 중 ${summary.done}/${summary.total}`
      : summary.failed > 0 || summary.unfinished > 0
        ? `${summary.done}개 완료 · ${summary.unfinished}개 못 받음`
        : `${summary.done}개 모두 받았어요`;
  const retryable = rows.filter(isRetryable);
  const announcement = useStatusAnnouncement(rows);
  return (
    <aside className="drawer" aria-label="다운로드 진행 상황">
      <header className="drawer-head">
        <h2>{title}</h2>
        <button type="button" className="btn btn-icon btn-ghost" aria-label="닫기" onClick={onClose}><CloseIcon /></button>
      </header>
      <p className="visually-hidden" role="status" aria-live="polite">{announcement}</p>
      {active && (
        <div className="progress" role="progressbar" aria-label="전체 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={summary.percent} aria-valuetext={`${summary.percent}%`}>
          <div className="progress-fill" style={{ width: `${summary.percent}%` }} />
        </div>
      )}
      <ul className="drawer-list">
        {rows.map((row) => (
          <li key={row.id} className={`drawer-row phase-${row.phase}`}>
            <div className="drawer-row-main">
              <span className="drawer-row-title" title={row.title}>{row.title}</span>
              <RowDetail row={row} />
            </div>
            {(row.phase === 'downloading' || row.phase === 'extracting' || row.phase === 'transcribing') && (
              <div
                className={`progress progress-sm${row.percent === null ? ' is-indeterminate' : ''}`}
                role="progressbar"
                aria-label={`${row.title} 진행률`}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={row.percent ?? undefined}
                aria-valuetext={row.percent === null ? row.label : `${row.label} ${percentText(row)}`}
              >
                <div className="progress-fill" style={{ width: `${row.percent ?? 100}%` }} />
              </div>
            )}
            {row.phase === 'done' && canOpenFolder && (
              <button type="button" className="btn btn-sm" onClick={() => onOpenFolder(row.outputPath)}>
                <FolderIcon />폴더 열기
              </button>
            )}
            {isRetryable(row) && !active && (
              <button type="button" className="btn btn-sm" onClick={() => onRetry([row.id])}>다시 시도</button>
            )}
          </li>
        ))}
      </ul>
      <footer className="drawer-foot">
        {active && (
          <button type="button" className="btn" disabled={starting || cancelRequested} onClick={onCancel}>
            {cancelRequested ? '취소하는 중…' : '모두 취소'}
          </button>
        )}
        {finished && retryable.length > 1 && (
          <button type="button" className="btn" onClick={() => onRetry()}>남은 {retryable.length}개 다시 시도</button>
        )}
        {finished && canOpenFolder && summary.done > 0 && rows.length > 1 && (
          <button type="button" className="btn" onClick={() => onOpenFolder()}><FolderIcon />폴더 열기</button>
        )}
      </footer>
    </aside>
  );
}
