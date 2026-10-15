import { useCallback, useEffect, useMemo, useState } from 'react';
import { ActionBar } from './components/ActionBar';
import { CourseSidebar } from './components/CourseSidebar';
import { DownloadDrawer } from './components/DownloadDrawer';
import { LecturePane } from './components/LecturePane';
import { BackendScreen, LoginScreen, SplashScreen } from './components/Screens';
import { SettingsDialog } from './components/SettingsDialog';
import { Toasts } from './components/Toasts';
import { TopBar } from './components/TopBar';
import { useBackendStatus } from './hooks/useBackendStatus';
import { useCourseContent } from './hooks/useCourseContent';
import { useDownloadDir } from './hooks/useDownloadDir';
import { useDownloads } from './hooks/useDownloads';
import { useLmsSession } from './hooks/useLmsSession';
import type { LmsSession } from './hooks/useLmsSession';
import { useMediaDependencies } from './hooks/useMediaDependencies';
import { useOfficialViewer } from './hooks/useOfficialViewer';
import { useToasts } from './hooks/useToasts';
import { groupByWeek, isSelectable, itemKey, sumSizes } from './lib/content';
import type { LectureTab } from './lib/content';
import { isTauriRuntime, openDownloadedFolder } from './lib/desktop';
import { friendlyError } from './lib/errors';
import { formatBytes, shortFolderName } from './lib/format';
import type { JobPlan, PlannedItem } from './lib/jobItems';
import type { CourseContentItem, CourseSummary, DownloadOutputFormat } from './lib/types';

const FORMAT_ORDER: DownloadOutputFormat[] = ['mp4', 'mp3', 'txt'];

function toPlannedItem(item: CourseContentItem): PlannedItem {
  return { id: itemKey(item) as string, rawId: item.module_item_id as string | number, title: item.title, sizeBytes: item.size_bytes };
}

function Workspace({ session }: { session: LmsSession }) {
  const { toasts, push, dismiss } = useToasts();
  const { courses, expire } = session;
  const [selectedCourseId, setSelectedCourseId] = useState<string | null>(null);
  const [tab, setTab] = useState<LectureTab>('video');
  const [unwatchedOnly, setUnwatchedOnly] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [formats, setFormats] = useState<DownloadOutputFormat[]>(['mp4']);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const onAuthError = useCallback((message: string) => { void expire(message); }, [expire]);
  const onError = useCallback((message: string) => push(message, 'error'), [push]);

  // Keep a valid course selected: the first one after (re)loading the list.
  useEffect(() => {
    setSelectedCourseId((current) => {
      if (courses.length === 0) return null;
      return current !== null && courses.some((course) => String(course.id) === current) ? current : String(courses[0].id);
    });
  }, [courses]);
  const course = useMemo(() => courses.find((candidate) => String(candidate.id) === selectedCourseId) ?? null, [courses, selectedCourseId]);

  const { dir, pick } = useDownloadDir();
  const content = useCourseContent(course, dir, onAuthError);
  const downloads = useDownloads({ downloadDir: dir, onAuthError, onError });
  const media = useMediaDependencies({ onMessage: push, downloadBusy: downloads.active });
  const viewer = useOfficialViewer({ notify: push, onAuthError });

  useEffect(() => {
    setSelected(new Set());
  }, [selectedCourseId]);

  const videos = useMemo(() => content.items.filter((item) => item.kind === 'video'), [content.items]);
  const materials = useMemo(() => content.items.filter((item) => item.kind === 'material'), [content.items]);
  const visibleItems = useMemo(() => {
    if (tab === 'material') return materials;
    return unwatchedOnly ? videos.filter((item) => item.completed !== true) : videos;
  }, [materials, tab, unwatchedOnly, videos]);
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
      kind: tab,
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
  const selectCourse = (next: CourseSummary) => setSelectedCourseId(String(next.id));

  return (
    <div className="app">
      <TopBar
        terms={session.terms}
        termId={session.termId}
        listState={session.listState}
        busy={busyElsewhere}
        onTermChange={session.changeTerm}
        onRefresh={() => { session.refresh(); content.reload(true); }}
        onOpenSettings={() => setSettingsOpen(true)}
      />
      <div className="workspace">
        <CourseSidebar
          courses={courses}
          selectedId={selectedCourseId}
          listState={session.listState}
          listError={session.listError}
          onSelect={selectCourse}
          onRetry={session.refresh}
        />
        <main className="main">
          <div className="main-body">
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
              selected={selected}
              selectableKeys={selectableKeys}
              queue={viewer.queue}
              openingKey={viewer.openingKey}
              actionBlockedReason={actionBlockedReason}
              isDownloaded={(key) => course !== null && (downloads.isDownloaded(course.id, key) || savedKeys.has(key))}
              onTabChange={setTab}
              onUnwatchedOnlyChange={setUnwatchedOnly}
              onToggle={(key, checked) => setChecked([key], checked)}
              onToggleMany={setChecked}
              onAction={handleRowAction}
              onStartWatch={() => { if (course) void viewer.startQueue(course, visibleItems); }}
              onStopWatch={viewer.stop}
              onReload={content.reload}
            />
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
          <ActionBar
            tab={tab}
            selectedCount={selectedItems.length}
            sizeText={sizeText}
            formats={formats}
            formatsLocked={downloads.active}
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
          />
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
