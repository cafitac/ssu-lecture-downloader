import { useState } from 'react';
import type { MediaDependencies } from '../hooks/useMediaDependencies';
import { isTauriRuntime } from '../lib/desktop';
import type { WatchDebugEvent } from '../lib/types';
import { Dialog } from './Dialog';
import { FolderIcon } from './Icons';
import { Spinner } from './Screens';

interface Props {
  downloadDir: string;
  busy: boolean;
  media: MediaDependencies;
  debugEvents: WatchDebugEvent[];
  onPickFolder: () => void;
  onOpenFolder: () => void;
  onSignOut: () => Promise<string | null>;
  onClose: () => void;
}

function ComponentRow({ name, ready, unsupported = false }: { name: string; ready: boolean | null; unsupported?: boolean }) {
  return (
    <li className="setting-row">
      <span>{name}</span>
      {unsupported ? <span className="chip">지원 안 함</span> : ready === null ? <span className="chip">확인 중</span> : ready ? <span className="chip chip-success">사용 가능</span> : <span className="chip chip-warning">설치 필요</span>}
    </li>
  );
}

function WatchDiagnostics({ events }: { events: WatchDebugEvent[] }) {
  return (
    <details className="diagnostics">
      <summary>시청 창 재생 기록 {events.length}개</summary>
      <ul>
        {events.slice(0, 12).map((event, index) => (
          <li key={`${event.stage}-${index}`}>
            <strong>{event.stage}</strong>{' '}
            {[event.mode, event.frame, event.host, event.ready_state ? `ready=${event.ready_state}` : null, event.videos ? `videos=${event.videos}` : null, event.buttons ? `buttons=${event.buttons}` : null].filter(Boolean).join(' · ')}
            {event.video_state && <code>{event.video_state}</code>}
            {event.selector && <code>{event.selector}</code>}
            {(event.error_name || event.error_message) && <em>{[event.error_name, event.error_message].filter(Boolean).join(': ')}</em>}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function SettingsDialog({ downloadDir, busy, media, debugEvents, onPickFolder, onOpenFolder, onSignOut, onClose }: Props) {
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);
  const desktop = isTauriRuntime();
  const missing = media.missingComponents;
  const known = media.ffmpegDependency !== undefined || media.whisperStatus !== undefined;

  async function handleSignOut() {
    setSigningOut(true);
    setSignOutError(null);
    const error = await onSignOut();
    if (error) {
      setSignOutError(error);
      setSigningOut(false);
    }
  }

  return (
    <Dialog title="설정" onClose={onClose}>
      <section className="setting">
        <h3>저장 폴더</h3>
        <p className="path">{downloadDir}</p>
        <div className="setting-actions">
          <button
            type="button"
            className="btn btn-sm"
            disabled={!desktop || busy}
            title={!desktop ? '데스크톱 앱에서만 바꿀 수 있어요' : busy ? '다운로드가 끝나면 바꿀 수 있어요' : undefined}
            onClick={onPickFolder}
          >
            변경
          </button>
          {desktop && <button type="button" className="btn btn-sm" onClick={onOpenFolder}><FolderIcon />폴더 열기</button>}
        </div>
      </section>

      <section className="setting">
        <h3>음성·자막 구성요소</h3>
        <ul className="setting-list">
          <ComponentRow name="음성 변환" ready={known ? media.ffmpegDependency?.available ?? null : null} />
          <ComponentRow name="자막 생성" ready={known ? media.whisperStatus?.available ?? null : null} unsupported={media.subtitlesUnsupportedReason !== null} />
        </ul>
        <p className="muted small">{media.subtitlesUnsupportedReason ?? '자막은 Whisper large-v3 모델로 만들어요. 처음 만들 때는 모델을 준비하느라 오래 걸릴 수 있어요.'}</p>
        <div className="setting-actions">
          {missing.length > 0 && (
            <button
              type="button"
              className="btn btn-sm"
              disabled={!media.canInstall}
              title={!media.autoInstallAvailable ? media.autoInstallUnavailableReason ?? undefined : undefined}
              onClick={() => void media.install()}
            >
              {media.isInstalling ? <><Spinner />설치 중…</> : '설치'}
            </button>
          )}
          <button type="button" className="btn btn-sm" disabled={media.isInstalling} onClick={() => void media.load()}>다시 확인</button>
        </div>
        {missing.length > 0 && !media.autoInstallAvailable && media.autoInstallUnavailableReason && (
          <p className="muted small">{media.autoInstallUnavailableReason}</p>
        )}
      </section>

      <section className="setting">
        <h3>계정</h3>
        <p className="muted small">앱을 끄면 로그인 정보가 지워져요.</p>
        <div className="setting-actions">
          <button type="button" className="btn btn-sm" disabled={signingOut || busy} title={busy ? '다운로드가 끝나면 로그아웃할 수 있어요' : undefined} onClick={() => void handleSignOut()}>
            {signingOut ? '로그아웃하는 중…' : '로그아웃'}
          </button>
        </div>
        {signOutError && <p className="notice notice-danger" role="alert">{signOutError}</p>}
      </section>

      {debugEvents.length > 0 && <WatchDiagnostics events={debugEvents} />}
    </Dialog>
  );
}
