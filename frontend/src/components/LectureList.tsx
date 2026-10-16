import { Fragment } from 'react';
import type { PageAttachmentsController } from '../hooks/usePageAttachments';
import { itemKey, isSelectable, itemPageUrl, libraryKindChip, triState } from '../lib/content';
import { isOpened, lectureDeadline } from '../lib/deadline';
import type { LectureTab, WeekGroup } from '../lib/content';
import { formatBytes, formatDuration } from '../lib/format';
import type { CourseContentItem, PageFile } from '../lib/types';
import { Checkbox } from './Checkbox';
import { DeadlineCell } from './DeadlineCell';
import { ChevronIcon, DownloadIcon, PlayIcon, WindowIcon } from './Icons';
import { PageAttachmentRows } from './PageAttachments';

function StatusChips({ item, tab, downloaded, now }: { item: CourseContentItem; tab: LectureTab; downloaded: boolean; now: Date }) {
  const opened = isOpened(item, now);
  const kindChip = tab === 'material' ? libraryKindChip(item) : null;
  return (
    <span className="cell-status">
      {kindChip && <span className="chip" title={kindChip}>{kindChip}</span>}
      {tab === 'video' && item.completed === true && <span className="chip chip-success">시청함</span>}
      {tab === 'video' && item.completed === false && opened && <span className="chip chip-warning">안 봄</span>}
      {tab === 'video' && item.completed === false && !opened && <span className="chip">열리기 전</span>}
      {downloaded && <span className="chip chip-info">받음</span>}
    </span>
  );
}

interface RowProps {
  item: CourseContentItem;
  tab: LectureTab;
  checked: boolean;
  downloaded: boolean;
  now: Date;
  /** Why the play / download button is unavailable right now, or `null` when it works. */
  actionBlockedReason: string | null;
  actionBusy: boolean;
  /** Week shown inline when the week has only this row (no separate header). */
  weekLabel?: string;
  /** The 자료 row opens in the app (page, board, other) instead of downloading. */
  openUrl: string | null;
  attachmentsOpen: boolean;
  onToggle: (key: string, checked: boolean) => void;
  onAction: (item: CourseContentItem) => void;
  onOpenItem: (item: CourseContentItem) => void;
  onToggleAttachments: (item: CourseContentItem) => void;
}

function LectureRow({ item, tab, checked, downloaded, now, actionBlockedReason, actionBusy, weekLabel, openUrl, attachmentsOpen, onToggle, onAction, onOpenItem, onToggleAttachments }: RowProps) {
  const key = itemKey(item);
  const selectable = isSelectable(item);
  const opensInApp = tab === 'material' && item.kind !== 'material';
  const canAct = key !== null && (tab === 'material' ? (opensInApp ? openUrl !== null : item.downloadable) : true);
  const hasAttachments = opensInApp && item.kind === 'page' && Boolean(item.page_ref);
  return (
    <label className={`lecture-row${checked ? ' is-checked' : ''}${selectable ? '' : ' is-disabled'}${opensInApp ? ' is-openable' : ''}`}>
      <span className="cell-check">
        <Checkbox
          checked={checked}
          disabled={!selectable}
          label={`${item.title} 선택`}
          onChange={(value) => key !== null && onToggle(key, value)}
        />
      </span>
      <span className="cell-title" title={selectable || opensInApp ? item.title : `${item.title} (받을 수 없는 항목)`}>
        {weekLabel && <span className="week-tag">{weekLabel}</span>}
        {item.title}
      </span>
      {tab === 'video' && <span className="cell-num cell-length">{formatDuration(item.duration)}</span>}
      <span className="cell-num cell-size">{formatBytes(item.size_bytes)}</span>
      {tab === 'video' && <DeadlineCell info={lectureDeadline(item, now)} />}
      <StatusChips item={item} tab={tab} downloaded={downloaded} now={now} />
      <span className="cell-action">
        {hasAttachments && (
          <button
            type="button"
            className="btn btn-icon btn-ghost btn-sm"
            aria-label={`${item.title} 첨부 파일 ${attachmentsOpen ? '접기' : '보기'}`}
            aria-expanded={attachmentsOpen}
            title="첨부 파일 보기"
            onClick={(event) => {
              event.preventDefault();
              onToggleAttachments(item);
            }}
          >
            <ChevronIcon open={attachmentsOpen} />
          </button>
        )}
        {canAct && (
          <button
            type="button"
            className="btn btn-icon btn-ghost btn-sm"
            aria-label={tab === 'video' ? `${item.title} 시청` : opensInApp ? `${item.title} 앱에서 열기` : `${item.title} 받기`}
            title={opensInApp ? '앱 안에서 열기' : actionBlockedReason ?? (tab === 'video' ? '공식 플레이어로 시청' : '이 자료만 받기')}
            disabled={opensInApp ? false : actionBlockedReason !== null || actionBusy}
            onClick={(event) => {
              event.preventDefault();
              if (opensInApp) onOpenItem(item);
              else onAction(item);
            }}
          >
            {tab === 'video' ? <PlayIcon /> : opensInApp ? <WindowIcon /> : <DownloadIcon />}
          </button>
        )}
      </span>
    </label>
  );
}

interface ListProps {
  groups: WeekGroup[];
  tab: LectureTab;
  courseId: string | number | null;
  attachments: PageAttachmentsController;
  selected: ReadonlySet<string>;
  selectableKeys: string[];
  isDownloaded: (key: string) => boolean;
  now: Date;
  actionBlockedReason: string | null;
  openingKey: string | null;
  onToggle: (key: string, checked: boolean) => void;
  onToggleMany: (keys: string[], checked: boolean) => void;
  onAction: (item: CourseContentItem) => void;
  onOpenItem: (item: CourseContentItem) => void;
}

export function LectureList({
  groups, tab, courseId, attachments, selected, selectableKeys, isDownloaded, now, actionBlockedReason, openingKey, onToggle, onToggleMany, onAction, onOpenItem,
}: ListProps) {
  const overall = triState(selectableKeys, selected);
  return (
    <div className={`lecture-table is-${tab}`} role="group" aria-label={tab === 'video' ? '영상 목록' : '자료 목록'}>
      <div className="lecture-head">
        <span className="cell-check">
          <Checkbox
            checked={overall === 'all'}
            indeterminate={overall === 'some'}
            disabled={selectableKeys.length === 0}
            label="전체 선택"
            onChange={(value) => onToggleMany(selectableKeys, value)}
          />
        </span>
        <span className="cell-title">제목</span>
        {tab === 'video' && <span className="cell-num cell-length">길이</span>}
        <span className="cell-num cell-size">크기</span>
        {tab === 'video' && <span className="cell-deadline">기한</span>}
        <span className="cell-status">상태</span>
        <span className="cell-action" />
      </div>
      {groups.map((group) => {
        const keys = group.items.filter(isSelectable).map((item) => itemKey(item) as string);
        const state = triState(keys, selected);
        const single = group.items.length === 1;
        return (
          <section key={group.key} className="week" aria-label={group.title}>
            {!single && <label className="week-head">
              <span className="cell-check">
                <Checkbox
                  checked={state === 'all'}
                  indeterminate={state === 'some'}
                  disabled={keys.length === 0}
                  label={`${group.title} 전체 선택`}
                  onChange={(value) => onToggleMany(keys, value)}
                />
              </span>
              <span className="week-title" title={group.title}>{group.title}</span>
              <span className="week-count">{group.items.length}</span>
            </label>}
            {group.items.map((item, index) => {
              const key = itemKey(item);
              const attachmentsOpen = key !== null && attachments.expanded.has(key);
              return (
                <Fragment key={key ?? `${group.key}-${index}`}>
                  <LectureRow
                    item={item}
                    tab={tab}
                    checked={key !== null && selected.has(key)}
                    downloaded={key !== null && isDownloaded(key)}
                    now={now}
                    actionBlockedReason={actionBlockedReason}
                    actionBusy={key !== null && openingKey === key}
                    weekLabel={single ? group.title : undefined}
                    openUrl={itemPageUrl(item, courseId)}
                    attachmentsOpen={attachmentsOpen}
                    onToggle={onToggle}
                    onAction={onAction}
                    onOpenItem={onOpenItem}
                    onToggleAttachments={attachments.toggle}
                  />
                  {attachmentsOpen && key !== null && (
                    <PageAttachmentRows
                      title={item.title}
                      attachments={attachments.pages[key]}
                      onRetry={() => attachments.retry(item)}
                      onSave={(file: PageFile) => void attachments.save(item, file)}
                    />
                  )}
                </Fragment>
              );
            })}
          </section>
        );
      })}
    </div>
  );
}
