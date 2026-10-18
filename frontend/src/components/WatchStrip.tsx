import { WATCH_STRIP_NOTE, WATCH_STRIP_NOTE_CHROME, watchStripText } from '../lib/watch';
import type { WatchQueueState } from '../lib/types';
import { Spinner } from './Screens';

interface Props {
  queue: WatchQueueState;
  onNext: () => void;
  onStop: () => void;
  onShowWindow: () => void;
}

/** Status of the running "순서대로 시청" queue, shown above the workspace whatever view is open. */
export function WatchStrip({ queue, onNext, onStop, onShowWindow }: Props) {
  const text = watchStripText(queue);
  if (!text) return null;
  const waiting = queue.phase === 'opening' || queue.phase === 'stalled';
  const chrome = queue.source === 'chrome';
  return (
    <section className="watch-strip" aria-label="자동 재생 상태">
      <div className="watch-strip-main">
        <p className="watch-strip-summary" role="status" title={text.summary}>
          {waiting && <Spinner />}
          {text.summary}
        </p>
        <div className="watch-strip-actions">
          <button type="button" className="btn btn-sm btn-ghost" onClick={onShowWindow}>{chrome ? 'Chrome 창 보이기' : '재생 창 보이기'}</button>
          <button type="button" className="btn btn-sm" onClick={onNext}>다음</button>
          <button type="button" className="btn btn-sm" onClick={onStop}>중지</button>
        </div>
      </div>
      <p className={`watch-strip-note${text.detail ? ' has-detail' : ''}`}>
        {text.detail ? `${text.detail} ` : ''}{chrome ? WATCH_STRIP_NOTE_CHROME : WATCH_STRIP_NOTE}
      </p>
    </section>
  );
}
