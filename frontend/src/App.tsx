import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActionBar } from './components/ActionBar';
import { CourseSidebar } from './components/CourseSidebar';
import { DeadlinesPane } from './components/DeadlinesPane';
import { DownloadDrawer } from './components/DownloadDrawer';
import { LecturePane } from './components/LecturePane';
import { BackendScreen, LoginScreen, SplashScreen } from './components/Screens';
import { SettingsDialog } from './components/SettingsDialog';
import { Toasts } from './components/Toasts';
import { TopBar } from './components/TopBar';
import { WatchStrip } from './components/WatchStrip';
import { useBackendStatus } from './hooks/useBackendStatus';
import { useCourseContent } from './hooks/useCourseContent';
import { useDeadlines } from './hooks/useDeadlines';
import { useDownloadDir } from './hooks/useDownloadDir';
import { useDownloads } from './hooks/useDownloads';
import { useLmsPages } from './hooks/useLmsPages';
import { useLmsSession } from './hooks/useLmsSession';
import type { LmsSession } from './hooks/useLmsSession';
import { useMediaDependencies } from './hooks/useMediaDependencies';
import { useNow } from './hooks/useNow';
import { useOfficialViewer } from './hooks/useOfficialViewer';
import { usePageAttachments } from './hooks/usePageAttachments';
import { useToasts } from './hooks/useToasts';
import { buildWatchQueue, groupByWeek, isLibraryItem, isSelectable, isTaskItem, isUnwatchedNow, itemKey, itemPageUrl, pickDefaultCourse, sortTasks, sumSizes } from './lib/content';
import type { LectureTab } from './lib/content';
import { dueSoonCount } from './lib/deadline';
import { isTauriRuntime, openDownloadedFolder } from './lib/desktop';
import { friendlyError } from './lib/errors';
import { formatBytes, shortFolderName } from './lib/format';
import type { JobPlan, PlannedItem } from './lib/jobItems';
import type { CourseContentItem, CourseSummary, DeadlineEntryDto, DownloadOutputFormat } from './lib/types';

const FORMAT_ORDER: DownloadOutputFormat[] = ['mp4', 'mp3', 'txt'];
/** The LMS needs a moment to count a lecture that just ended before a reload shows it as watched. */
const QUEUE_REFRESH_DELAY_MS = 20_000;

function toPlannedItem(item: CourseContentItem): PlannedItem {
  return { id: itemKey(item) as string, rawId: item.module_item_id as string | number, title: item.title, sizeBytes: item.size_bytes };
}

function Workspace({ session }: { session: LmsSession }) {
  const { toasts, push, dismiss } = useToasts();
  const { courses, expire } = session;
  const now = useNow();
  const [view, setView] = useState<'course' | 'deadlines'>('course');
  const [selectedCourseId, setSelectedCourseId] = useState<string | null>(null);
  const [tab, setTab] = useState<LectureTab>('video');
  const [unwatchedOnly, setUnwatchedOnly] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [formats, setFormats] = useState<DownloadOutputFormat[]>(['mp4']);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const onAuthError = useCallback((message: string) => { void expire(message); }, [expire]);
  const onError = useCallback((message: string) => push(message, 'error'), [push]);

  // Keep a valid course selected; after (re)loading, start where there is something left to watch.
  useEffect(() => {
    setSelectedCourseId((current) => {
      if (courses.length === 0) return null;
      if (current !== null && courses.some((course) => String(course.id) === current)) return current;
      const preferred = pickDefaultCourse(courses) ?? courses[0];
      return String(preferred.id);
    });
  }, [courses]);
  const course = useMemo(() => courses.find((candidate) => String(candidate.id) === selectedCourseId) ?? null, [courses, selectedCourseId]);

  const { dir, pick } = useDownloadDir();
  const content = useCourseContent(course, dir, onAuthError);
  const deadlines = useDeadlines(session.termId, courses, onAuthError);
  const downloads = useDownloads({ downloadDir: dir, onAuthError, onError });
  const media = useMediaDependencies({ onMessage: push, downloadBusy: downloads.active });
  const lmsPages = useLmsPages({ notify: push, onAuthError });
  const attachments = usePageAttachments(course, dir, { onAuthError, notify: push });

  // A lecture that played to its end counts as watched at once; the LMS is asked a little later.
  const refreshTimers = useRef(new Set<ReturnType<typeof setTimeout>>());
  useEffect(() => {
    const timers = refreshTimers.current;
    return () => {
      timers.forEach((timer) => clearTimeout(timer));
      timers.clear();
    };
  }, []);
  const onQueueSettled = useCallback((finished: CourseSummary) => {
    const timer = setTimeout(() => {
      refreshTimers.current.delete(timer);
      void content.refreshQuietly(finished);
    }, QUEUE_REFRESH_DELAY_MS);
    refreshTimers.current.add(timer);
  }, [content]);
  const viewer = useOfficialViewer({ notify: push, onAuthError, onItemWatched: content.markWatched, onQueueSettled });

  useEffect(() => {
    setSelected(new Set());
  }, [selectedCourseId]);

  const videos = useMemo(() => content.items.filter((item) => item.kind === 'video'), [content.items]);
  const materials = useMemo(() => content.items.filter(isLibraryItem), [content.items]);
  const watchCount = useMemo(() => buildWatchQueue(content.items, now).length, [content.items, now]);
  const tasks = useMemo(() => sortTasks(content.items.filter(isTaskItem), now), [content.items, now]);
  const visibleItems = useMemo(() => {
    if (tab === 'task') return [];
    if (tab === 'material') return materials;
    return unwatchedOnly ? videos.filter((item) => isUnwatchedNow(item, now)) : videos;
  }, [materials, now, tab, unwatchedOnly, videos]);
  const groups = useMemo(() => groupByWeek(visibleItems), [visibleItems]);
  const selectableKeys = useMemo(() => visibleItems.filter(isSelectable).map((item) => itemKey(item) as string), [visibleItems]);
  const selectedItems = useMemo(
    () => visibleItems.filter((item) => isSelectable(item) && selected.has(itemKey(item) as string)),
    [selected, visibleItems],
  );

  // Files already in the download folder, as reported by the backend.
  const savedKeys = useMemo(
    () => new Set(content.items.filter((item) => item.downloaded_path).map((item) => itemKey(item) as string)),
    [content.items],
  );
    const sizes = sumSizes(selectedItems);
  const sizeText = sizes.bytes > 0 ? (sizes.unknown > 0 ? `${formatBytes(sizes.bytes)} 이상` : formatBytes(sizes.bytes)) : null;
  const missing = tab === 'video' ? media.missingFor(formats) : [];
  const busyElsewhere = media.isInstalling;
  const canDownload = selectedItems.length > 0 && !downloads.active && missing.length === 0 && !media.isInstalling;

  const setChecked = useCallback((keys: string[], checked: boolean) => {
    setSelected((current) => {
      const next = new Set(current);
      for (const key of keys) {
        if (checked) next.add(key);
        else next.delete(key);
      }
      return next;
    });
  }, []);

  const toggleFormat = useCallback((format: DownloadOutputFormat) => {
    setFormats((current) => {
      if (current.includes(format)) return current.length === 1 ? current : current.filter((candidate) => candidate !== format);
      return FORMAT_ORDER.filter((candidate) => candidate === format || current.includes(candidate));
    });
  }, []);

  async function beginDownload(items: CourseContentItem[]) {
    if (!course || items.length === 0 || downloads.active) return;
    const plan: JobPlan = {
      courseId: course.id,
      courseName: course.name,
      kind: tab === 'material' ? 'material' : 'video',
      outputs: tab === 'video' ? formats : ['mp4'],
      items: items.map(toPlannedItem),
    };
    setDrawerOpen(true);
    const started = await downloads.start(plan);
    if (started) setSelected(new Set());
    else setDrawerOpen(false);
  }

  function handleRowAction(item: CourseContentItem) {
    if (!course) return;
    if (tab === 'video') void viewer.open(course, item, itemKey(item) as string);
    else void beginDownload([item]);
  }

  /** Pages, boards and other LMS items open in an app window (with the LMS session). */
  function openItemInApp(item: CourseContentItem) {
    if (!course) return;
    void lmsPages.openInApp(item.title, itemPageUrl(item, course.id));
  }

  function openTaskInBrowser(item: CourseContentItem) {
    if (!course) return;
    void lmsPages.openInBrowser(itemPageUrl(item, course.id));
  }

  function deadlineAddress(entry: DeadlineEntryDto): string | null {
    return itemPageUrl({ url: entry.url, module_item_id: entry.module_item_id }, entry.course_id);
  }

  function selectDeadline(entry: DeadlineEntryDto) {
    if (entry.kind === 'video') {
      setSelectedCourseId(String(entry.course_id));
      setTab('video');
      setView('course');
      return;
    }
    void lmsPages.openInApp(entry.title, deadlineAddress(entry));
  }

  async function openFolder(path?: string) {
    try {
      await openDownloadedFolder(path ?? dir);
    } catch (error) {
      push(friendlyError(error, '폴더를 열지 못했어요.'), 'error');
    }
  }

  async function chooseFolder() {
    try {
      await pick();
    } catch (error) {
      push(friendlyError(error, '폴더를 선택하지 못했어요.'), 'error');
    }
  }

  // Escape closes the progress drawer unless a dialog (which handles Escape itself) is open.
  useEffect(() => {
    if (!drawerOpen || settingsOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDrawerOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [drawerOpen, settingsOpen]);

  // Watching works during a download; a material download still waits for the running job.
  const actionBlockedReason = downloads.active && tab !== 'video'
    ? '다른 다운로드가 끝나면 받을 수 있어요'
    : viewer.queue.active ? '순서대로 시청 중이에요' : null;
  const installBlockedReason = !media.autoInstallAvailable ? media.autoInstallUnavailableReason ?? '이 환경에서는 자동으로 설치할 수 없어요' : downloads.active ? '다운로드가 끝나면 설치할 수 있어요' : null;
  const selectCourse = (next: CourseSummary) => {
    setSelectedCourseId(String(next.id));
    setView('course');
  };
  const showActionBar = (view === 'course' && tab !== 'task') || downloads.active || downloads.startError !== null;

  return (
    <div className="app">
      <TopBar
        terms={session.terms}
        termId={session.termId}
        listState={session.listState}
        busy={busyElsewhere}
        dueSoonCount={dueSoonCount(deadlines.items, now)}
        deadlinesOpen={view === 'deadlines'}
        onToggleDeadlines={() => setView((current) => (current === 'deadlines' ? 'course' : 'deadlines'))}
        onTermChange={session.changeTerm}
        onRefresh={() => { session.refresh(); content.reload(true); }}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <WatchStrip queue={viewer.queue} onNext={() => void viewer.next()} onStop={() => void viewer.stop()} onShowWindow={() => void viewer.showWindow()} />
      <div className="workspace">
        <CourseSidebar
          courses={courses}
          selectedId={view === 'course' ? selectedCourseId : null}
          listState={session.listState}
          listError={session.listError}
          now={now}
          onSelect={selectCourse}
          onRetry={session.refresh}
        />
        <main className="main">
          <div className="main-body">
            {view === 'deadlines' ? (
              <DeadlinesPane
                items={deadlines.items}
                state={deadlines.state}
                error={deadlines.error}
                now={now}
                onSelect={selectDeadline}
                onOpenInBrowser={(entry) => void lmsPages.openInBrowser(deadlineAddress(entry))}
                onReload={deadlines.reload}
              />
            ) : (
            <LecturePane
              course={course}
              allItems={content.items}
              contentState={content.state}
              contentError={content.error}
              tab={tab}
              unwatchedOnly={unwatchedOnly}
              groups={groups}
              videoCount={videos.length}
              materialCount={materials.length}
              tasks={tasks}
              now={now}
              selected={selected}
              selectableKeys={selectableKeys}
              watchCount={watchCount}
              attachments={attachments}
              queue={viewer.queue}
              openingKey={viewer.openingKey}
              actionBlockedReason={actionBlockedReason}
              isDownloaded={(key) => course !== null && (downloads.isDownloaded(course.id, key) || savedKeys.has(key))}
              onTabChange={setTab}
              onUnwatchedOnlyChange={setUnwatchedOnly}
              onToggle={(key, checked) => setChecked([key], checked)}
              onToggleMany={setChecked}
              onAction={handleRowAction}
              onOpenItem={openItemInApp}
              onOpenTask={openItemInApp}
              onOpenTaskInBrowser={openTaskInBrowser}
              onStartWatch={() => { if (course) void viewer.startQueue(course, buildWatchQueue(content.items, new Date())); }}
              onReload={content.reload}
            />
            )}
            {drawerOpen && downloads.plan && (
              <DownloadDrawer
                rows={downloads.rows}
                summary={downloads.summary}
                starting={downloads.starting}
                active={downloads.active}
                cancelRequested={downloads.cancelRequested}
                canOpenFolder={isTauriRuntime()}
                onClose={() => setDrawerOpen(false)}
                onCancel={() => void downloads.cancel()}
                onRetry={(ids) => void downloads.retry(ids)}
                onOpenFolder={(path) => void openFolder(path)}
              />
            )}
          </div>
          {showActionBar && <ActionBar
            tab={tab}
            selectedCount={selectedItems.length}
            sizeText={sizeText}
            formats={formats}
            formatsLocked={downloads.active}
            unsupportedFormats={media.subtitlesUnsupportedReason ? { txt: media.subtitlesUnsupportedReason } : {}}
            onToggleFormat={toggleFormat}
            missingComponents={missing}
            installing={media.isInstalling}
            canInstall={media.canInstall}
            installBlockedReason={installBlockedReason}
            onInstall={() => void media.install(missing)}
            folderName={shortFolderName(dir)}
            folderPath={dir}
            onOpenSettings={() => setSettingsOpen(true)}
            progressLabel={downloads.plan ? (downloads.active ? `${downloads.summary.done}/${downloads.summary.total}` : '') : null}
            drawerOpen={drawerOpen}
            onToggleDrawer={() => setDrawerOpen((open) => !open)}
            starting={downloads.starting}
            jobActive={downloads.active && !downloads.starting}
            canDownload={canDownload}
            onDownload={() => void beginDownload(selectedItems)}
            startError={downloads.startError}
            onDismissError={downloads.dismissStartError}
          />}
        </main>
      </div>
      <Toasts toasts={toasts} dismiss={dismiss} />
      {settingsOpen && (
        <SettingsDialog
          downloadDir={dir}
          busy={downloads.active}
          media={media}
          debugEvents={viewer.debugEvents}
          onPickFolder={() => void chooseFolder()}
          onOpenFolder={() => void openFolder()}
          onSignOut={async () => {
            const error = await session.signOut();
            if (!error) setSettingsOpen(false);
            return error;
          }}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}

function SessionGate() {
  const session = useLmsSession();
  if (session.phase === 'checking') return <SplashScreen text="로그인 상태를 확인하는 중이에요…" />;
  if (session.phase === 'signed-out') return <LoginScreen notice={session.notice} busy={session.signingIn} onSubmit={(username, password) => void session.signIn(username, password)} />;
  return <Workspace session={session} />;
}

/** Everything below talks to the API, so it renders only once the backend is ready. */
export function App() {
  const { status, retry } = useBackendStatus();
  if (status.state !== 'ready') return <BackendScreen status={status} onRetry={() => void retry()} />;
  return <SessionGate />;
}
