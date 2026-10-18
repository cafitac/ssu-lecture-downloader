import type { AllCoursesQueueItem } from '../lib/watch';
import { formatTotalTime, summarizeAllCoursesQueue, watchAllConfirmText } from '../lib/watch';
import { Dialog } from './Dialog';

interface Props {
  items: AllCoursesQueueItem[];
  onStart: () => void;
  onClose: () => void;
}

const PREVIEW_COUNT = 5;

/** Confirmation before "전체 순서대로 시청": how many lectures, how long, and what the player window needs. */
export function WatchAllDialog({ items, onStart, onClose }: Props) {
  const summary = summarizeAllCoursesQueue(items);
  const text = watchAllConfirmText(summary);
  return (
    <Dialog title="전체 순서대로 시청" onClose={onClose}>
      <section className="setting">
        <h3>{text.headline}</h3>
        <ol className="watch-all-preview">
          {items.slice(0, PREVIEW_COUNT).map((item) => (
            <li key={`${item.course_id}-${item.module_item_id}`} title={item.title}>
              <span className="watch-all-title">{item.title}</span>
              <span className="watch-all-time">{item.duration ? formatTotalTime(item.duration) : '-'}</span>
            </li>
          ))}
        </ol>
        {items.length > PREVIEW_COUNT && <p className="muted small">외 {items.length - PREVIEW_COUNT}개</p>}
        {text.lines.map((line) => <p key={line} className="muted small">{line}</p>)}
        <div className="setting-actions">
          <button type="button" className="btn btn-sm" onClick={onStart}>시작</button>
          <button type="button" className="btn btn-sm" onClick={onClose}>취소</button>
        </div>
      </section>
    </Dialog>
  );
}
