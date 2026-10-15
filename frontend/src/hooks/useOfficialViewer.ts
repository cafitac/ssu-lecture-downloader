import { useEffect, useRef, useState } from 'react';
import { isAuthenticationError, planOfficialViewer, planOfficialViewerSessionBridge } from '../lib/api';
import { isTauriRuntime, openAssistedOfficialViewer, openOfficialViewer } from '../lib/desktop';
import { friendlyError } from '../lib/errors';
import type { CourseContentItem, CourseSummary, WatchDebugEvent, WatchQueueState } from '../lib/types';
import { useTauriEvent } from './useTauriEvent';

interface Options {
  notify: (message: string, kind?: 'info' | 'success' | 'error') => void;
  onAuthError: (message: string) => void;
}

/**
 * Opens the official Commons player (one app-owned window) for a lecture, and runs the
 * experimental "watch in order" mode that advances when the player reports the real
 * video `ended` event.
 */
export function useOfficialViewer({ notify, onAuthError }: Options) {
  const [openingKey, setOpeningKey] = useState<string | null>(null);
  const [queue, setQueue] = useState<WatchQueueState>({ active: false, index: 0, total: 0 });
  const [debugEvents, setDebugEvents] = useState<WatchDebugEvent[]>([]);
  const queueRef = useRef<CourseContentItem[]>([]);
  const indexRef = useRef(0);
  const activeRef = useRef(false);
  const openingRef = useRef(false);
  const courseRef = useRef<CourseSummary | null>(null);
  const optionsRef = useRef({ notify, onAuthError });
  useEffect(() => {
    optionsRef.current = { notify, onAuthError };
  });

  function fail(error: unknown, fallback: string) {
    if (isAuthenticationError(error)) optionsRef.current.onAuthError(friendlyError(error));
    else optionsRef.current.notify(friendlyError(error, fallback), 'error');
  }

  function stopQueue() {
    activeRef.current = false;
    setQueue((current) => ({ ...current, active: false }));
  }

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

  async function openQueueItem(index: number) {
    const course = courseRef.current;
    const lecture = queueRef.current[index];
    if (!course || !lecture || lecture.module_item_id === null || lecture.module_item_id === undefined) return;
    indexRef.current = index;
    openingRef.current = true;
    setDebugEvents([]);
    setQueue({ active: true, index, total: queueRef.current.length });
    try {
      const plan = await planOfficialViewerSessionBridge(course.id, lecture.module_item_id, course.name);
      if (!plan.viewer_url) throw new Error('시청 주소를 찾지 못했어요.');
      await openAssistedOfficialViewer(plan.viewer_url, plan.title ?? lecture.title ?? '숭실 LMS 공식 시청 창', plan);
    } catch (error) {
      stopQueue();
      fail(error, '시청 창을 열지 못했어요.');
    } finally {
      openingRef.current = false;
    }
  }

  async function startQueue(course: CourseSummary, lectures: CourseContentItem[]) {
    if (queue.active || openingRef.current) return;
    const playable = lectures.filter((lecture) => lecture.kind === 'video' && lecture.module_item_id !== null && lecture.module_item_id !== undefined);
    if (playable.length === 0) {
      optionsRef.current.notify('순서대로 시청할 영상이 없어요.', 'info');
      return;
    }
    queueRef.current = playable;
    courseRef.current = course;
    activeRef.current = true;
    await openQueueItem(0);
  }

  function stop() {
    stopQueue();
    optionsRef.current.notify('순서대로 시청을 멈췄어요. 열려 있는 시청 창은 직접 닫아 주세요.', 'info');
  }

  // The player reports the real video `ended` event; advance to the next lecture.
  useTauriEvent<{ stage: string }>('official-watch-ended', () => {
    if (!activeRef.current || openingRef.current) return;
    const next = indexRef.current + 1;
    if (next >= queueRef.current.length) {
      activeRef.current = false;
      setQueue((current) => ({ ...current, active: false, index: current.total }));
      optionsRef.current.notify('순서대로 시청을 마쳤어요.', 'success');
      return;
    }
    void openQueueItem(next);
  });

  useTauriEvent<WatchDebugEvent>('official-watch-debug', (payload) => {
    setDebugEvents((current) => [payload, ...current].slice(0, 40));
  });

  return { openingKey, queue, debugEvents, open, startQueue, stop };
}

export type OfficialViewer = ReturnType<typeof useOfficialViewer>;
