import { useEffect, useRef, useState } from 'react';
import type { GradesController } from '../hooks/useGrades';
import type { LibraryTidy } from '../hooks/useLibraryTidy';
import type { MediaDependencies } from '../hooks/useMediaDependencies';
import type { RemoteStt } from '../hooks/useRemoteStt';
import { useReminders } from '../hooks/useReminders';
import { formatDeadline } from '../lib/deadline';
import { isTauriRuntime } from '../lib/desktop';
import { MAC_STUDIO_PRESET, REMOTE_KIND_LABEL, REMOTE_STT_NOTE, draftFromStatus, patchFromDraft, remoteSummary } from '../lib/remoteStt';
import type { RemoteSttDraft } from '../lib/remoteStt';
import type { RemoteSttKind, WatchDebugEvent, WatchMode } from '../lib/types';
import { TIDY_COPY } from '../lib/tidy';
import { CHROME_MISSING_NOTE, WATCH_MODE_DESCRIPTION, WATCH_MODE_LABEL } from '../lib/watch';
import { Dialog } from './Dialog';
import { FolderIcon } from './Icons';
import { Spinner } from './Screens';
import { TidyEntries } from './TidyDialog';

interface Props {
  downloadDir: string;
  busy: boolean;
  /** Any tracked backend work (download, component install, attachment save) is running: signing out waits. */
  workBusy: boolean;
  media: MediaDependencies;
  debugEvents: WatchDebugEvent[];
  grades: GradesController;
  /** The mode a started queue plays in, whether Chrome is installed (null: not known yet) and whether a queue runs now. */
  watchMode: WatchMode;
  chromeAvailable: boolean | null;
  queueActive: boolean;
  /** The folder tidy-up and why it cannot start now (a download or an attachment save is running). */
  tidy: LibraryTidy;
  tidyBlocked: boolean;
  onTidy: () => void;
  onUndoTidy: () => void;
  onWatchModeChange: (mode: WatchMode) => void;
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

function RemoteSttSection({ remote }: { remote: RemoteStt }) {
  const { status, busy } = remote;
  const [draft, setDraft] = useState<RemoteSttDraft>(() => draftFromStatus(status));
  const [formError, setFormError] = useState<string | null>(null);
  // Every successful save returns the stored settings; the form follows them (and forgets the typed API key).
  useEffect(() => setDraft(draftFromStatus(status)), [status]);
  const working = busy !== null;
  const openAiKind = draft.kind === 'openai_compatible';
  const usesTunnel = draft.tunnelHost.trim() !== '';
  const summary = remoteSummary(status);

  function edit<K extends keyof RemoteSttDraft>(key: K, value: RemoteSttDraft[K]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setFormError(null);
  }

  async function saveForm(enabled?: boolean): Promise<boolean> {
    const built = patchFromDraft(draft, status);
    if ('error' in built) {
      setFormError(built.error);
      return false;
    }
    setFormError(null);
    return remote.save(enabled === undefined ? built.patch : { ...built.patch, enabled });
  }

  async function toggle(enabled: boolean) {
    if (!enabled) {
      await remote.save({ enabled: false });
      return;
    }
    await saveForm(true);
  }

  async function usePreset() {
    setFormError(null);
    if (await remote.save(MAC_STUDIO_PRESET)) await remote.check();
  }

  async function checkConnection() {
    if (await saveForm()) await remote.check();
  }

  return (
    <div className="subsetting">
      <h4>
        원격 자막 서버{' '}
        <span className={`chip${summary.tone === 'success' ? ' chip-success' : summary.tone === 'warning' ? ' chip-warning' : ''}`}>{summary.label}</span>
      </h4>
      <label className="switch">
        <input
          type="checkbox"
          role="switch"
          checked={status?.enabled ?? false}
          disabled={status === null || working}
          onChange={(event) => void toggle(event.currentTarget.checked)}
        />
        <span className="switch-track" aria-hidden="true" />
        원격 서버로 자막 만들기
      </label>
      <div className="setting-actions">
        <button type="button" className="btn btn-sm" disabled={status === null || working} onClick={() => void usePreset()}>Mac Studio 사용</button>
      </div>
      <div className="form-grid">
        <label className="field">
          <span>종류</span>
          <select className="select" value={draft.kind} disabled={working} onChange={(event) => edit('kind', event.currentTarget.value as RemoteSttKind)}>
            {(Object.keys(REMOTE_KIND_LABEL) as RemoteSttKind[]).map((kind) => <option key={kind} value={kind}>{REMOTE_KIND_LABEL[kind]}</option>)}
          </select>
        </label>
        <label className="field">
          <span>주소</span>
          <input type="text" value={draft.baseUrl} placeholder="http://127.0.0.1:8795" disabled={working || usesTunnel} autoComplete="off" spellCheck={false} onChange={(event) => edit('baseUrl', event.currentTarget.value)} />
        </label>
        {openAiKind && (
          <>
            <label className="field">
              <span>모델</span>
              <input type="text" value={draft.model} placeholder="whisper-1" disabled={working} autoComplete="off" spellCheck={false} onChange={(event) => edit('model', event.currentTarget.value)} />
            </label>
            <label className="field">
              <span>API 키</span>
              <input type="password" value={draft.apiKey} placeholder={status?.api_key_set ? '저장돼 있어요 (바꾸려면 새로 입력)' : '필요한 서버만 입력'} disabled={working} autoComplete="off" onChange={(event) => edit('apiKey', event.currentTarget.value)} />
            </label>
          </>
        )}
        <label className="field">
          <span>SSH 터널 호스트</span>
          <input type="text" value={draft.tunnelHost} placeholder="macstudio (쓰지 않으면 비워 두세요)" disabled={working} autoComplete="off" spellCheck={false} onChange={(event) => edit('tunnelHost', event.currentTarget.value)} />
        </label>
        {usesTunnel && (
          <label className="field">
            <span>원격 포트</span>
            <input type="text" inputMode="numeric" value={draft.tunnelRemotePort} placeholder="8795" disabled={working} autoComplete="off" onChange={(event) => edit('tunnelRemotePort', event.currentTarget.value)} />
          </label>
        )}
      </div>
      {usesTunnel && <p className="muted small">SSH 터널을 쓰면 주소는 자동으로 정해져요. 터널은 필요할 때 앱이 열고, 앱을 끄면 닫혀요.</p>}
      <div className="setting-actions">
        <button type="button" className="btn btn-sm" disabled={status === null || working} onClick={() => void checkConnection()}>
          {busy === 'checking' ? <><Spinner />확인하는 중…</> : '연결 확인'}
        </button>
        <button type="button" className="btn btn-sm" disabled={status === null || working} onClick={() => void saveForm()}>
          {busy === 'saving' ? <><Spinner />저장하는 중…</> : '저장'}
        </button>
        {openAiKind && status?.api_key_set && (
          <button type="button" className="btn btn-sm" disabled={working} onClick={() => void remote.save({ api_key: '' })}>API 키 지우기</button>
        )}
      </div>
      {formError && <p className="notice notice-danger" role="alert">{formError}</p>}
      {remote.outcome && (remote.outcome.ok
        ? <p className="small" role="status">{remote.outcome.text}</p>
        : <p className="notice notice-danger" role="alert">{remote.outcome.text}</p>)}
      <p className="muted small">{REMOTE_STT_NOTE}</p>
    </div>
  );
}

function RemoteComponentRow({ remote }: { remote: RemoteStt }) {
  const summary = remoteSummary(remote.status);
  return (
    <li className="setting-row">
      <span>자막 생성 (원격)</span>
      <span className={`chip${summary.tone === 'success' ? ' chip-success' : ' chip-warning'}`}>{summary.label}</span>
    </li>
  );
}

function WatchModeSection({ mode, chromeAvailable, queueActive, onChange }: { mode: WatchMode; chromeAvailable: boolean | null; queueActive: boolean; onChange: (mode: WatchMode) => void }) {
  const modes: WatchMode[] = ['chrome', 'app'];
  return (
    <section className="setting">
      <h3>자동 재생 방식</h3>
      <div className="choice-list" role="radiogroup" aria-label="자동 재생 방식">
        {modes.map((option) => {
          const unavailable = option === 'chrome' && chromeAvailable === false;
          return (
            <label key={option} className="choice">
              <input
                type="radio"
                name="watch-mode"
                value={option}
                checked={mode === option}
                disabled={unavailable || queueActive}
                onChange={() => onChange(option)}
              />
              <span>{WATCH_MODE_LABEL[option]}</span>
            </label>
          );
        })}
      </div>
      <p className="muted small">{WATCH_MODE_DESCRIPTION[mode]}</p>
      {chromeAvailable === false && <p className="muted small">{CHROME_MISSING_NOTE}</p>}
      {queueActive && <p className="muted small">자동 재생 중에는 바꿀 수 없어요.</p>}
    </section>
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

function GradesSection({ grades }: { grades: GradesController }) {
  const { consent, refreshStatus, revoke } = grades;
  const [turningOff, setTurningOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const offRef = useRef<HTMLParagraphElement>(null);
  const [focusOff, setFocusOff] = useState(false);
  // The 끄기 button disappears when it works; the keyboard moves to the line that replaces it.
  useEffect(() => {
    if (focusOff && consent === 'off') {
      offRef.current?.focus();
      setFocusOff(false);
    }
  }, [focusOff, consent]);
  // Asks only whether the user agreed; nothing is read from u-SAINT here.
  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  async function turnOff() {
    setTurningOff(true);
    setError(null);
    const problem = await revoke();
    setError(problem);
    setTurningOff(false);
    if (!problem) setFocusOff(true);
  }

  return (
    <section className="setting">
      <h3>성적 보기</h3>
      {consent === 'on' ? (
        <>
          <p className="muted small">u-SAINT에서 성적을 읽어 와요</p>
          <div className="setting-actions">
            <button type="button" className="btn btn-sm" disabled={turningOff} onClick={() => void turnOff()}>
              {turningOff ? '끄는 중…' : '끄기'}
            </button>
          </div>
        </>
      ) : consent === 'off' ? (
        <p className="muted small" ref={offRef} tabIndex={-1}>꺼져 있어요. 상단 ‘성적’에서 켤 수 있어요.</p>
      ) : consent === 'error' ? (
        <p className="muted small">성적 보기 상태를 확인하지 못했어요.</p>
      ) : (
        <p className="muted small" role="status">확인하는 중이에요…</p>
      )}
      {error && <p className="notice notice-danger" role="alert">{error}</p>}
    </section>
  );
}

function RemindersSection() {
  const reminders = useReminders(true);
  const { status } = reminders;
  const savedAt = status?.last_saved_at ? formatDeadline(new Date(status.last_saved_at * 1000)) : '아직 없어요';
  return (
    <section className="setting">
      <h3>기한 알림</h3>
      <label className="switch">
        <input
          type="checkbox"
          role="switch"
          checked={status?.enabled ?? false}
          disabled={status === null || reminders.busy}
          onChange={(event) => void reminders.setOn(event.currentTarget.checked)}
        />
        <span className="switch-track" aria-hidden="true" />
        기한 하루 전과 3시간 전에 알림 받기
      </label>
      <p className="muted small">앱이 꺼져 있어도 마지막으로 불러온 기한으로 알려 줘요. 기한이 바뀌었으면 앱을 한 번 열어 새로 불러와 주세요.</p>
      {status && !status.supported && <p className="muted small">이 빌드에서는 앱이 켜져 있는 동안에만 알려 줘요.</p>}
      <p className="muted small">마지막으로 불러온 시각: {status ? savedAt : '확인 중'}</p>
      <div className="setting-actions">
        <button type="button" className="btn btn-sm" onClick={() => void reminders.sendTest()}>알림 테스트</button>
      </div>
      {reminders.testSent && <p className="muted small" role="status">알림을 보냈어요. 보이지 않으면 시스템 설정의 알림에서 허용해 주세요.</p>}
      {reminders.error && <p className="notice notice-danger" role="alert">{reminders.error}</p>}
    </section>
  );
}

export function SettingsDialog({ downloadDir, busy, workBusy, media, debugEvents, grades, watchMode, chromeAvailable, queueActive, tidy, tidyBlocked, onTidy, onUndoTidy, onWatchModeChange, onPickFolder, onOpenFolder, onSignOut, onClose }: Props) {
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
          <TidyEntries tidy={tidy} blocked={tidyBlocked} onTidy={onTidy} onUndoTidy={onUndoTidy} />
        </div>
        {tidyBlocked && tidy.runs.state !== 'unsupported' && <p className="muted small">{TIDY_COPY.busyHint}</p>}
      </section>

      <section className="setting">
        <h3>음성·자막 구성요소</h3>
        <ul className="setting-list">
          <ComponentRow name="음성 변환" ready={known ? media.ffmpegDependency?.available ?? null : null} />
          {media.subtitlesViaRemote
            ? <RemoteComponentRow remote={media.remote} />
            : <ComponentRow name="자막 생성" ready={known ? media.whisperStatus?.available ?? null : null} unsupported={media.subtitlesUnsupportedReason !== null} />}
        </ul>
        <p className="muted small">
          {media.subtitlesViaRemote
            ? '자막은 원격 서버가 만들어요. 이 Mac에는 자막 엔진을 설치하지 않아도 돼요.'
            : media.subtitlesUnsupportedReason ?? '자막은 Whisper large-v3 모델로 만들어요. 처음 만들 때는 모델을 준비하느라 오래 걸릴 수 있어요.'}
        </p>
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
        <RemoteSttSection remote={media.remote} />
      </section>

      <WatchModeSection mode={watchMode} chromeAvailable={chromeAvailable} queueActive={queueActive} onChange={onWatchModeChange} />

      <section className="setting">
        <h3>계정</h3>
        <p className="muted small">앱을 끄면 로그인 정보가 지워져요.</p>
        <div className="setting-actions">
          <button type="button" className="btn btn-sm" disabled={signingOut || busy || workBusy} title={busy ? '다운로드가 끝나면 로그아웃할 수 있어요' : workBusy ? '설치나 저장이 끝나면 로그아웃할 수 있어요' : undefined} onClick={() => void handleSignOut()}>
            {signingOut ? '로그아웃하는 중…' : '로그아웃'}
          </button>
        </div>
        {signOutError && <p className="notice notice-danger" role="alert">{signOutError}</p>}
      </section>

      <GradesSection grades={grades} />

      {desktop && <RemindersSection />}

      {debugEvents.length > 0 && <WatchDiagnostics events={debugEvents} />}
    </Dialog>
  );
}
