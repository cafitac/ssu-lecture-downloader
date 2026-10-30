import { useEffect, useId, useRef, useState } from 'react';
import type { AppUpdate } from '../hooks/useAppUpdate';
import { BUSY_HELD_HINT, NO_NOTES, chipLabel, isChipVisible, isInstalling, progressPercent, statusText } from '../lib/appUpdate';
import type { UpdateState } from '../lib/appUpdate';

interface ViewProps {
  state: UpdateState;
  open: boolean;
  busyHeld?: boolean;
  onToggle: () => void;
  onUpdate: () => void;
  onLater: () => void;
}

/**
 * The update notice. Only a small chip in the top bar; the details and the buttons live in a
 * panel the user opens. The panel is not a dialog: nothing is blocked, focus is never moved, no
 * keys are captured, and a click outside or Esc closes it.
 */
export function UpdateChipView({ state, open, busyHeld = false, onToggle, onUpdate, onLater }: ViewProps) {
  const panelId = useId();
  if (!isChipVisible(state)) return null;
  const working = isInstalling(state.phase);
  const status = statusText(state);
  const percent = state.phase === 'downloading' ? progressPercent(state.progress) : null;
  return (
    <div className="update-anchor">
      <button
        type="button"
        className="update-chip"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        title="업데이트 내용 보기"
        onClick={onToggle}
      >
        <i aria-hidden="true" />
        {chipLabel(state)}
      </button>
      {open && (
        <section className="update-panel" id={panelId} aria-label={`새 버전 ${state.version} 안내`}>
          <h2 className="update-panel-title">새 버전 {state.version}</h2>
          <div className="update-notes" tabIndex={0} aria-label="변경 내용">{state.notes ?? NO_NOTES}</div>
          {status && (
            <div className="update-status" role="status">
              <span>{status}</span>
              {state.phase === 'downloading' && (
                <div
                  className={`update-progress${percent === null ? ' is-indeterminate' : ''}`}
                  role="progressbar"
                  aria-label="내려받기 진행률"
                  aria-valuemin={0}
                  aria-valuemax={100}
                  aria-valuenow={percent ?? undefined}
                >
                  <i style={percent === null ? undefined : { width: `${percent}%` }} />
                </div>
              )}
            </div>
          )}
          {busyHeld && state.phase === 'available' && <p className="update-hint">{BUSY_HELD_HINT}</p>}
          {state.notice && <p className="update-message" role="status">{state.notice}</p>}
          {state.error && <p className="update-message is-error" role="status">{state.error}</p>}
          {!working && (state.phase === 'available' || state.canRetry) && (
            <div className="update-actions">
              <button type="button" className="btn btn-sm update-go" onClick={onUpdate}>
                {state.phase === 'failed' ? '다시 시도' : '지금 업데이트'}
              </button>
              <button type="button" className="btn btn-sm" onClick={onLater}>나중에</button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}

interface Props {
  /** The one app-wide update controller (owned by the App root, so it outlives sign-in and backend state). */
  update: AppUpdate;
  /** A small chip in the window corner, for the screens that have no top bar (login, backend problem). */
  corner?: boolean;
  /** The last workspace left tracked work running that cannot be seen from here (login screen). */
  busyHeld?: boolean;
}

export function UpdateChip({ update: appUpdate, corner = false, busyHeld = false }: Props) {
  const { state, update, later } = appUpdate;
  const [open, setOpen] = useState(false);
  const anchorRef = useRef<HTMLDivElement>(null);
  const visible = isChipVisible(state);

  // A panel for a chip that is gone (after "나중에") must not come back with the next update.
  useEffect(() => {
    if (!visible) setOpen(false);
  }, [visible]);

  // Outside click and Esc close the panel. Nothing is prevented or swallowed, and focus stays
  // wherever it was (Esc puts it back on the chip only when it was inside the panel).
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: MouseEvent) => {
      const anchor = anchorRef.current;
      if (anchor && event.target instanceof Node && !anchor.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      const anchor = anchorRef.current;
      if (anchor && document.activeElement instanceof HTMLElement && anchor.contains(document.activeElement)) {
        anchor.querySelector<HTMLElement>('.update-chip')?.focus();
      }
      setOpen(false);
    };
    document.addEventListener('mousedown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('mousedown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <div ref={anchorRef} className={corner ? 'update-host update-corner' : 'update-host'}>
      <UpdateChipView
        state={state}
        open={open}
        busyHeld={busyHeld}
        onToggle={() => setOpen((current) => !current)}
        onUpdate={() => void update()}
        onLater={() => { later(); setOpen(false); }}
      />
    </div>
  );
}
