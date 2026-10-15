import type { FormEvent, RefObject } from 'react';
import type { CredentialStatus } from '../lib/types';

interface Props {
  credentialStatus: CredentialStatus | null;
  formRef: RefObject<HTMLFormElement | null>;
  state: 'idle' | 'loading' | 'ready' | 'error';
  message: string;
  isAuthenticating: boolean;
  onSubmit: (event: FormEvent<HTMLFormElement>) => void;
}

export function LoginPanel({ credentialStatus, formRef, state, message, isAuthenticating, onSubmit }: Props) {
  return (
    <main className="app-shell login-shell">
      <section className="login-card panel">
        <div className="panel-header">
          <h2>숭실 LMS 로그인</h2>
          <span>{credentialStatus?.source ?? 'app'}</span>
        </div>
        <p className="hint">
          처음 실행하면 계정 정보만 입력하면 됩니다. 계정 정보는 이 기기의 앱 저장소에 보관하고, 로그인 세션은 앱 실행 중에만 재사용합니다.
        </p>
        <form ref={formRef} className="credential-form" onSubmit={onSubmit}>
          <input name="username" autoComplete="username" placeholder="SSU LMS ID" aria-label="SSU LMS ID" disabled={isAuthenticating} />
          <input name="password" type="password" autoComplete="current-password" placeholder="SSU LMS password" aria-label="SSU LMS password" disabled={isAuthenticating} />
          <button type="submit" disabled={credentialStatus?.can_write === false || isAuthenticating}>{isAuthenticating ? '로그인 중...' : '로그인'}</button>
        </form>
        <p className={`status login-status ${state}`} role={state === 'error' ? 'alert' : 'status'}>{message}</p>
        <p className="hint">
          비밀번호는 저장 여부만 확인합니다. 화면에는 다시 표시하지 않습니다.
        </p>
      </section>
    </main>
  );
}
