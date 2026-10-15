import { useCallback, useEffect, useRef, useState } from 'react';
import { deleteCredentials, fetchCourses, fetchCredentialStatus, fetchTerms, isAuthenticationError, saveCredentials } from '../lib/api';
import { friendlyError } from '../lib/errors';
import type { CourseSummary, TermSummary } from '../lib/types';

/**
 * checking    - deciding whether the in-memory credentials still work
 * signed-out  - show the login screen
 * signed-in   - show the workspace
 */
export type SessionPhase = 'checking' | 'signed-out' | 'signed-in';
export type ListState = 'idle' | 'loading' | 'ready' | 'error';

/**
 * Login state plus the term/course list. The workspace is shown only after the
 * credentials were verified by a real LMS request (or accepted at login), so a
 * stale session never renders an empty course screen. Any authentication failure,
 * anywhere in the app, goes through `expire` and lands on the login screen.
 */
export function useLmsSession() {
  const [phase, setPhase] = useState<SessionPhase>('checking');
  const [notice, setNotice] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);
  const [terms, setTerms] = useState<TermSummary[]>([]);
  const [termId, setTermId] = useState<number | null>(null);
  const [courses, setCourses] = useState<CourseSummary[]>([]);
  const [listState, setListState] = useState<ListState>('idle');
  const [listError, setListError] = useState<string | null>(null);
  const requestRef = useRef(0);
  const startedRef = useRef(false);

  const reset = useCallback(() => {
    requestRef.current += 1;
    setTerms([]);
    setTermId(null);
    setCourses([]);
    setListState('idle');
    setListError(null);
  }, []);

  /** Back to the login screen, forgetting the stored credentials. */
  const expire = useCallback(async (message: string) => {
    reset();
    setPhase('signed-out');
    setNotice(message);
    try {
      await deleteCredentials();
    } catch {
      // The backend keeps credentials in memory only; the login screen is what matters.
    }
  }, [reset]);

  const loadCourses = useCallback(async (id: number | null, refresh = false) => {
    const request = ++requestRef.current;
    setListState('loading');
    setListError(null);
    try {
      const response = await fetchCourses(id, refresh);
      if (request !== requestRef.current) return;
      setCourses(response.courses);
      setListState('ready');
      setPhase('signed-in');
    } catch (error) {
      if (request !== requestRef.current) return;
      if (isAuthenticationError(error)) {
        await expire(friendlyError(error));
        return;
      }
      setListState('error');
      setListError(friendlyError(error, '과목 목록을 불러오지 못했어요.'));
      setPhase('signed-in');
    }
  }, [expire]);

  /** Current term first, then its courses. */
  const bootstrap = useCallback(async () => {
    let currentTermId: number | null = null;
    try {
      const response = await fetchTerms();
      setTerms(response.terms);
      currentTermId = response.terms.find((term) => term.is_current)?.term_id ?? null;
      setTermId(currentTermId);
    } catch (error) {
      if (isAuthenticationError(error)) {
        await expire(friendlyError(error));
        return;
      }
      setTerms([]);
    }
    await loadCourses(currentTermId);
  }, [expire, loadCourses]);

  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    void (async () => {
      let hasCredentials = false;
      try {
        const status = await fetchCredentialStatus();
        hasCredentials = status.username_present && status.password_present;
      } catch {
        hasCredentials = false;
      }
      if (!hasCredentials) {
        setPhase('signed-out');
        return;
      }
      await bootstrap();
    })();
  }, [bootstrap]);

  const signIn = useCallback(async (username: string, password: string) => {
    if (signingIn) return;
    setSigningIn(true);
    setNotice(null);
    try {
      await saveCredentials(username, password);
      await bootstrap();
    } catch (error) {
      setNotice(friendlyError(error, '로그인하지 못했어요. 잠시 후 다시 시도해 주세요.'));
      if (isAuthenticationError(error)) {
        try {
          await deleteCredentials();
        } catch {
          // Nothing was stored.
        }
      }
    } finally {
      setSigningIn(false);
    }
  }, [bootstrap, signingIn]);

  /** Returns an error message to show next to the button, or `null` on success. */
  const signOut = useCallback(async (): Promise<string | null> => {
    try {
      await deleteCredentials();
    } catch (error) {
      return friendlyError(error, '로그아웃하지 못했어요. 다시 시도해 주세요.');
    }
    reset();
    setNotice(null);
    setPhase('signed-out');
    return null;
  }, [reset]);

  const changeTerm = useCallback((id: number) => {
    setTermId(id);
    void loadCourses(id);
  }, [loadCourses]);

  // An explicit refresh skips the backend's LMS caches.
  const refresh = useCallback(() => {
    void loadCourses(termId, true);
  }, [loadCourses, termId]);

  return { phase, notice, signingIn, terms, termId, courses, listState, listError, signIn, signOut, expire, changeTerm, refresh };
}

export type LmsSession = ReturnType<typeof useLmsSession>;
