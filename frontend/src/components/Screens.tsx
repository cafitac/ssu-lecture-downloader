import type { FormEvent, ReactNode } from 'react';
import type { BackendSupervisionStatus } from '../lib/types';
import { AlertIcon } from './Icons';

function CenterCard({ children, busy }: { children: ReactNode; busy?: boolean }) {
  return (
    <main className="center-screen">
      <section className="center-card" aria-busy={busy}>{children}</section>
    </main>
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function SplashScreen({ text }: { text: string }) {
  return (
    <CenterCard busy>
      <p className="center-status" role="status"><Spinner />{text}</p>
    </CenterCard>
  );
}

interface LoginProps {
  notice: string | null;
  busy: boolean;
  onSubmit: (username: string, password: string) => void;
}

export function LoginScreen({ notice, busy, onSubmit }: LoginProps) {
  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy) return;
    const data = new FormData(event.currentTarget);
    onSubmit(String(data.get('username') ?? '').trim(), String(data.get('password') ?? ''));
  }
  return (
    <CenterCard busy={busy}>
      <h1 className="center-title">강의 다운로더</h1>
      <p className="center-sub">숭실 LMS 계정으로 로그인해 주세요.</p>
      <form className="login-form" onSubmit={handleSubmit}>
        <label className="field">
          <span>아이디</span>
          <input name="username" autoComplete="username" autoFocus required disabled={busy} />
        </label>
        <label className="field">
          <span>비밀번호</span>
          <input name="password" type="password" autoComplete="current-password" required disabled={busy} />
        </label>
        {notice && <p className="notice notice-danger" role="alert"><AlertIcon />{notice}</p>}
        <button type="submit" className="btn btn-primary btn-lg" disabled={busy}>
          {busy ? <><Spinner />로그인하는 중…</> : '로그인'}
        </button>
      </form>
      <p className="center-fine">
        계정 정보는 디스크에 저장하지 않고 앱이 켜져 있는 동안 메모리에만 보관합니다. 앱을 종료하면 지워지므로 다음 실행 때 다시 로그인해야 합니다.
      </p>
    </CenterCard>
  );
}

interface BackendProps {
  status: BackendSupervisionStatus;
  onRetry: () => void;
}

/** Shown until the bundled API server is ready, or when it could not start. */
export function BackendScreen({ status, onRetry }: BackendProps) {
  if (status.state === 'failed') {
    return (
      <CenterCard>
        <h1 className="center-title">앱을 시작하지 못했어요</h1>
        <p className="center-sub">내부 서버가 켜지지 않았어요. 다시 시도해도 안 되면 아래 로그 파일을 확인해 주세요.</p>
        {status.log_path && (
          <p className="center-fine">로그 파일 <code className="path">{status.log_path}</code></p>
        )}
        {status.error && (
          <details className="center-details">
            <summary>자세한 오류 보기</summary>
            <pre>{status.error}</pre>
          </details>
        )}
        <button type="button" className="btn btn-lg" onClick={onRetry}>다시 시도</button>
      </CenterCard>
    );
  }
  return (
    <CenterCard busy>
      <p className="center-status" role="status"><Spinner />앱을 준비하고 있어요…</p>
      <p className="center-fine">처음 실행할 때는 최대 90초까지 걸릴 수 있어요.</p>
    </CenterCard>
  );
}
