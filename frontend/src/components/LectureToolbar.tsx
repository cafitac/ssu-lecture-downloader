import type { LectureTab } from '../lib/content';
import type { WatchMode, WatchQueueState } from '../lib/types';
import { WATCH_WINDOW_HINT } from '../lib/watch';

interface Props {
  tab: LectureTab;
  videoCount: number;
  materialCount: number;
  taskCount: number;
  unwatchedOnly: boolean;
  queue: WatchQueueState;
  /** How a started queue plays (decides the window sentence of the tooltip). */
  watchMode: WatchMode;
  /** Open, unwatched videos the queue would play. */
  watchCount: number;
  watchSelected: boolean;
  watchDisabled: boolean;
  onTabChange: (tab: LectureTab) => void;
  onUnwatchedOnlyChange: (value: boolean) => void;
  onStartWatch: () => void;
}

export function LectureToolbar({
  tab, videoCount, materialCount, taskCount, unwatchedOnly, queue, watchMode, watchCount, watchSelected, watchDisabled, onTabChange, onUnwatchedOnlyChange, onStartWatch,
}: Props) {
  return (
    <div className="toolbar">
      <div className="tabs" role="tablist" aria-label="보기">
        <button type="button" role="tab" aria-selected={tab === 'video'} className="tab" onClick={() => onTabChange('video')}>
          영상 <span className="tab-count">{videoCount}</span>
        </button>
        <button type="button" role="tab" aria-selected={tab === 'material'} className="tab" onClick={() => onTabChange('material')}>
          자료 <span className="tab-count">{materialCount}</span>
        </button>
        <button type="button" role="tab" aria-selected={tab === 'task'} className="tab" onClick={() => onTabChange('task')}>
          과제·퀴즈 <span className="tab-count">{taskCount}</span>
        </button>
      </div>
      {tab === 'video' && (
        <label className="switch">
          <input type="checkbox" role="switch" checked={unwatchedOnly} onChange={(event) => onUnwatchedOnlyChange(event.currentTarget.checked)} />
          <span className="switch-track" aria-hidden="true" />
          안 본 것만
        </label>
      )}
      <span className="toolbar-spacer" />
      {tab === 'video' && (
        <button
          type="button"
          className="btn btn-sm"
          disabled={watchDisabled || queue.active}
          title={
            queue.active
              ? '자동 재생 중이에요. 위쪽 상태 줄에서 멈출 수 있어요.'
              : watchSelected
                ? `선택한 강의 ${watchCount}개를 목록 순서대로 소리 없이 자동 재생해요. ${WATCH_WINDOW_HINT[watchMode]}`
                : watchCount === 0
                  ? '열려 있는데 아직 안 본 강의가 없어요. 다시 보려면 강의를 선택하세요.'
                  : `열린 안 본 강의 ${watchCount}개를 마감이 빠른 순서로 소리 없이 자동 재생해요. ${WATCH_WINDOW_HINT[watchMode]}`
          }
          onClick={onStartWatch}
        >
          {queue.active ? `순서대로 시청 중 (${Math.min(queue.index + 1, queue.total)}/${queue.total})` : watchSelected ? `선택한 ${watchCount}개 시청` : watchCount > 0 ? `순서대로 시청 (${watchCount})` : '순서대로 시청'}
        </button>
      )}
    </div>
  );
}
