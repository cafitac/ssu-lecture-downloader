import { useCallback, useState } from 'react';
import { openDownloadedFolder, saveDiagnosticsAutomatically } from '../lib/desktop';
import { IDLE_DIAGNOSTICS_STATE } from '../lib/diagnosticsSave';
import type { DiagnosticsOutcome, DiagnosticsSaveState } from '../lib/diagnosticsSave';
import { PROBLEM_HINT, PROBLEM_SAVED_TEXT, PROBLEM_TITLE, problemSentence, runProblemSave } from '../lib/problemPrompt';
import type { ProblemKind } from '../lib/problemPrompt';
import { Dialog } from './Dialog';
import { Spinner } from './Screens';

interface ViewProps {
  kind: ProblemKind;
  busy: boolean;
  outcome: DiagnosticsOutcome | null;
  onSave: () => void;
  onClose: () => void;
}

/** "문제가 생겼어요": what happened in one sentence, then save the diagnostics file or close. */
export function ProblemDialogView({ kind, busy, outcome, onSave, onClose }: ViewProps) {
  const saved = outcome?.ok === true;
  return (
    <Dialog title={PROBLEM_TITLE} onClose={onClose}>
      <section className="problem">
        <p className="problem-lead">{problemSentence(kind)}</p>
        {saved ? (
          <p className="small" role="status">{PROBLEM_SAVED_TEXT}</p>
        ) : (
          <p className="muted small">{PROBLEM_HINT}</p>
        )}
        {outcome && !outcome.ok && <p className="notice notice-danger" role="alert">{outcome.text}</p>}
        <div className="setting-actions problem-actions">
          {!saved && (
            <button type="button" className="btn btn-sm btn-primary" disabled={busy} onClick={onSave}>
              {busy ? <><Spinner />저장하는 중…</> : '진단 파일 저장'}
            </button>
          )}
          <button type="button" className="btn btn-sm" onClick={onClose}>닫기</button>
        </div>
      </section>
    </Dialog>
  );
}

/** Mounted only while the dialog is open, so every opening starts idle. */
export function ProblemDialog({ kind, onClose }: { kind: ProblemKind; onClose: () => void }) {
  const [state, setState] = useState<DiagnosticsSaveState>(IDLE_DIAGNOSTICS_STATE);
  const save = useCallback(() => runProblemSave({ save: () => saveDiagnosticsAutomatically(), reveal: openDownloadedFolder }, setState), []);
  return <ProblemDialogView kind={kind} busy={state.busy} outcome={state.outcome} onSave={() => void save()} onClose={onClose} />;
}
