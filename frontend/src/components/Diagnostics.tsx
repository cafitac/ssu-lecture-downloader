import { DIAGNOSTICS_SAVED_TEXT, useDiagnosticsSave } from '../hooks/useDiagnosticsSave';
import type { DiagnosticsOutcome } from '../hooks/useDiagnosticsSave';
import { Spinner } from './Screens';

interface ViewProps {
  busy: boolean;
  outcome: DiagnosticsOutcome | null;
  onSave: () => void;
  onReveal: () => void;
}

/** Settings row "문제 보내기": the save button and its result. Nothing is uploaded. */
export function DiagnosticsSectionView({ busy, outcome, onSave, onReveal }: ViewProps) {
  return (
    <section className="setting">
      <h3>문제 보내기</h3>
      <p className="muted small">이상하게 동작했다면 진단 파일을 저장해 전달해 주세요. 어디로도 자동 전송되지 않아요.</p>
      <div className="setting-actions">
        <button type="button" className="btn btn-sm" disabled={busy} onClick={onSave}>
          {busy ? <><Spinner />저장하는 중…</> : '진단 파일 저장'}
        </button>
        {outcome?.ok && <button type="button" className="btn btn-sm" onClick={onReveal}>폴더에서 보기</button>}
      </div>
      {outcome?.ok && <p className="small" role="status">{DIAGNOSTICS_SAVED_TEXT}</p>}
      {outcome && !outcome.ok && <p className="notice notice-danger" role="alert">{outcome.text}</p>}
    </section>
  );
}

export function DiagnosticsSection() {
  const diagnostics = useDiagnosticsSave();
  return <DiagnosticsSectionView busy={diagnostics.busy} outcome={diagnostics.outcome} onSave={() => void diagnostics.save()} onReveal={() => void diagnostics.reveal()} />;
}
