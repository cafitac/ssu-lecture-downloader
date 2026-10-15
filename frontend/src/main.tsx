import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ApiError, cancelDownloadJob, deleteCredentials, fetchCourseDetail, fetchCourses, fetchCredentialStatus, fetchDownloadJob, fetchTerms, isAuthenticationError, planOfficialViewer, planOfficialViewerSessionBridge, saveCredentials, startArtifactDownloadJob, startBatchDownloadJob, startDownloadJob } from './lib/api';
import { defaultDownloadDirectory, isTauriRuntime, openAssistedOfficialViewer, openDownloadedFolder, openOfficialViewer, pickDownloadDirectory } from './lib/desktop';
import { legacyLectureToContentItem } from './lib/content';
import { TERMINAL_DOWNLOAD_STATUSES, deriveDownloadView, downloadPayloadFromArtifact, downloadStageLabel, isBatchDownloadResult } from './lib/progress';
import type { BatchDownloadResult, ConflictStrategy, CourseContentItem, CourseSummary, CredentialStatus, DownloadJobStatus, DownloadOutputFormat, DownloadResult, LectureItem, TermSummary } from './lib/types';
import { BackendStatus } from './components/BackendStatus';
import { CourseList } from './components/CourseList';
import { DownloadPanel } from './components/DownloadPanel';
import { LecturePanel } from './components/LecturePanel';
import { LoginPanel } from './components/LoginPanel';
import { WatchQueueControl } from './components/WatchQueue';
import type { WatchDebugEvent, WatchQueueState } from './components/WatchQueue';
import { useBackendStatus } from './hooks/useBackendStatus';
import { useMediaDependencies } from './hooks/useMediaDependencies';
import { usePolledJob } from './hooks/usePolledJob';
import { usePostprocessJobs } from './hooks/usePostprocessJobs';
import { useTauriEvent } from './hooks/useTauriEvent';
import './styles.css';

type LoadState = 'idle' | 'loading' | 'ready' | 'error';

const DEFAULT_DOWNLOAD_DIR = '~/Downloads';

function App({ backendMode }: { backendMode: string }) {
  const credentialFormRef = useRef<HTMLFormElement | null>(null);
  const [credentialStatus, setCredentialStatus] = useState<CredentialStatus | null>(null);
  const [courses, setCourses] = useState<CourseSummary[]>([]);
  const [terms, setTerms] = useState<TermSummary[]>([]);
  const [selectedTermId, setSelectedTermId] = useState<number | null>(null);
  const [selectedCourse, setSelectedCourse] = useState<CourseSummary | null>(null);
  const [lectures, setLectures] = useState<LectureItem[]>([]);
  const [contentItems, setContentItems] = useState<CourseContentItem[]>([]);
  const [selectedLecture, setSelectedLecture] = useState<CourseContentItem | null>(null);
  const [contentKindFilter, setContentKindFilter] = useState('downloadable');
  const [weekFilter, setWeekFilter] = useState('all');
  const [completionFilter, setCompletionFilter] = useState('all');
  const [selectedLectureIds, setSelectedLectureIds] = useState<Array<string | number>>([]);
  const [selectedOutputFormats, setSelectedOutputFormats] = useState<DownloadOutputFormat[]>(['mp4']);
  const [downloadResult, setDownloadResult] = useState<DownloadResult | null>(null);
  const [downloadJob, setDownloadJob] = useState<DownloadJobStatus | null>(null);
  const [downloadStartError, setDownloadStartError] = useState<string | null>(null);
  const [downloadDiagnostics, setDownloadDiagnostics] = useState<string[]>([]);
  const [watchQueueState, setWatchQueueState] = useState<WatchQueueState>({ active: false, index: 0, total: 0 });
  const watchQueueRef = useRef<CourseContentItem[]>([]);
  const watchQueueIndexRef = useRef(0);
  const watchQueueActiveRef = useRef(false);
  const watchQueueOpeningRef = useRef(false);
  const selectedCourseRef = useRef<CourseSummary | null>(null);
  const [batchResult, setBatchResult] = useState<BatchDownloadResult | null>(null);
  const [state, setState] = useState<LoadState>('idle');
  const [downloadDir, setDownloadDir] = useState(DEFAULT_DOWNLOAD_DIR);
  const [message, setMessage] = useState('숭실 LMS 계정으로 로그인하면 강의 목록을 불러옵니다.');
  const [isAuthenticating, setIsAuthenticating] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [isSessionVerified, setIsSessionVerified] = useState(false);
  const [isCourseDetailLoading, setIsCourseDetailLoading] = useState(false);
  const [isStartingDownload, setIsStartingDownload] = useState(false);
  const [isOpeningViewer, setIsOpeningViewer] = useState(false);
  const [isOpeningFolder, setIsOpeningFolder] = useState(false);
  const [watchDebugEvents, setWatchDebugEvents] = useState<WatchDebugEvent[]>([]);

  const isDownloadActive = Boolean(downloadJob && !TERMINAL_DOWNLOAD_STATUSES.has(downloadJob.status));
  const downloadView = deriveDownloadView({ downloadJob, batchResult, downloadResult, isStartingDownload, downloadStartError, downloadDir });
  const { activeSingleResult } = downloadView;

  /** Auth failures send the user back to the login screen; everything else becomes a visible message. */
  async function reportError(error: unknown, fallback: string) {
    if (isAuthenticationError(error)) {
      await returnToLoginAfterAuthenticationFailure(error);
      return;
    }
    setMessage(error instanceof Error ? error.message : fallback);
  }

  const media = useMediaDependencies({ onMessage: setMessage, downloadBusy: isStartingDownload || isDownloadActive });
  const postprocess = usePostprocessJobs({
    defaultVideoPath: activeSingleResult?.written ? activeSingleResult.output_path ?? '' : '',
    onMessage: setMessage,
    onError: reportError,
  });
  // Course refresh/reload clears the active download job, so it must wait for every running job.
  const isAnyJobActive = isStartingDownload || isDownloadActive || media.isInstalling || postprocess.isActive;

  async function loadCredentialStatus(): Promise<CredentialStatus | null> {
    try {
      const status = await fetchCredentialStatus();
      setCredentialStatus(status);
      setIsSessionVerified(false);
      if (!status.username_present || !status.password_present) setIsSessionVerified(false);
      return status;
    } catch {
      setCredentialStatus(null);
      return null;
    }
  }

  async function initializeDownloadDir() {
    try {
      setDownloadDir(await defaultDownloadDirectory());
    } catch {
      setDownloadDir(DEFAULT_DOWNLOAD_DIR);
    }
  }

  function clearSessionUi() {
    setTerms([]);
    setCourses([]);
    setLectures([]);
    setContentItems([]);
    setSelectedCourse(null);
    setSelectedLecture(null);
    setSelectedLectureIds([]);
    setDownloadResult(null);
    setDownloadJob(null);
    setBatchResult(null);
    postprocess.clear();
    setIsSessionVerified(false);
    setState('idle');
  }

  async function returnToLoginAfterAuthenticationFailure(error: unknown) {
    clearSessionUi();
    try {
      const status = await deleteCredentials();
      setCredentialStatus(status);
    } catch {
      setCredentialStatus({
        ok: true,
        source: 'app',
        username_present: false,
        password_present: false,
        can_write: true,
      });
      setIsSessionVerified(false);
    }
    setState('error');
    setMessage(error instanceof Error ? error.message : '로그인에 실패했습니다.');
  }

  async function handlePickDownloadDir() {
    try {
      const selected = await pickDownloadDirectory();
      if (selected) {
        setDownloadDir(selected);
        setMessage('다운로드 폴더를 Finder에서 선택했습니다.');
      }
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '다운로드 폴더를 선택할 수 없습니다.');
    }
  }

  async function handleCredentialSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isAuthenticating) return;
    const formData = new FormData(event.currentTarget);
    const username = String(formData.get('username') ?? '');
    const password = String(formData.get('password') ?? '');
    setIsAuthenticating(true);
    setState('loading');
    setMessage('로그인하고 강의 목록을 불러오는 중입니다...');
    try {
      const status = await saveCredentials(username, password);
      setCredentialStatus(status);
      credentialFormRef.current?.reset();
      setMessage('로그인했습니다. 강의 목록을 불러오는 중입니다...');
      const currentTermId = await loadTerms();
      await loadCourses(currentTermId);
    } catch (error) {
      if (isAuthenticationError(error)) {
        await returnToLoginAfterAuthenticationFailure(error);
      } else {
        setState('error');
        setMessage(error instanceof Error ? error.message : '로그인 정보 저장 실패');
      }
    } finally {
      setIsAuthenticating(false);
    }
  }

  function clearLectureSelection() {
    setSelectedLecture(null);
    setSelectedLectureIds([]);
    setDownloadResult(null);
    setDownloadJob(null);
    setDownloadStartError(null);
    setDownloadDiagnostics([]);
    setBatchResult(null);
  }

  function appendDownloadDiagnostic(stage: string, details?: Record<string, unknown>) {
    const safeDetails = details ? Object.fromEntries(
      Object.entries(details).filter(([key]) => !/password|token|cookie|credential|url/i.test(key))
    ) : null;
    const timestamp = new Date().toISOString();
    const line = safeDetails && Object.keys(safeDetails).length > 0
      ? `${timestamp} ${stage} ${JSON.stringify(safeDetails)}`
      : `${timestamp} ${stage}`;
    setDownloadDiagnostics((current) => {
      const next = [line, ...current].slice(0, 30);
      try {
        window.localStorage.setItem('ssu-download-diagnostics', JSON.stringify(next));
      } catch {
        // Diagnostics are best-effort; never break the download UI because storage is unavailable.
      }
      return next;
    });
    console.info('[ssu-download]', line);
  }

  function handleWeekFilterChange(value: string) {
    setWeekFilter(value);
    clearLectureSelection();
  }

  function handleContentKindFilterChange(value: string) {
    setContentKindFilter(value);
    clearLectureSelection();
  }

  function handleCompletionFilterChange(value: string) {
    setCompletionFilter(value);
    clearLectureSelection();
  }

  async function handleCredentialDelete() {
    if (isLoggingOut) return;
    setIsLoggingOut(true);
    try {
      const status = await deleteCredentials();
      setCredentialStatus(status);
      clearSessionUi();
      setMessage('저장된 로그인 정보를 삭제했습니다.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '로그인 정보 삭제 실패');
    } finally {
      setIsLoggingOut(false);
    }
  }

  async function loadCourses(termId: number | null = selectedTermId) {
    setState('loading');
    setMessage(termId === null ? '현재 학기 과목 조회 중...' : `선택한 학기 ${termId} 과목 조회 중...`);
    setDownloadResult(null);
    setDownloadJob(null);
    setBatchResult(null);
    postprocess.clear();
    try {
      const response = await fetchCourses(termId);
      setCourses(response.courses);
      setSelectedCourse(response.courses[0] ?? null);
      setIsSessionVerified(true);
      setState('ready');
      setMessage(termId === null ? `현재 학기 과목 ${response.course_count}개를 불러왔습니다.` : `선택한 학기 ${termId} 과목 ${response.course_count}개를 불러왔습니다.`);
    } catch (error) {
      if (isAuthenticationError(error)) {
        await returnToLoginAfterAuthenticationFailure(error);
        return;
      }
      setState('error');
      setMessage(error instanceof Error ? error.message : '과목 조회 실패');
    }
  }

  async function loadTerms(): Promise<number | null> {
    try {
      const response = await fetchTerms();
      setTerms(response.terms);
      const current = response.terms.find((term) => term.is_current);
      const currentTermId = current?.term_id ?? null;
      setSelectedTermId(currentTermId);
      return currentTermId;
    } catch (error) {
      if (isAuthenticationError(error)) {
        await returnToLoginAfterAuthenticationFailure(error);
        throw error;
      }
      setTerms([]);
      return null;
    }
  }

  async function handleLoadSelectedTerm() {
    if (state === 'loading' || isCourseDetailLoading || isAnyJobActive) return;
    await loadCourses(selectedTermId);
  }

  async function handleRefreshCourses() {
    if (state === 'loading' || isCourseDetailLoading || isAnyJobActive) return;
    await loadCourses(selectedTermId);
  }

  async function selectCourse(course: CourseSummary) {
    if (state === 'loading' || isCourseDetailLoading) return;
    setIsCourseDetailLoading(true);
    setSelectedCourse(course);
    setSelectedLecture(null);
    setSelectedLectureIds([]);
    setDownloadResult(null);
    setDownloadJob(null);
    setBatchResult(null);
    setMessage(`${course.name} 강의 목록 조회 중...`);
    try {
      const detail = await fetchCourseDetail(course.id, course.name);
      const normalizedContentItems = detail.content_items?.length ? detail.content_items : detail.lectures.map(legacyLectureToContentItem);
      setLectures(detail.lectures);
      setContentItems(normalizedContentItems);
      const firstDownloadableVideo = normalizedContentItems.find((item) => item.downloadable && item.kind === 'video' && item.module_item_id !== null) ?? null;
      const firstDownloadable = firstDownloadableVideo ?? normalizedContentItems.find((item) => item.downloadable && item.module_item_id !== null) ?? null;
      setSelectedLecture(firstDownloadable ?? normalizedContentItems[0] ?? null);
      setSelectedLectureIds([]);
      setMessage(firstDownloadableVideo ? `${course.name} 콘텐츠 ${normalizedContentItems.length}개를 불러왔습니다. 다운로드할 영상을 직접 선택하세요.` : firstDownloadable ? `${course.name} 콘텐츠 ${normalizedContentItems.length}개를 불러왔습니다. 다운로드 가능한 자료가 있지만 영상 항목이 없습니다.` : `${course.name} 콘텐츠 ${normalizedContentItems.length}개를 불러왔습니다. 다운로드할 항목을 선택하세요.`);
    } catch (error) {
      await reportError(error, '강의 목록 조회 실패');
    } finally {
      setIsCourseDetailLoading(false);
    }
  }

  function resolveConflictStrategy(selectedIds: Array<string | number>): ConflictStrategy {
    appendDownloadDiagnostic('conflict_check.skipped_default_skip', { selected_count: selectedIds.length });
    return 'skip';
  }

  async function handleDownloadJobStart() {
    if (isStartingDownload || isDownloadActive) return;
    if (!selectedCourse) {
      setMessage('먼저 과목을 선택해 주세요.');
      return;
    }
    if (selectedLectureIds.length === 0) {
      setMessage('다운로드할 강의를 선택해 주세요.');
      return;
    }
    const selectedIds = [...effectiveSelectedIds];
    if (selectedIds.length === 0) {
      setMessage('영상/음성/자막 파일은 영상 강의에서만 만들 수 있습니다. 영상 항목을 선택해 주세요.');
      return;
    }
    setIsStartingDownload(true);
    setDownloadResult(null);
    setBatchResult(null);
    setDownloadJob(null);
    setDownloadStartError(null);
    setDownloadDiagnostics([]);
    setMessage('저장 경로를 확인하는 중입니다.');
    try {
      const normalizedOutputs = selectedOutputFormats.length === 0 ? ['mp4'] as DownloadOutputFormat[] : selectedOutputFormats;
      appendDownloadDiagnostic('click.accepted', {
        outputs: normalizedOutputs.join('+'),
        selected_count: selectedIds.length,
        course_id: selectedCourse.id,
        output_dir_present: Boolean(downloadDir.trim()),
        tauri: isTauriRuntime(),
      });
      if ((normalizedOutputs.includes('mp3') || normalizedOutputs.includes('txt')) && media.ffmpegDependency && !media.ffmpegDependency.available) {
        const text = `MP3/TXT 생성에는 ffmpeg가 필요합니다. ${media.ffmpegDependency.install_hint ?? '앱을 재설치한 뒤 다시 시도하세요.'}`;
        appendDownloadDiagnostic('dependency.blocked', { component: 'ffmpeg' });
        setDownloadStartError(text);
        setMessage(text);
        return;
      }
      if (normalizedOutputs.includes('txt') && media.whisperStatus && !media.whisperStatus.available) {
        const text = `TXT 생성에는 STT 엔진 설치가 필요합니다. ${media.whisperStatus.install_hint ?? '앱을 재설치한 뒤 다시 시도하세요.'}`;
        appendDownloadDiagnostic('dependency.blocked', { component: 'stt' });
        setDownloadStartError(text);
        setMessage(text);
        return;
      }
      const conflictStrategy = resolveConflictStrategy(selectedIds);
      appendDownloadDiagnostic('conflict_check.done', { strategy: conflictStrategy });
      setMessage(conflictStrategy === 'skip' ? '기존 파일은 건너뛰고 다운로드를 시작했습니다.' : '다운로드를 시작했습니다. 진행률은 아래에서 확인할 수 있습니다.');
      appendDownloadDiagnostic('job_start.start', {
        mode: normalizedOutputs.length === 1 && normalizedOutputs[0] === 'mp4' ? (selectedIds.length === 1 ? 'single_mp4' : 'batch_mp4') : 'artifact',
      });
      const job = normalizedOutputs.length === 1 && normalizedOutputs[0] === 'mp4'
        ? selectedIds.length === 1
          ? await startDownloadJob(selectedCourse.id, selectedIds[0], selectedCourse.name, downloadDir, conflictStrategy)
          : await startBatchDownloadJob(selectedCourse.id, selectedCourse.name, selectedIds, downloadDir, conflictStrategy)
        : await startArtifactDownloadJob(selectedCourse.id, selectedCourse.name, selectedIds, downloadDir, normalizedOutputs, conflictStrategy);
      appendDownloadDiagnostic('job_start.done', { job_id: job.job_id, status: job.status });
      setDownloadJob(job);
      setMessage(`${downloadStageLabel(job.status)} 상태입니다.`);
    } catch (error) {
      if (isAuthenticationError(error)) {
        appendDownloadDiagnostic('job_start.auth_error', { code: error.code });
        await returnToLoginAfterAuthenticationFailure(error);
        return;
      }
      const text = error instanceof Error ? error.message : '다운로드 시작 실패';
      appendDownloadDiagnostic('job_start.failed', { error: text });
      setDownloadStartError(text);
      setMessage(text);
    } finally {
      setIsStartingDownload(false);
    }
  }

  async function handleCancelDownloadJob() {
    if (!downloadJob) return;
    try {
      const job = await cancelDownloadJob(downloadJob.job_id);
      setDownloadJob((current) => (current && current.job_id === job.job_id ? job : current));
      setMessage('취소를 요청했습니다. 잠시 후 상태가 바뀝니다.');
    } catch (error) {
      await reportError(error, '다운로드 취소 요청 실패');
    }
  }

  async function handleOpenOfficialViewer(lecture: CourseContentItem | null = selectedLecture) {
    if (!selectedCourse || lecture?.module_item_id === null || lecture?.module_item_id === undefined || isOpeningViewer) return;
    setSelectedLecture(lecture);
    setWatchDebugEvents([]);
    setIsOpeningViewer(true);
    setMessage('공식 시청 창을 준비하는 중입니다...');
    try {
      if (isTauriRuntime()) {
        const plan = await planOfficialViewerSessionBridge(selectedCourse.id, lecture.module_item_id, selectedCourse.name);
        if (!plan.viewer_url) throw new Error('공식 시청 주소를 찾지 못했습니다.');
        await openOfficialViewer(plan.viewer_url, plan.title ?? lecture.title ?? '숭실 LMS 공식 시청 창', plan);
      } else {
        const plan = await planOfficialViewer(selectedCourse.id, lecture.module_item_id, selectedCourse.name);
        if (!plan.viewer_url) throw new Error('공식 시청 주소를 찾지 못했습니다.');
        await openOfficialViewer(plan.viewer_url, plan.title ?? lecture.title ?? '숭실 LMS 공식 시청 창');
      }
      setMessage(isTauriRuntime() ? '시청 창을 앞으로 가져왔습니다. 재생/진도 처리는 숭실 LMS 공식 플레이어가 그대로 수행합니다.' : '브라우저 개발 모드라 공식 시청 창을 새 탭으로 열었습니다. 앱 내부 시청은 Tauri 데스크톱 앱에서 동작합니다.');
    } catch (error) {
      await reportError(error, '공식 시청 창을 열 수 없습니다.');
    } finally {
      setIsOpeningViewer(false);
    }
  }

  async function openWatchQueueItem(index: number) {
    const course = selectedCourseRef.current;
    const lecture = watchQueueRef.current[index];
    if (!course || !lecture || lecture.module_item_id === null || lecture.module_item_id === undefined) return;
    watchQueueIndexRef.current = index;
    watchQueueOpeningRef.current = true;
    setWatchDebugEvents([]);
    setWatchQueueState({ active: true, index, total: watchQueueRef.current.length });
    setSelectedLecture(lecture);
    setMessage(`순차 시청 ${index + 1}/${watchQueueRef.current.length}: 공식 플레이어를 열고 자동 재생을 시도합니다.`);
    try {
      const plan = await planOfficialViewerSessionBridge(course.id, lecture.module_item_id, course.name);
      if (!plan.viewer_url) throw new Error('공식 시청 주소를 찾지 못했습니다.');
      await openAssistedOfficialViewer(plan.viewer_url, plan.title ?? lecture.title ?? '숭실 LMS 공식 시청 창', plan);
    } catch (error) {
      watchQueueActiveRef.current = false;
      setWatchQueueState((current) => ({ ...current, active: false }));
      await reportError(error, '순차 시청 창을 열 수 없습니다.');
    } finally {
      watchQueueOpeningRef.current = false;
    }
  }

  async function handleStartSequentialWatch() {
    if (!selectedCourse || watchQueueState.active || isOpeningViewer || isDownloadActive || isListLoading) return;
    const queue = filteredContentItems.filter((lecture) => lecture.kind === 'video' && lecture.module_item_id !== null && lecture.module_item_id !== undefined);
    if (queue.length === 0) {
      setMessage('표시된 목록에 순차 시청할 영상이 없습니다. 필터를 영상/미완료 등으로 조정해 주세요.');
      return;
    }
    watchQueueRef.current = queue;
    watchQueueActiveRef.current = true;
    selectedCourseRef.current = selectedCourse;
    await openWatchQueueItem(0);
  }

  function handleStopSequentialWatch() {
    watchQueueActiveRef.current = false;
    setWatchQueueState((current) => ({ ...current, active: false }));
    setMessage('순차 시청 모드를 중지했습니다. 현재 열린 공식 시청 창은 직접 닫아 주세요.');
  }

  function toggleLectureSelection(lecture: CourseContentItem) {
    if (!lecture.downloadable || lecture.module_item_id === null) return;
    setSelectedLectureIds((current) => (
      current.some((id) => String(id) === String(lecture.module_item_id))
        ? current.filter((id) => String(id) !== String(lecture.module_item_id))
        : [...current, lecture.module_item_id!]
    ));
  }

  function handleSelectAllLectures(checked?: boolean) {
    const selectableIds = selectedOutputRequiresVideo
      ? selectableVideoIds
      : filteredContentItems
        .filter((lecture) => lecture.downloadable && lecture.module_item_id !== null)
        .map((lecture) => lecture.module_item_id!);
    const selectedKeys = new Set(selectedLectureIds.map((id) => String(id)));
    const allSelected = selectableIds.length > 0 && selectableIds.every((id) => selectedKeys.has(String(id)));
    const shouldSelectAll = checked ?? !allSelected;
    setSelectedLectureIds(shouldSelectAll ? selectableIds : []);
    if (shouldSelectAll && selectableIds.length > 0) {
      setSelectedLecture(filteredContentItems.find((lecture) => lecture.downloadable && lecture.module_item_id !== null) ?? null);
    }
  }

  useEffect(() => {
    selectedCourseRef.current = selectedCourse;
  }, [selectedCourse]);

  // The official viewer reports the real video `ended` event; advance the sequential-watch queue.
  useTauriEvent<{ stage: string }>('official-watch-ended', () => {
    if (!watchQueueActiveRef.current || watchQueueOpeningRef.current) return;
    const nextIndex = watchQueueIndexRef.current + 1;
    if (nextIndex >= watchQueueRef.current.length) {
      watchQueueActiveRef.current = false;
      setWatchQueueState((current) => ({ ...current, active: false, index: current.total }));
      setMessage('순차 시청 모드가 끝났습니다. 공식 플레이어가 종료 신호를 보낸 항목까지만 다음으로 이동했습니다.');
      return;
    }
    void openWatchQueueItem(nextIndex);
  });

  useTauriEvent<WatchDebugEvent>('official-watch-debug', (payload) => {
    setWatchDebugEvents((current) => [payload, ...current].slice(0, 40));
  });

  useEffect(() => {
    void initializeDownloadDir();
    void (async () => {
      const credential = await loadCredentialStatus();
      if (!credential?.username_present || !credential?.password_present) {
        setIsSessionVerified(false);
        setMessage('숭실 LMS 계정으로 로그인하면 강의 목록을 불러옵니다.');
        return;
      }
      setIsSessionVerified(false);
      setState('loading');
      setMessage('저장된 로그인 정보로 LMS 세션을 확인하는 중입니다...');
      try {
        const currentTermId = await loadTerms();
        void loadCourses(currentTermId);
      } catch {
        // Authentication failures are already routed back to the login screen by loadTerms.
      }
    })();
  }, []);

  useEffect(() => {
    if (selectedCourse) {
      void selectCourse(selectedCourse);
    }
  }, [selectedCourse?.id]);

  // One poller per active job: setTimeout chain, no overlapping requests, stale responses dropped.
  usePolledJob(downloadJob, {
    fetch: fetchDownloadJob,
    isTerminal: (job) => TERMINAL_DOWNLOAD_STATUSES.has(job.status),
    onUpdate: async (job) => {
      setDownloadJob((current) => (current && current.job_id === job.job_id ? job : current));
      if (job.status === 'failed' && job.error_code && isAuthenticationError(new ApiError(job.error_code, job.error_code))) {
        await returnToLoginAfterAuthenticationFailure(new ApiError(job.error_code, '로그인 세션이 만료되었습니다. 다시 로그인해 주세요.'));
        return;
      }
      const payload = downloadPayloadFromArtifact(job.result);
      if (isBatchDownloadResult(payload)) {
        setBatchResult(payload);
      } else if (payload) {
        setDownloadResult(payload);
      }
      if (TERMINAL_DOWNLOAD_STATUSES.has(job.status)) setMessage(`${downloadStageLabel(job.status)} 상태입니다.`);
    },
    onError: (error) => reportError(error, '작업 상태 조회 실패'),
  });

  const availableWeeks = useMemo(() => Array.from(new Set(contentItems.map((item) => String(item.week_position ?? item.module_name ?? 'unknown')))), [contentItems]);
  const filteredContentItems = useMemo(() => contentItems.filter((item) => {
    const weekKey = String(item.week_position ?? item.module_name ?? 'unknown');
    if (weekFilter !== 'all' && weekKey !== weekFilter) return false;
    if (contentKindFilter === 'downloadable' && !item.downloadable) return false;
    if (contentKindFilter !== 'all' && contentKindFilter !== 'downloadable' && item.kind !== contentKindFilter) return false;
    if (completionFilter === 'completed' && item.completed !== true) return false;
    if (completionFilter === 'uncompleted' && item.completed !== false) return false;
    if (completionFilter === 'unknown' && item.completed !== null) return false;
    return true;
  }), [contentItems, contentKindFilter, completionFilter, weekFilter]);
  const completedCount = useMemo(() => contentItems.filter((lecture) => lecture.completed === true).length, [contentItems]);
  const selectableLectureIds = useMemo(() => filteredContentItems.filter((lecture) => lecture.downloadable && lecture.module_item_id !== null).map((lecture) => lecture.module_item_id!), [filteredContentItems]);
  const selectedKeys = useMemo(() => new Set(selectedLectureIds.map((id) => String(id))), [selectedLectureIds]);
  const selectedContentItems = useMemo(() => contentItems.filter((item) => item.module_item_id !== null && selectedKeys.has(String(item.module_item_id))), [contentItems, selectedKeys]);
  const selectedVideoItems = useMemo(() => selectedContentItems.filter((item) => item.kind === 'video'), [selectedContentItems]);
  const selectedOutputRequiresVideo = selectedOutputFormats.some((format) => format === 'mp4' || format === 'mp3' || format === 'txt');
  const selectableVideoIds = useMemo(() => filteredContentItems.filter((lecture) => lecture.downloadable && lecture.kind === 'video' && lecture.module_item_id !== null).map((lecture) => lecture.module_item_id!), [filteredContentItems]);
  const visibleSelectableIds = selectedOutputRequiresVideo ? selectableVideoIds : selectableLectureIds;
  const selectableCount = visibleSelectableIds.length;
  const selectedCount = selectedOutputRequiresVideo ? selectedVideoItems.length : selectedLectureIds.length;
  const allLecturesSelected = selectableCount > 0 && visibleSelectableIds.every((id) => selectedKeys.has(String(id)));
  const effectiveSelectedIds = selectedOutputRequiresVideo ? selectedVideoItems.map((item) => item.module_item_id!).filter((id) => id !== null && id !== undefined) : selectedLectureIds;
  const selectedOutputLabel = selectedOutputFormats.map((item) => item.toUpperCase()).join('+') || 'MP4';
  const downloadPrimaryActionLabel = isStartingDownload ? '다운로드 준비 중...' : isDownloadActive ? '취소하기' : selectedOutputRequiresVideo && effectiveSelectedIds.length === 0 ? '영상을 선택하세요' : selectedCount === 0 ? '강의를 선택하세요' : effectiveSelectedIds.length > 1 ? `${effectiveSelectedIds.length}개 ${selectedOutputLabel} 다운로드 시작` : `${selectedOutputLabel} 다운로드 시작`;
  const isCourseListLoading = state === 'loading';
  const isListLoading = isCourseListLoading || isCourseDetailLoading;
  const canOpenLectureOfficialViewer = (lecture: CourseContentItem | null | undefined) => Boolean(selectedCourse && lecture?.kind === 'video' && lecture.module_item_id !== null && lecture.module_item_id !== undefined && !isDownloadActive && !isListLoading);
  const selectedOutputRequiresMediaDependencies = selectedOutputFormats.some((format) => format === 'mp3' || format === 'txt');
  const missingSelectedOutputComponents = selectedOutputRequiresMediaDependencies ? media.missingComponents : [];
  const canStartSelectedDownload = Boolean(selectedCourse) && effectiveSelectedIds.length > 0 && !isStartingDownload && !downloadJob?.cancel_requested && missingSelectedOutputComponents.length === 0;

  function toggleOutputFormat(format: DownloadOutputFormat) {
    setSelectedOutputFormats((current) => {
      const next = current.includes(format) ? current.filter((item) => item !== format) : [...current, format];
      const normalized = next.length === 0 ? ['mp4'] as DownloadOutputFormat[] : next;
      const requiresVideo = normalized.some((item) => item === 'mp3' || item === 'txt');
      if (requiresVideo) {
        const selectedVideoIds = selectedContentItems
          .filter((item) => item.kind === 'video' && item.module_item_id !== null)
          .map((item) => item.module_item_id!);
        if (selectedVideoIds.length > 0) {
          setSelectedLectureIds(selectedVideoIds);
        } else if (selectableVideoIds.length > 0) {
          setSelectedLectureIds([selectableVideoIds[0]]);
          const firstVideo = filteredContentItems.find((item) => item.kind === 'video' && item.module_item_id !== null);
          if (firstVideo) setSelectedLecture(firstVideo);
        }
      }
      return normalized;
    });
  }

  async function handleDownloadPrimaryAction() {
    if (isDownloadActive) {
      await handleCancelDownloadJob();
      return;
    }
    await handleDownloadJobStart();
  }

  async function handleOpenDownloadedFolder() {
    if (isOpeningFolder) return;
    setIsOpeningFolder(true);
    try {
      await openDownloadedFolder(downloadView.downloadOutputPath);
      setMessage('다운로드 폴더를 열었습니다.');
    } catch (error) {
      setMessage(error instanceof Error ? error.message : '다운로드 폴더를 열 수 없습니다.');
    } finally {
      setIsOpeningFolder(false);
    }
  }

  const hasStoredCredential = Boolean(credentialStatus?.username_present && credentialStatus?.password_present);
  const shouldShowLogin = !hasStoredCredential || !isSessionVerified;

  if (shouldShowLogin) {
    return (
      <LoginPanel
        credentialStatus={credentialStatus}
        formRef={credentialFormRef}
        state={state}
        message={message}
        isAuthenticating={isAuthenticating}
        onSubmit={handleCredentialSubmit}
      />
    );
  }

  return (
    <main className="app-shell">
      <section className="hero-card">
        <div>
          <p className="eyebrow">Soongsil LMS local downloader</p>
          <h1>현재 학기 강의 다운로드</h1>
          <p className="hero-copy">
            기본 화면은 LearningX의 최신 활성 학기만 보여줍니다. 과거 학기는 명시 선택 UX로 따로 열어야 합니다.
          </p>
        </div>
        <div className="hero-actions">
          <span>{backendMode === 'unknown' ? '앱 연결 확인 중' : '앱 연결됨'}</span>
          <button className="primary" onClick={() => void handleRefreshCourses()} disabled={isListLoading || isLoggingOut || isAnyJobActive}>
            현재 학기 새로고침
          </button>
          <button className="secondary-action" type="button" onClick={handleCredentialDelete} disabled={!credentialStatus?.can_write || isLoggingOut || isListLoading || isDownloadActive}>
            {isLoggingOut ? '로그아웃 중...' : '로그아웃'}
          </button>
        </div>
      </section>

      <p className={`status ${state}`}>{message}</p>

      <section className="grid" aria-busy={isCourseListLoading}>
        <CourseList
          courses={courses}
          terms={terms}
          selectedTermId={selectedTermId}
          selectedCourse={selectedCourse}
          disabled={isListLoading || isAnyJobActive}
          onTermChange={setSelectedTermId}
          onLoadTerm={() => void handleLoadSelectedTerm()}
          onSelectCourse={setSelectedCourse}
        />

        <LecturePanel
          contentItems={contentItems}
          filteredContentItems={filteredContentItems}
          availableWeeks={availableWeeks}
          completedCount={completedCount}
          weekFilter={weekFilter}
          contentKindFilter={contentKindFilter}
          completionFilter={completionFilter}
          selectedLecture={selectedLecture}
          selectedLectureIds={selectedLectureIds}
          selectedCount={selectedCount}
          selectableCount={selectableCount}
          allLecturesSelected={allLecturesSelected}
          selectedOutputRequiresVideo={selectedOutputRequiresVideo}
          isCourseDetailLoading={isCourseDetailLoading}
          locked={isDownloadActive || isListLoading}
          isOpeningViewer={isOpeningViewer}
          canWatchLecture={canOpenLectureOfficialViewer}
          onWeekFilterChange={handleWeekFilterChange}
          onContentKindFilterChange={handleContentKindFilterChange}
          onCompletionFilterChange={handleCompletionFilterChange}
          onSelectAll={handleSelectAllLectures}
          onRowClick={(lecture) => {
            setSelectedLecture(lecture);
            toggleLectureSelection(lecture);
          }}
          onToggleLecture={toggleLectureSelection}
          onWatch={(lecture) => { void handleOpenOfficialViewer(lecture); }}
          watchControl={(
            <WatchQueueControl
              queue={watchQueueState}
              startDisabled={!selectedCourse || filteredContentItems.every((lecture) => lecture.kind !== 'video' || lecture.module_item_id === null || lecture.module_item_id === undefined) || isDownloadActive || isListLoading || isOpeningViewer}
              onStart={() => { void handleStartSequentialWatch(); }}
              onStop={handleStopSequentialWatch}
            />
          )}
        />

        <DownloadPanel
          selectedLecture={selectedLecture}
          selectedCourse={selectedCourse}
          selectedCount={selectedCount}
          selectedOutputFormats={selectedOutputFormats}
          onToggleOutputFormat={toggleOutputFormat}
          media={media}
          missingSelectedOutputComponents={missingSelectedOutputComponents}
          downloadDir={downloadDir}
          onDownloadDirChange={setDownloadDir}
          onPickDownloadDir={() => void handlePickDownloadDir()}
          isStartingDownload={isStartingDownload}
          isDownloadActive={isDownloadActive}
          downloadJob={downloadJob}
          canStartSelectedDownload={canStartSelectedDownload}
          downloadPrimaryActionLabel={downloadPrimaryActionLabel}
          onPrimaryAction={() => void handleDownloadPrimaryAction()}
          watchDebugEvents={watchDebugEvents}
          view={downloadView}
          downloadStartError={downloadStartError}
          downloadDiagnostics={downloadDiagnostics}
          isOpeningFolder={isOpeningFolder}
          onOpenFolder={() => void handleOpenDownloadedFolder()}
        />
      </section>
    </main>
  );
}

function Root() {
  const { status, retry } = useBackendStatus();
  // Everything below talks to the API, so render it only once the backend is ready.
  if (status.state !== 'ready') {
    return <BackendStatus status={status} onRetry={() => void retry()} />;
  }
  return <App backendMode={status.mode} />;
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
