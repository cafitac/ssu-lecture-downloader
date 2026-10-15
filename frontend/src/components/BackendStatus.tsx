import type { BackendSupervisionStatus } from '../lib/types';

interface Props {
  status: BackendSupervisionStatus;
  onRetry: () => void;
}

/** Full-screen state shown until the Python API backend is ready. */
export function BackendStatus({ status, onRetry }: Props) {
  if (status.state === 'failed') {
    return (
      <main className="app-shell login-shell">
        <section className="login-card panel backend-status failed" role="alert">
          <div className="panel-header">
            <h2>API 서버를 시작하지 못했습니다</h2>
          </div>
          <p className="status error">{status.error ?? '알 수 없는 오류로 Python API 서버를 시작하지 못했습니다.'}</p>
          {status.log_path && (
            <p className="hint">
              자세한 내용은 로그 파일에서 확인할 수 있습니다: <code>{status.log_path}</code>
            </p>
          )}
          <button type="button" className="primary" onClick={onRetry}>다시 시도</button>
        </section>
      </main>
    );
  }
  return (
    <main className="app-shell login-shell">
      <section className="login-card panel backend-status starting" aria-busy="true">
        <div className="panel-header">
          <h2>API 서버를 시작하는 중입니다</h2>
        </div>
        <div className="inline-loading" role="status" aria-live="polite">
          <span className="mini-spinner" />
          <span>Python API 서버를 준비하고 있습니다. 처음 실행하면 최대 90초까지 걸릴 수 있습니다.</span>
        </div>
      </section>
    </main>
  );
}
