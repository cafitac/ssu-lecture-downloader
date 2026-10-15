import type { ReactNode } from 'react';
import { contentKindLabel, formatBytes, formatDuration } from '../lib/format';
import type { CourseContentItem } from '../lib/types';

interface Props {
  contentItems: CourseContentItem[];
  filteredContentItems: CourseContentItem[];
  availableWeeks: string[];
  completedCount: number;
  weekFilter: string;
  contentKindFilter: string;
  completionFilter: string;
  selectedLecture: CourseContentItem | null;
  selectedLectureIds: Array<string | number>;
  selectedCount: number;
  selectableCount: number;
  allLecturesSelected: boolean;
  /** Only video rows can be produced as MP4/MP3/TXT. */
  selectedOutputRequiresVideo: boolean;
  isCourseDetailLoading: boolean;
  /** Course list/detail loading or a running job: rows and filters are read-only. */
  locked: boolean;
  isOpeningViewer: boolean;
  canWatchLecture: (lecture: CourseContentItem) => boolean;
  onWeekFilterChange: (value: string) => void;
  onContentKindFilterChange: (value: string) => void;
  onCompletionFilterChange: (value: string) => void;
  onSelectAll: (checked: boolean) => void;
  onRowClick: (lecture: CourseContentItem) => void;
  onToggleLecture: (lecture: CourseContentItem) => void;
  onWatch: (lecture: CourseContentItem) => void;
  /** Sequential-watch controls rendered inside the toolbar. */
  watchControl: ReactNode;
}

export function LecturePanel(props: Props) {
  const {
    contentItems, filteredContentItems, availableWeeks, completedCount, weekFilter, contentKindFilter, completionFilter,
    selectedLecture, selectedLectureIds, selectedCount, selectableCount, allLecturesSelected, selectedOutputRequiresVideo,
    isCourseDetailLoading, locked, isOpeningViewer, canWatchLecture,
  } = props;
  return (
    <section className="panel lecture-panel" aria-busy={isCourseDetailLoading}>
      <div className="panel-header">
        <h2>강의 선택</h2>
        <span>{isCourseDetailLoading ? '불러오는 중...' : `${completedCount}/${contentItems.length} 완료`}</span>
      </div>
      <div className="lecture-filters">
        <select aria-label="주차 필터" value={weekFilter} onChange={(event) => props.onWeekFilterChange(event.target.value)} disabled={locked}>
          <option value="all">전체 주차</option>
          {availableWeeks.map((week) => <option key={week} value={week}>{week === 'unknown' ? '주차 정보 없음' : `${week}주차`}</option>)}
        </select>
        <select aria-label="타입 필터" value={contentKindFilter} onChange={(event) => props.onContentKindFilterChange(event.target.value)} disabled={locked}>
          <option value="downloadable">다운로드 가능</option>
          <option value="all">전체 타입</option>
          <option value="video">영상</option>
          <option value="material">PDF/PPT 자료</option>
          <option value="assignment">과제</option>
          <option value="quiz">퀴즈</option>
          <option value="board">게시판</option>
          <option value="page">페이지</option>
        </select>
        <select aria-label="완료 상태 필터" value={completionFilter} onChange={(event) => props.onCompletionFilterChange(event.target.value)} disabled={locked}>
          <option value="all">전체 완료 상태</option>
          <option value="completed">완료</option>
          <option value="uncompleted">미완료</option>
          <option value="unknown">완료 정보 없음</option>
        </select>
      </div>
      <div className="lecture-toolbar">
        <span>{selectedCount}/{selectableCount}개 선택됨 · 표시 {filteredContentItems.length}개</span>
        <label className="select-all-check">
          <input
            type="checkbox"
            checked={allLecturesSelected}
            disabled={selectableCount === 0 || locked}
            onChange={(event) => props.onSelectAll(event.currentTarget.checked)}
          />
          전체 선택
        </label>
        {props.watchControl}
      </div>
      <div className="lecture-list">
        {isCourseDetailLoading && (
          <div className="inline-loading" role="status" aria-live="polite">
            <span className="mini-spinner" />
            <span>선택한 과목의 강의 목록을 불러오는 중입니다.</span>
          </div>
        )}
        {filteredContentItems.map((lecture) => {
          const checked = lecture.module_item_id !== null && selectedLectureIds.some((id) => String(id) === String(lecture.module_item_id));
          return (
            <div
              key={`${lecture.module_item_id}-${lecture.title}`}
              className={`lecture-row ${selectedLecture?.module_item_id === lecture.module_item_id ? 'selected' : ''} ${checked ? 'checked' : ''}`}
              onClick={() => {
                if (locked) return;
                props.onRowClick(lecture);
              }}
            >
              <label className="lecture-check" onClick={(event) => event.stopPropagation()}>
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={!lecture.downloadable || lecture.module_item_id === null || (selectedOutputRequiresVideo && lecture.kind !== 'video') || locked}
                  onChange={() => props.onToggleLecture(lecture)}
                />
                <span>{lecture.downloadable ? selectedOutputRequiresVideo && lecture.kind !== 'video' ? '영상만' : '선택' : '보기 전용'}</span>
              </label>
              <span className="module-name">{lecture.module_name || '주차 정보 없음'}</span>
              <strong>{lecture.title}</strong>
              <span>{contentKindLabel(lecture.kind)} · {lecture.completed === null ? '완료 정보 없음' : lecture.completed ? '완료' : '미완료'} · {lecture.duration ? `길이 ${formatDuration(lecture.duration)} · ` : ''}{lecture.size_bytes ? `크기 ${formatBytes(lecture.size_bytes)} · ` : ''}{lecture.inner_content_type ?? lecture.content_type ?? 'type unknown'}</span>
              {lecture.kind === 'video' && (
                <button
                  className="inline-watch-button"
                  type="button"
                  disabled={!canWatchLecture(lecture) || isOpeningViewer}
                  onClick={(event) => {
                    event.stopPropagation();
                    props.onWatch(lecture);
                  }}
                >
                  시청
                </button>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
