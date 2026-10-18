import { useEffect, useRef, useState } from 'react';
import {
  ApiError,
  fetchChromeWatchStatus,
  isAuthenticationError,
  nextChromeWatch,
  planOfficialViewer,
  planOfficialViewerSessionBridge,
  showChromeWatch,
  startChromeWatch,
  stopChromeWatch,
} from '../lib/api';
import {
  isTauriRuntime,
  openOfficialViewer,
  watchExternalStatus,
  watchQueueNext,
  watchQueueShowWindow,
  watchQueueStart,
  watchQueueStatus,
  watchQueueStop,
} from '../lib/desktop';
import { friendlyError } from '../lib/errors';
import type { ChromeWatchStatus, CourseContentItem, CourseSummary, WatchDebugEvent, WatchMode, WatchQueueNotice, WatchQueueState } from '../lib/types';
import type { AllCoursesQueueItem } from '../lib/watch';
import { IDLE_WATCH_QUEUE, chromeStatusToQueue, newChromeNotices, resolveWatchMode, toChromeAllInputs, toChromeInputs, toExternalView, toQueueInputs } from '../lib/watch';
import { startPolling } from '../lib/polling';
import { useTauriEvent } from './useTauriEvent';

const CHROME_POLL_MS = 2500;

interface Options {
  notify: (message: string, kind?: 'info' | 'success' | 'error') => void;
  onAuthError: (message: string) => void;
  /** A lecture of the queue played to its real end (mark it watched right away). */
  onItemWatched: (moduleItemId: string) => void;
  /** The queue ended or was stopped; the course it ran for, or `'all'` for the all-course queue (reload later). */
  onQueueSettled: (scope: CourseSummary | 'all') => void;
  /** The saved playback mode choice (`null`: none yet, so Chrome when it is installed). */
  modePreference: WatchMode | null;
}

/**
 * Opens the official Commons player (one app-owned window) for a lecture, and drives the
 * "순서대로 시청" queue. The queue itself — state, the small always-visible playback window,
 * the menu bar item — lives in the desktop shell, which advances only when the player reports
 * the real video `ended` event; this hook starts/stops it and mirrors its state for the UI.
 */
export function useOfficialViewer({ notify, onAuthError, onItemWatched, onQueueSettled, modePreference }: Options) {
  const [openingKey, setOpeningKey] = useState<string | null>(null);
  const [queue, setQueue] = useState<WatchQueueState>(IDLE_WATCH_QUEUE);
  const [debugEvents, setDebugEvents] = useState<WatchDebugEvent[]>([]);
  const openingRef = useRef(false);
  const scopeRef = useRef<CourseSummary | 'all' | null>(null);
  const wasActiveRef = useRef(false);
  const optionsRef = useRef({ notify, onAuthError, onItemWatched, onQueueSettled });
  useEffect(() => {
    optionsRef.current = { notify, onAuthError, onItemWatched, onQueueSettled };
  });
  // Chrome mode: the backend drives the queue; this hook polls its status.
  const [chromeAvailable, setChromeAvailable] = useState<boolean | null>(null);
  const [chromePolling, setChromePolling] = useState(false);
  const queueRef = useRef(queue);
  const chromeRun = useRef({ runId: '', lastNoticeId: 0, watched: new Set<string>(), lastSentActive: false });
  useEffect(() => {
    queueRef.current = queue;
  });
  const mode = resolveWatchMode(modePreference, chromeAvailable);

  function fail(error: unknown, fallback: string) {
    if (isAuthenticationError(error)) optionsRef.current.onAuthError(friendlyError(error));
    else optionsRef.current.notify(friendlyError(error, fallback), 'error');
  }

  function applyQueue(next: WatchQueueState) {
    setQueue(next);
    if (wasActiveRef.current && !next.active && scopeRef.current) optionsRef.current.onQueueSettled(scopeRef.current);
    wasActiveRef.current = next.active;
  }

  /** Fold one backend status of the Chrome queue into the UI, the menu bar and the toasts. */
  function applyChromeStatus(status: ChromeWatchStatus, options: { adopt?: boolean } = {}) {
    setChromeAvailable(status.chrome_available);
    // A status without a run says nothing about a queue; it must not wipe the in-app queue's state.
    if (!status.run_id) return;
    const run = chromeRun.current;
    if (run.runId !== status.run_id) {
      run.runId = status.run_id;
      run.watched = new Set();
      run.lastSentActive = false;
      // A run first seen while it is already going (the window was reloaded) does not replay old notices.
      run.lastNoticeId = options.adopt ? Math.max(0, ...status.notices.map((notice) => notice.id)) : 0;
      if (options.adopt && !scopeRef.current) scopeRef.current = 'all';
    }
    for (const item of status.items) {
      if (item.state === 'done' && !run.watched.has(item.module_item_id)) {
        run.watched.add(item.module_item_id);
        optionsRef.current.onItemWatched(item.module_item_id);
      }
    }
    const authLost = status.state === 'failed' && isAuthenticationError(new ApiError(status.error_code ?? '', ''));
    for (const notice of newChromeNotices(status, run.lastNoticeId)) {
      run.lastNoticeId = notice.id;
      if (notice.kind === 'failed' && authLost) continue;
      optionsRef.current.notify(notice.text, notice.level);
    }
    if (authLost) optionsRef.current.onAuthError(friendlyError(new ApiError(status.error_code ?? '', '')));
    const next = chromeStatusToQueue(status);
    applyQueue(next);
    if (isTauriRuntime() && (status.active || run.lastSentActive)) {
      run.lastSentActive = status.active;
      void watchExternalStatus(toExternalView(status)).catch(() => {
        // Older shell without the menu bar bridge; the strip still works.
      });
    }
    if (!status.active) setChromePolling(false);
  }

  // The latest closure, so the poller (started once per run) never applies a status with stale callbacks.
  const applyChromeRef = useRef(applyChromeStatus);
  useEffect(() => {
    applyChromeRef.current = applyChromeStatus;
  });

  useEffect(() => {
    if (!chromePolling) return;
    // A setTimeout chain (lib/polling.ts): a slow answer never overlaps the next request. A missed poll is retried.
    return startPolling<ChromeWatchStatus>({
      intervalMs: CHROME_POLL_MS,
      fetch: fetchChromeWatchStatus,
      onUpdate: (status) => applyChromeRef.current(status),
      isTerminal: (status) => !status.active,
    });
  }, [chromePolling]);

  // On load: learn whether Chrome is installed and pick up a Chrome queue that is already running.
  useEffect(() => {
    let cancelled = false;
    fetchChromeWatchStatus()
      .then((status) => {
        if (cancelled) return;
        if (status.active) {
          applyChromeStatus(status, { adopt: true });
          setChromePolling(true);
        } else {
          setChromeAvailable(status.chrome_available);
        }
      })
      .catch(() => {
        // Backend without the Chrome routes: stay with the in-app window.
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The window may have been reloaded while a queue runs: pick its state up again.
  useEffect(() => {
    if (!isTauriRuntime()) return;
    let cancelled = false;
    watchQueueStatus()
      .then((state) => {
        if (!cancelled && state.active) {
          wasActiveRef.current = true;
          setQueue(state);
        }
      })
      .catch(() => {
        // Older shell without the queue commands; nothing to mirror.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function open(course: CourseSummary, lecture: CourseContentItem, key: string) {
    if (lecture.module_item_id === null || lecture.module_item_id === undefined || openingRef.current) return;
    openingRef.current = true;
    setOpeningKey(key);
    setDebugEvents([]);
    try {
      if (isTauriRuntime()) {
        const plan = await planOfficialViewerSessionBridge(course.id, lecture.module_item_id, course.name);
        if (!plan.viewer_url) throw new Error('시청 주소를 찾지 못했어요.');
        await openOfficialViewer(plan.viewer_url, plan.title ?? lecture.title ?? '숭실 LMS 공식 시청 창', plan);
      } else {
        const plan = await planOfficialViewer(course.id, lecture.module_item_id, course.name);
        if (!plan.viewer_url) throw new Error('시청 주소를 찾지 못했어요.');
        await openOfficialViewer(plan.viewer_url, plan.title ?? lecture.title ?? '숭실 LMS 공식 시청 창');
      }
    } catch (error) {
      fail(error, '시청 창을 열지 못했어요.');
    } finally {
      openingRef.current = false;
      setOpeningKey(null);
    }
  }

  async function startChrome(items: Parameters<typeof startChromeWatch>[0]) {
    try {
      const status = await startChromeWatch(items);
      applyChromeStatus(status);
      setChromePolling(true);
    } catch (error) {
      fail(error, 'Chrome 창에서 자동 재생을 시작하지 못했어요.');
    }
  }

  /** Starts the queue with `lectures` in the given order (already filtered and sorted). */
  async function startQueue(course: CourseSummary, lectures: CourseContentItem[]) {
    if (queue.active) return;
    if (mode === 'app' && !isTauriRuntime()) {
      optionsRef.current.notify('순서대로 시청은 데스크톱 앱에서만 쓸 수 있어요.', 'info');
      return;
    }
    const inputs = toQueueInputs(lectures);
    if (inputs.length === 0) {
      optionsRef.current.notify('순서대로 시청할 영상이 없어요.', 'info');
      return;
    }
    scopeRef.current = course;
    setDebugEvents([]);
    if (mode === 'chrome') {
      await startChrome(toChromeInputs(course, lectures));
      return;
    }
    try {
      applyQueue(await watchQueueStart({ course_id: String(course.id), course_name: course.name, items: inputs }));
    } catch (error) {
      fail(error, '순서대로 시청을 시작하지 못했어요.');
    }
  }

  /** Starts the all-course queue: every item names its own course and plays in the given order. */
  async function startAllQueue(queueItems: AllCoursesQueueItem[]) {
    if (queue.active) return;
    if (mode === 'app' && !isTauriRuntime()) {
      optionsRef.current.notify('순서대로 시청은 데스크톱 앱에서만 쓸 수 있어요.', 'info');
      return;
    }
    if (queueItems.length === 0) {
      optionsRef.current.notify('순서대로 시청할 영상이 없어요.', 'info');
      return;
    }
    scopeRef.current = 'all';
    setDebugEvents([]);
    if (mode === 'chrome') {
      await startChrome(toChromeAllInputs(queueItems));
      return;
    }
    try {
      applyQueue(await watchQueueStart({ items: queueItems }));
    } catch (error) {
      fail(error, '순서대로 시청을 시작하지 못했어요.');
    }
  }

  async function stop() {
    try {
      if (queueRef.current.source === 'chrome') {
        applyChromeStatus(await stopChromeWatch());
      } else {
        applyQueue(await watchQueueStop());
      }
      optionsRef.current.notify('순서대로 시청을 멈췄어요.', 'info');
    } catch (error) {
      fail(error, '순서대로 시청을 멈추지 못했어요.');
    }
  }

  async function next() {
    try {
      if (queueRef.current.source === 'chrome') applyChromeStatus(await nextChromeWatch());
      else applyQueue(await watchQueueNext());
    } catch (error) {
      fail(error, '다음 강의로 넘어가지 못했어요.');
    }
  }

  async function showWindow() {
    try {
      if (queueRef.current.source === 'chrome') await showChromeWatch();
      else await watchQueueShowWindow();
    } catch (error) {
      fail(error, queueRef.current.source === 'chrome' ? 'Chrome 창을 보여주지 못했어요.' : '재생 창을 보여주지 못했어요.');
    }
  }

  // Menu bar actions on a Chrome queue come back through the app (the shell cannot reach the backend).
  useTauriEvent<{ command: string }>('watch-external-command', ({ command }) => {
    if (queueRef.current.source !== 'chrome') return;
    if (command === 'next') void next();
    else if (command === 'stop') void stop();
    else if (command === 'show') void showWindow();
  });

  // Every state change of the shell's queue (a report every few seconds while playing).
  useTauriEvent<WatchQueueState>('watch-queue-changed', applyQueue);

  // The player reported the real video `ended` event: that lecture counts as watched.
  useTauriEvent<{ stage: string; module_item_id?: string | null }>('official-watch-ended', (payload) => {
    if (payload.module_item_id) optionsRef.current.onItemWatched(payload.module_item_id);
  });

  useTauriEvent<WatchQueueNotice>('watch-queue-notice', (payload) => {
    if (payload.auth_expired) optionsRef.current.onAuthError(payload.text);
    else optionsRef.current.notify(payload.text, payload.kind);
  });

  useTauriEvent<WatchDebugEvent>('official-watch-debug', (payload) => {
    setDebugEvents((current) => [payload, ...current].slice(0, 40));
  });

  return { openingKey, queue, debugEvents, mode, chromeAvailable, open, startQueue, startAllQueue, stop, next, showWindow };
}

export type OfficialViewer = ReturnType<typeof useOfficialViewer>;
