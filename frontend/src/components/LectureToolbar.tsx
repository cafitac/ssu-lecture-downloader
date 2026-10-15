import type { LectureTab } from '../lib/content';
import type { WatchQueueState } from '../lib/types';

interface Props {
  tab: LectureTab;
  videoCount: number;
  materialCount: number;
  taskCount: number;
  unwatchedOnly: boolean;
  queue: WatchQueueState;
  watchDisabled: boolean;
  onTabChange: (tab: LectureTab) => void;
  onUnwatchedOnlyChange: (value: boolean) => void;
  onStartWatch: () => void;
  onStopWatch: () => void;
}

export function LectureToolbar({
  tab, videoCount, materialCount, taskCount, unwatchedOnly, queue, watchDisabled, onTabChange, onUnwatchedOnlyChange, onStartWatch, onStopWatch,
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
        queue.active ? (
          <button type="button" className="btn btn-sm" onClick={onStopWatch}>
            순서대로 시청 중지 ({Math.min(queue.index + 1, queue.total)}/{queue.total})
          </button>
        ) : (
          <button
            type="button"
            className="btn btn-sm"
            disabled={watchDisabled}
            title="목록에 보이는 영상을 차례로 열어 자동 재생을 시도해요 (실험 기능)"
            onClick={onStartWatch}
          >
            순서대로 시청
          </button>
        )
      )}
    </div>
  );
}
