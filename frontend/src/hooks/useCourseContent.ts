import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchCourseDetail, isAuthenticationError } from '../lib/api';
import { itemKey, legacyLectureToContentItem } from '../lib/content';
import { friendlyError } from '../lib/errors';
import type { CourseContentItem, CourseDetailResponse, CourseSummary } from '../lib/types';

export type ContentState = 'idle' | 'loading' | 'ready' | 'error';

function itemsOf(detail: CourseDetailResponse): CourseContentItem[] {
  return detail.content_items?.length ? detail.content_items : detail.lectures.map(legacyLectureToContentItem);
}

/** Lecture and material rows of the selected course; a newer selection discards older responses. */
export function useCourseContent(course: CourseSummary | null, outputDir: string, onAuthError: (message: string) => void) {
  const [items, setItems] = useState<CourseContentItem[]>([]);
  const [state, setState] = useState<ContentState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  // Videos that played to the end in this session, shown as watched until the LMS says so itself.
  const [watchedHere, setWatchedHere] = useState<ReadonlySet<string>>(new Set());
  const refreshRef = useRef(false);
  const onAuthErrorRef = useRef(onAuthError);
  useEffect(() => {
    onAuthErrorRef.current = onAuthError;
  });

  const courseId = course?.id ?? null;
  const courseName = course?.name ?? '';
  const courseIdRef = useRef<string | null>(null);
  courseIdRef.current = courseId === null ? null : String(courseId);
  const outputDirRef = useRef(outputDir);
  outputDirRef.current = outputDir;

  useEffect(() => {
    setItems([]);
    setError(null);
    if (courseId === null) {
      setState('idle');
      return;
    }
    let cancelled = false;
    setState('loading');
    const refresh = refreshRef.current;
    refreshRef.current = false;
    fetchCourseDetail(courseId, courseName, { refresh, outputDir })
      .then((detail) => {
        if (cancelled) return;
        setItems(itemsOf(detail));
        setState('ready');
      })
      .catch((failure: unknown) => {
        if (cancelled) return;
        if (isAuthenticationError(failure)) {
          onAuthErrorRef.current(friendlyError(failure));
          return;
        }
        setError(friendlyError(failure, '강의 목록을 불러오지 못했어요.'));
        setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [courseId, courseName, outputDir, attempt]);

  /** Loads the rows again; `refresh` also skips the backend's LMS caches. */
  const reload = useCallback((refresh = false) => {
    refreshRef.current = refresh;
    setAttempt((current) => current + 1);
  }, []);

  /** Shows a video as watched right away (the LMS may take a while to say so). */
  const markWatched = useCallback((moduleItemId: string) => {
    setWatchedHere((current) => (current.has(moduleItemId) ? current : new Set(current).add(moduleItemId)));
  }, []);

  /**
   * Asks the LMS again for a course (skipping the backend caches) without clearing the list.
   * What it answers replaces the "watched here" marks of that course's videos.
   */
  const refreshQuietly = useCallback(async (target: CourseSummary) => {
    try {
      const detail = await fetchCourseDetail(target.id, target.name, { refresh: true, outputDir: outputDirRef.current });
      const fresh = itemsOf(detail);
      const known = fresh.map(itemKey).filter((key): key is string => key !== null);
      setWatchedHere((current) => {
        if (!known.some((key) => current.has(key))) return current;
        const next = new Set(current);
        known.forEach((key) => next.delete(key));
        return next;
      });
      if (courseIdRef.current === String(target.id)) setItems(fresh);
    } catch (failure) {
      // A background refresh never interrupts; an expired login is noticed by the next request.
      if (isAuthenticationError(failure)) onAuthErrorRef.current(friendlyError(failure));
    }
  }, []);

  const shown = useMemo(
    () => (watchedHere.size === 0
      ? items
      : items.map((item) => {
        const key = itemKey(item);
        return item.kind === 'video' && item.completed !== true && key !== null && watchedHere.has(key) ? { ...item, completed: true } : item;
      })),
    [items, watchedHere],
  );

  return { items: shown, state, error, reload, markWatched, refreshQuietly };
}
