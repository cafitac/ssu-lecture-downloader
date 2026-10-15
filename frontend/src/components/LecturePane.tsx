import type { ContentState } from '../hooks/useCourseContent';
import { countVideos, splitCourseName } from '../lib/content';
import type { LectureTab, WeekGroup } from '../lib/content';
import type { CourseContentItem, CourseSummary, WatchQueueState } from '../lib/types';
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
  queue: WatchQueueState;
  openingKey: string | null;
  actionBlockedReason: string | null;
  isDownloaded: (key: string) => boolean;
  onTabChange: (tab: LectureTab) => void;
  onUnwatchedOnlyChange: (value: boolean) => void;
  onToggle: (key: string, checked: boolean) => void;
  onToggleMany: (keys: string[], checked: boolean) => void;
  onAction: (item: CourseContentItem) => void;
  onOpenTask: (item: CourseContentItem) => void;
  onStartWatch: () => void;
  onStopWatch: () => void;
  onReload: () => void;
}

function emptyText(tab: LectureTab, unwatchedOnly: boolean): string {
  if (tab === 'material') return '받을 수 있는 자료가 없어요.';
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
      </header>
      <LectureToolbar
        tab={tab}
        videoCount={props.videoCount}
        materialCount={props.materialCount}
        taskCount={props.tasks.length}
        unwatchedOnly={unwatchedOnly}
        queue={props.queue}
        watchDisabled={contentState !== 'ready' || !hasRows || tab !== 'video'}
        onTabChange={props.onTabChange}
        onUnwatchedOnlyChange={props.onUnwatchedOnlyChange}
        onStartWatch={props.onStartWatch}
        onStopWatch={props.onStopWatch}
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
        {contentState === 'ready' && hasRows && tab === 'task' && <TaskList tasks={props.tasks} now={props.now} onOpen={props.onOpenTask} />}
        {contentState === 'ready' && hasRows && tab !== 'task' && (
          <LectureList
            groups={groups}
            tab={tab}
            selected={selected}
            selectableKeys={selectableKeys}
            isDownloaded={props.isDownloaded}
            now={props.now}
            actionBlockedReason={props.actionBlockedReason}
            openingKey={props.openingKey}
            onToggle={props.onToggle}
            onToggleMany={props.onToggleMany}
            onAction={props.onAction}
          />
        )}
      </div>
    </section>
  );
}
