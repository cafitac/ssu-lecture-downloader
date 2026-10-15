import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchDeadlines, isAuthenticationError } from '../lib/api';
import { friendlyError } from '../lib/errors';
import type { CourseSummary, DeadlineEntryDto } from '../lib/types';

export type DeadlinesState = 'idle' | 'loading' | 'ready' | 'error';

/**
 * Deadlines of the term across all courses. It reloads whenever the course list does; the
 * backend builds the answer from the module lists the course list just fetched, so this
 * costs no extra LMS request.
 */
export function useDeadlines(termId: number | null, courses: CourseSummary[], onAuthError: (message: string) => void) {
  const [items, setItems] = useState<DeadlineEntryDto[]>([]);
  const [state, setState] = useState<DeadlinesState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  const onAuthErrorRef = useRef(onAuthError);
  useEffect(() => {
    onAuthErrorRef.current = onAuthError;
  });

  useEffect(() => {
    if (courses.length === 0) {
      setItems([]);
      setState('idle');
      return;
    }
    let cancelled = false;
    setState('loading');
    setError(null);
    fetchDeadlines(termId)
      .then((response) => {
        if (cancelled) return;
        setItems(response.deadlines);
        setState('ready');
      })
      .catch((failure: unknown) => {
        if (cancelled) return;
        if (isAuthenticationError(failure)) {
          onAuthErrorRef.current(friendlyError(failure));
          return;
        }
        setError(friendlyError(failure, '기한을 불러오지 못했어요.'));
        setState('error');
      });
    return () => {
      cancelled = true;
    };
  }, [termId, courses, attempt]);

  const reload = useCallback(() => setAttempt((current) => current + 1), []);

  return { items, state, error, reload };
}
