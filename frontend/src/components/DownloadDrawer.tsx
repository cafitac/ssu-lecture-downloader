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

function RowDetail({ row }: { row: JobRow }) {
  if (row.phase === 'failed') return <span className="row-reason">{row.reason}</span>;
  const bytes = row.phase === 'downloading' && row.written !== undefined
    ? `${formatBytes(row.written)}${row.total ? ` / ${formatBytes(row.total)}` : ''}`
    : null;
  return (
    <span className="row-detail">
      {row.label}
      {bytes && <> · {bytes}</>}
      {row.percent !== null && row.phase !== 'done' && row.phase !== 'waiting' && <> · {row.percent}%</>}
    </span>
  );
}

export function DownloadDrawer({ rows, summary, starting, active, cancelRequested, canOpenFolder, onClose, onCancel, onRetry, onOpenFolder }: Props) {
  const finished = !active && rows.length > 0;
  const title = starting && rows.every((row) => row.phase === 'waiting')
    ? '다운로드를 준비하는 중'
    : active
      ? `받는 중 ${summary.done}/${summary.total}`
      : summary.failed > 0 || summary.unfinished > 0
        ? `${summary.done}개 완료 · ${summary.unfinished}개 못 받음`
        : `${summary.done}개 모두 받았어요`;
  const retryable = rows.filter(isRetryable);
  return (
    <aside className="drawer" aria-label="다운로드 진행 상황">
      <header className="drawer-head">
        <h2>{title}</h2>
        <button type="button" className="btn btn-icon btn-ghost" aria-label="닫기" onClick={onClose}><CloseIcon /></button>
      </header>
      {active && (
        <div className="progress" role="progressbar" aria-label="전체 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={summary.percent}>
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
        {finished && canOpenFolder && summary.done > 0 && (
          <button type="button" className="btn" onClick={() => onOpenFolder()}><FolderIcon />폴더 열기</button>
        )}
      </footer>
    </aside>
  );
}
