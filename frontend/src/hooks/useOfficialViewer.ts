import { useEffect, useRef, useState } from 'react';
import { isAuthenticationError, planOfficialViewer, planOfficialViewerSessionBridge } from '../lib/api';
import {
  isTauriRuntime,
  openOfficialViewer,
  watchQueueNext,
  watchQueueShowWindow,
  watchQueueStart,
  watchQueueStatus,
  watchQueueStop,
} from '../lib/desktop';
import { friendlyError } from '../lib/errors';
import type { CourseContentItem, CourseSummary, WatchDebugEvent, WatchQueueNotice, WatchQueueState } from '../lib/types';
import type { AllCoursesQueueItem } from '../lib/watch';
import { IDLE_WATCH_QUEUE, toQueueInputs } from '../lib/watch';
import { useTauriEvent } from './useTauriEvent';

interface Options {
  notify: (message: string, kind?: 'info' | 'success' | 'error') => void;
  onAuthError: (message: string) => void;
  /** A lecture of the queue played to its real end (mark it watched right away). */
  onItemWatched: (moduleItemId: string) => void;
  /** The queue ended or was stopped; the course it ran for, or `'all'` for the all-course queue (reload later). */
  onQueueSettled: (scope: CourseSummary | 'all') => void;
}

/**
 * Opens the official Commons player (one app-owned window) for a lecture, and drives the
 * "순서대로 시청" queue. The queue itself — state, the small always-visible playback window,
 * the menu bar item — lives in the desktop shell, which advances only when the player reports
 * the real video `ended` event; this hook starts/stops it and mirrors its state for the UI.
 */
export function useOfficialViewer({ notify, onAuthError, onItemWatched, onQueueSettled }: Options) {
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

  function fail(error: unknown, fallback: string) {
    if (isAuthenticationError(error)) optionsRef.current.onAuthError(friendlyError(error));
    else optionsRef.current.notify(friendlyError(error, fallback), 'error');
  }

  function applyQueue(next: WatchQueueState) {
    setQueue(next);
    if (wasActiveRef.current && !next.active && scopeRef.current) optionsRef.current.onQueueSettled(scopeRef.current);
    wasActiveRef.current = next.active;
  }

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

  /** Starts the queue with `lectures` in the given order (already filtered and sorted). */
  async function startQueue(course: CourseSummary, lectures: CourseContentItem[]) {
    if (queue.active) return;
    if (!isTauriRuntime()) {
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
    try {
      applyQueue(await watchQueueStart({ course_id: String(course.id), course_name: course.name, items: inputs }));
    } catch (error) {
      fail(error, '순서대로 시청을 시작하지 못했어요.');
    }
  }

  /** Starts the all-course queue: every item names its own course and plays in the given order. */
  async function startAllQueue(queueItems: AllCoursesQueueItem[]) {
    if (queue.active) return;
    if (!isTauriRuntime()) {
      optionsRef.current.notify('순서대로 시청은 데스크톱 앱에서만 쓸 수 있어요.', 'info');
      return;
    }
    if (queueItems.length === 0) {
      optionsRef.current.notify('순서대로 시청할 영상이 없어요.', 'info');
      return;
    }
    scopeRef.current = 'all';
    setDebugEvents([]);
    try {
      applyQueue(await watchQueueStart({ items: queueItems }));
    } catch (error) {
      fail(error, '순서대로 시청을 시작하지 못했어요.');
    }
  }

  async function stop() {
    try {
      applyQueue(await watchQueueStop());
      optionsRef.current.notify('순서대로 시청을 멈췄어요.', 'info');
    } catch (error) {
      fail(error, '순서대로 시청을 멈추지 못했어요.');
    }
  }

  async function next() {
    try {
      applyQueue(await watchQueueNext());
    } catch (error) {
      fail(error, '다음 강의로 넘어가지 못했어요.');
    }
  }

  async function showWindow() {
    try {
      await watchQueueShowWindow();
    } catch (error) {
      fail(error, '재생 창을 보여주지 못했어요.');
    }
  }

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

  return { openingKey, queue, debugEvents, open, startQueue, startAllQueue, stop, next, showWindow };
}

export type OfficialViewer = ReturnType<typeof useOfficialViewer>;
