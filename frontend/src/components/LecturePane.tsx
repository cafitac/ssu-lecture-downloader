import type { ContentState } from '../hooks/useCourseContent';
import type { PageAttachmentsController } from '../hooks/usePageAttachments';
import { countVideos, splitCourseName } from '../lib/content';
import { TIDY_COPY } from '../lib/tidy';
import type { LectureTab, WeekGroup } from '../lib/content';
import type { CourseContentItem, CourseSummary, WatchMode, WatchQueueState } from '../lib/types';
import { LectureList } from './LectureList';
import { LectureToolbar } from './LectureToolbar';
import { Spinner } from './Screens';
import { TaskList } from './TaskList';

interface Props {
  course: CourseSummary | null;
  allItems: CourseContentItem[];
  contentState: ContentState;
  contentError: string | null;
  tab: LectureTab;
  unwatchedOnly: boolean;
  groups: WeekGroup[];
  videoCount: number;
  materialCount: number;
  /** Assignments and quizzes in the order to show them. */
  tasks: CourseContentItem[];
  now: Date;
  selected: ReadonlySet<string>;
  selectableKeys: string[];
  /** Videos "순서대로 시청" would play now. */
  watchCount: number;
  /** True when the count is the checked videos rather than the automatic queue. */
  watchSelected: boolean;
  attachments: PageAttachmentsController;
  /** Files of this course saved the old way, and the notice's two answers; `null` hides the notice. */
  legacyNotice: { count: number; blocked: boolean; onTidy: () => void; onLater: () => void } | null;
  queue: WatchQueueState;
  watchMode: WatchMode;
  openingKey: string | null;
  actionBlockedReason: string | null;
  isDownloaded: (key: string) => boolean;
  onTabChange: (tab: LectureTab) => void;
  onUnwatchedOnlyChange: (value: boolean) => void;
  onToggle: (key: string, checked: boolean) => void;
  onToggleMany: (keys: string[], checked: boolean) => void;
  onAction: (item: CourseContentItem) => void;
  onOpenItem: (item: CourseContentItem) => void;
  onOpenTask: (item: CourseContentItem) => void;
  onOpenTaskInBrowser: (item: CourseContentItem) => void;
  onStartWatch: () => void;
  onReload: () => void;
}

function emptyText(tab: LectureTab, unwatchedOnly: boolean): string {
  if (tab === 'material') return '이 과목에는 자료나 페이지가 없어요.';
  if (tab === 'task') return '이 과목에는 과제나 퀴즈가 없어요.';
  return unwatchedOnly ? '안 본 영상이 없어요. 모두 시청했어요.' : '이 과목에는 영상이 없어요.';
}

export function LecturePane(props: Props) {
  const { course, allItems, contentState, contentError, tab, unwatchedOnly, groups, selected, selectableKeys } = props;
  if (!course) {
    return <section className="lecture-pane"><p className="pane-empty">왼쪽에서 과목을 선택해 주세요.</p></section>;
  }
  const { total, watched, upcoming } = countVideos(allItems, props.now);
  const hasRows = tab === 'task' ? props.tasks.length > 0 : groups.some((group) => group.items.length > 0);
  return (
    <section className="lecture-pane" aria-busy={contentState === 'loading'}>
      <header className="pane-head">
        <h1 className="pane-title" title={course.name}>{splitCourseName(course.name).title}</h1>
        {contentState === 'ready' && <p className="pane-sub">영상 {total} · 시청 {watched}{upcoming > 0 && <span className="pane-sub-upcoming"> · 예정 {upcoming}</span>}</p>}
        {props.legacyNotice && contentState === 'ready' && (
          <div className="legacy-notice" role="status">
            <p>{TIDY_COPY.notice(props.legacyNotice.count)}</p>
            <div className="legacy-notice-actions">
              <button type="button" className="btn btn-sm" disabled={props.legacyNotice.blocked} title={props.legacyNotice.blocked ? TIDY_COPY.busyHint : undefined} onClick={props.legacyNotice.onTidy}>정리하기</button>
              <button type="button" className="btn btn-sm btn-ghost" onClick={props.legacyNotice.onLater}>나중에</button>
            </div>
          </div>
        )}
      </header>
      <LectureToolbar
        tab={tab}
        videoCount={props.videoCount}
        materialCount={props.materialCount}
        taskCount={props.tasks.length}
        unwatchedOnly={unwatchedOnly}
        queue={props.queue}
        watchMode={props.watchMode}
        watchCount={props.watchCount}
        watchSelected={props.watchSelected}
        watchDisabled={contentState !== 'ready' || props.watchCount === 0}
        onTabChange={props.onTabChange}
        onUnwatchedOnlyChange={props.onUnwatchedOnlyChange}
        onStartWatch={props.onStartWatch}
      />
      <div className="lecture-scroll">
        {contentState === 'loading' && <p className="pane-empty" role="status"><Spinner />강의를 불러오는 중…</p>}
        {contentState === 'error' && (
          <div className="pane-empty" role="alert">
            <p>{contentError}</p>
            <button type="button" className="btn btn-sm" onClick={props.onReload}>다시 시도</button>
          </div>
        )}
        {contentState === 'ready' && !hasRows && <p className="pane-empty">{emptyText(tab, unwatchedOnly)}</p>}
        {contentState === 'ready' && hasRows && tab === 'task' && <TaskList tasks={props.tasks} courseId={course.id} now={props.now} onOpen={props.onOpenTask} onOpenInBrowser={props.onOpenTaskInBrowser} />}
        {contentState === 'ready' && hasRows && tab !== 'task' && (
          <LectureList
            groups={groups}
            tab={tab}
            courseId={course.id}
            attachments={props.attachments}
            selected={selected}
            selectableKeys={selectableKeys}
            isDownloaded={props.isDownloaded}
            now={props.now}
            actionBlockedReason={props.actionBlockedReason}
            openingKey={props.openingKey}
            onToggle={props.onToggle}
            onToggleMany={props.onToggleMany}
            onAction={props.onAction}
            onOpenItem={props.onOpenItem}
          />
        )}
      </div>
    </section>
  );
}
