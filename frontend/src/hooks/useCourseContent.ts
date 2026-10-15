import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchCourseDetail, isAuthenticationError } from '../lib/api';
import { legacyLectureToContentItem } from '../lib/content';
import { friendlyError } from '../lib/errors';
import type { CourseContentItem, CourseSummary } from '../lib/types';

export type ContentState = 'idle' | 'loading' | 'ready' | 'error';

/** Lecture and material rows of the selected course; a newer selection discards older responses. */
export function useCourseContent(course: CourseSummary | null, onAuthError: (message: string) => void) {
  const [items, setItems] = useState<CourseContentItem[]>([]);
  const [state, setState] = useState<ContentState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const onAuthErrorRef = useRef(onAuthError);
  useEffect(() => {
    onAuthErrorRef.current = onAuthError;
  });

  const courseId = course?.id ?? null;
  const courseName = course?.name ?? '';

  useEffect(() => {
    setItems([]);
    setError(null);
    if (courseId === null) {
      setState('idle');
      return;
    }
    let cancelled = false;
    setState('loading');
    fetchCourseDetail(courseId, courseName)
      .then((detail) => {
        if (cancelled) return;
        setItems(detail.content_items?.length ? detail.content_items : detail.lectures.map(legacyLectureToContentItem));
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
  }, [courseId, courseName, attempt]);

  const reload = useCallback(() => setAttempt((current) => current + 1), []);

  return { items, state, error, reload };
}
