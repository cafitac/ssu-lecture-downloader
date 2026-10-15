export type WatchDebugEvent = {
  stage: string;
  mode?: string;
  frame?: string;
  host?: string;
  ready_state?: string;
  videos?: string;
  video_state?: string;
  buttons?: string;
  selector?: string;
  error_name?: string;
  error_message?: string;
};

export type WatchQueueState = { active: boolean; index: number; total: number };

interface ControlProps {
  queue: WatchQueueState;
  /** Start is disabled when there is nothing to watch or the UI is busy. */
  startDisabled: boolean;
  onStart: () => void;
  onStop: () => void;
}

/** Toolbar button that starts or stops the experimental sequential-watch mode. */
export function WatchQueueControl({ queue, startDisabled, onStart, onStop }: ControlProps) {
  if (queue.active) {
    return (
      <button type="button" className="secondary-action" onClick={onStop}>
        순차 시청 중지 ({Math.min(queue.index + 1, queue.total)}/{queue.total})
      </button>
    );
  }
  return (
    <button type="button" className="secondary-action" disabled={startDisabled} onClick={onStart}>
      표시된 영상 순차 시청
    </button>
  );
}

export function WatchDebugPanel({ events }: { events: WatchDebugEvent[] }) {
  if (events.length === 0) return null;
  return (
    <details className="watch-debug-panel" open>
      <summary>공식 플레이어 재생 진단 로그 {events.length}개</summary>
      <div className="watch-debug-list">
        {events.slice(0, 12).map((event, index) => (
          <div className="watch-debug-row" key={`${event.stage}-${index}`}>
            <strong>{event.stage}</strong>
            <span>{[event.mode, event.frame, event.host, event.ready_state ? `ready=${event.ready_state}` : null, event.videos ? `videos=${event.videos}` : null, event.buttons ? `buttons=${event.buttons}` : null].filter(Boolean).join(' · ')}</span>
            {event.video_state && <code>{event.video_state}</code>}
            {event.selector && <code>{event.selector}</code>}
            {(event.error_name || event.error_message) && <em>{[event.error_name, event.error_message].filter(Boolean).join(': ')}</em>}
          </div>
        ))}
      </div>
    </details>
  );
}
