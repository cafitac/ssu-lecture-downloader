import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import type { LibraryTidy } from '../hooks/useLibraryTidy';
import { TIDY_COPY, capRows, fileName, groupMoves, skipReasonText } from '../lib/tidy';
import type { TidyPlan, TidySkip } from '../lib/types';
import { Dialog } from './Dialog';
import { FolderIcon } from './Icons';
import { Spinner } from './Screens';

/** A preview longer than this lists its first rows and says how many more there are. */
const LIST_ROW_CAP = 300;

interface Props {
  tidy: LibraryTidy;
  canOpenFolder: boolean;
  onOpenFolder: () => void;
}

function MoveList({ plan }: { plan: TidyPlan }) {
  const { shown, hidden } = capRows(plan.moves, LIST_ROW_CAP);
  return (
    <div className="tidy-scroll" tabIndex={0} role="region" aria-label="옮길 파일">
      <ul className="tidy-tree">
        {groupMoves(shown).map((course) => (
          <li key={course.course}>
            <h3 className="tidy-course">{course.course}</h3>
            <ul className="tidy-tree">
              {course.weeks.map((week) => (
                <li key={week.week}>
                  <h4 className="tidy-week">{week.week}</h4>
                  <ul className="tidy-tree">
                    {week.lectures.map((lecture) => (
                      <li key={lecture.stem}>
                        <p className="tidy-lecture">{lecture.stem}</p>
                        <ul className="tidy-moves">
                          {lecture.moves.map((move) => (
                            <li key={move.from} title={`${move.from} → ${move.to}`}>
                              <span className="tidy-name">{fileName(move.from)}</span>
                              <span role="img" aria-label="다음 이름으로" className="tidy-arrow">→</span>
                              <span className="tidy-name tidy-to">{fileName(move.to)}</span>
                            </li>
                          ))}
                        </ul>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>
      {hidden > 0 && <p className="muted small tidy-more">{TIDY_COPY.more(hidden)}</p>}
    </div>
  );
}

function SkipList({ skips, label }: { skips: TidySkip[]; label: (count: number) => string }) {
  if (skips.length === 0) return null;
  const { shown, hidden } = capRows(skips, LIST_ROW_CAP);
  return (
    <details className="tidy-skips">
      <summary>{label(skips.length)}</summary>
      <ul className="tidy-moves">
        {shown.map((skip) => (
          <li key={skip.path} title={skip.path}>
            <span className="tidy-name">{fileName(skip.path)}</span>
            <span className="muted tidy-reason">{skipReasonText(skip.reason)}</span>
          </li>
        ))}
      </ul>
      {hidden > 0 && <p className="muted small tidy-more">{TIDY_COPY.more(hidden)}</p>}
    </details>
  );
}

function Actions({ children }: { children: ReactNode }) {
  return <div className="setting-actions tidy-actions">{children}</div>;
}

/** Preview, move and undo of the folder tidy-up. Opening it is the user's explicit action. */
export function TidyDialog({ tidy, canOpenFolder, onOpenFolder }: Props) {
  const { phase } = tidy;
  const leadRef = useRef<HTMLParagraphElement>(null);
  const kind = phase?.kind;
  // The button that was pressed is gone after each step; the keyboard moves to the new heading line.
  useEffect(() => {
    leadRef.current?.focus();
  }, [kind]);
  if (!phase) return null;

  const folderButton = canOpenFolder && <button type="button" className="btn btn-sm" onClick={onOpenFolder}><FolderIcon />폴더 열기</button>;
  let body: ReactNode;
  if (phase.kind === 'checking') {
    body = (
      <>
        <p className="tidy-lead" role="status" tabIndex={-1} ref={leadRef}><Spinner />{TIDY_COPY.checking}</p>
        <Actions><button type="button" className="btn btn-sm" onClick={tidy.close}>취소</button></Actions>
      </>
    );
  } else if (phase.kind === 'preview') {
    body = (
      <>
        <p className="tidy-lead" tabIndex={-1} ref={leadRef}>{TIDY_COPY.preview(phase.plan.move_count)}</p>
        {tidy.note && <p className="muted small" role="status">{tidy.note}</p>}
        <MoveList plan={phase.plan} />
        <SkipList skips={phase.plan.skips} label={TIDY_COPY.skipsSummary} />
        <p className="muted small">{TIDY_COPY.openFiles}</p>
        <Actions>
          <button type="button" className="btn btn-sm" onClick={tidy.close}>취소</button>
          <button type="button" className="btn btn-sm" onClick={tidy.apply}>정리하기</button>
        </Actions>
      </>
    );
  } else if (phase.kind === 'empty') {
    body = (
      <>
        <p className="tidy-lead" tabIndex={-1} ref={leadRef}>{TIDY_COPY.empty}</p>
        <Actions><button type="button" className="btn btn-sm" onClick={tidy.close}>닫기</button></Actions>
      </>
    );
  } else if (phase.kind === 'moving') {
    const apply = phase.job === 'apply';
    const percent = phase.total > 0 ? Math.min(100, Math.round((phase.done / phase.total) * 100)) : 0;
    body = (
      <>
        <p className="tidy-lead" tabIndex={-1} ref={leadRef}>
          <Spinner />{apply ? TIDY_COPY.moving(phase.done, phase.total) : TIDY_COPY.undoing}
        </p>
        {apply && (
          <div className="progress" role="progressbar" aria-label="정리 진행" aria-valuemin={0} aria-valuemax={phase.total} aria-valuenow={phase.done}>
            <div className="progress-fill" style={{ width: `${percent}%` }} />
          </div>
        )}
        {apply && (
          <>
            <Actions>
              <button type="button" className="btn btn-sm" disabled={tidy.stopping} onClick={() => void tidy.stop()}>{tidy.stopping ? '멈추는 중…' : '멈추기'}</button>
            </Actions>
            <p className="muted small">{TIDY_COPY.stopHint}</p>
          </>
        )}
      </>
    );
  } else if (phase.kind === 'result') {
    body = (
      <>
        <p className="tidy-lead" tabIndex={-1} ref={leadRef}>{TIDY_COPY.result(phase.result.moved, phase.result.skipped)}</p>
        <SkipList skips={phase.result.skips} label={TIDY_COPY.skipsSummaryAfter} />
        <Actions>
          {folderButton}
          {phase.result.undo_available && <button type="button" className="btn btn-sm" onClick={() => tidy.undo(phase.result.run_id)}>되돌리기</button>}
          <button type="button" className="btn btn-sm" onClick={tidy.close}>닫기</button>
        </Actions>
      </>
    );
  } else if (phase.kind === 'undone') {
    body = (
      <>
        <p className="tidy-lead" tabIndex={-1} ref={leadRef}>{TIDY_COPY.undone(phase.result.restored, phase.result.skipped)}</p>
        <SkipList skips={phase.result.skips} label={TIDY_COPY.skipsSummaryAfter} />
        <Actions>
          {folderButton}
          <button type="button" className="btn btn-sm" onClick={tidy.close}>닫기</button>
        </Actions>
      </>
    );
  } else {
    body = (
      <>
        <p className="notice notice-danger" role="alert" tabIndex={-1} ref={leadRef}>{phase.message}</p>
        <Actions><button type="button" className="btn btn-sm" onClick={tidy.close}>닫기</button></Actions>
      </>
    );
  }

  return (
    <Dialog title="폴더 정리" wide onClose={tidy.close} hideClose={phase.kind === 'moving'}>
      <section className="tidy">{body}</section>
    </Dialog>
  );
}
