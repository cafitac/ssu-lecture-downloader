import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchLmsSessionBridge, isAuthenticationError } from '../lib/api';
import { lmsItemUrl } from '../lib/content';
import { isTauriRuntime, openLmsPageWindow, openUrlInBrowser } from '../lib/desktop';
import { friendlyError } from '../lib/errors';

interface Options {
  notify: (message: string, kind?: 'info' | 'success' | 'error') => void;
  onAuthError: (message: string) => void;
}

/**
 * Opens LMS pages (wiki pages, boards, assignments, quizzes) in a normal app window that
 * carries the LMS session, so the user does not have to sign in again in the browser. The
 * address is checked here and again by the desktop shell (https on the Soongsil domain only).
 * "브라우저로 열기" stays available as a secondary action.
 */
export function useLmsPages({ notify, onAuthError }: Options) {
  const [openingUrl, setOpeningUrl] = useState<string | null>(null);
  const busyRef = useRef(false);
  const optionsRef = useRef({ notify, onAuthError });
  useEffect(() => {
    optionsRef.current = { notify, onAuthError };
  });

  const openInBrowser = useCallback(async (address: string | null) => {
    const url = lmsItemUrl(address);
    if (!url) {
      optionsRef.current.notify('LMS 주소를 알 수 없어요.', 'error');
      return;
    }
    try {
      await openUrlInBrowser(url);
    } catch (error) {
      optionsRef.current.notify(friendlyError(error, 'LMS 페이지를 열지 못했어요.'), 'error');
    }
  }, []);

  const openInApp = useCallback(async (title: string, address: string | null) => {
    const url = lmsItemUrl(address);
    if (!url) {
      optionsRef.current.notify('LMS 주소를 알 수 없어요.', 'error');
      return;
    }
    if (!isTauriRuntime()) {
      // Browser/Vite mode has no app window: the normal browser is the only place to open it.
      await openInBrowser(url);
      return;
    }
    if (busyRef.current) return;
    busyRef.current = true;
    setOpeningUrl(url);
    try {
      const bridge = await fetchLmsSessionBridge();
      await openLmsPageWindow({ title: title.trim() || '숭실 LMS', url, cookies: bridge.cookies });
    } catch (error) {
      if (isAuthenticationError(error)) optionsRef.current.onAuthError(friendlyError(error));
      else optionsRef.current.notify(friendlyError(error, 'LMS 페이지를 열지 못했어요.'), 'error');
    } finally {
      busyRef.current = false;
      setOpeningUrl(null);
    }
  }, [openInBrowser]);

  return { openingUrl, openInApp, openInBrowser };
}

export type LmsPages = ReturnType<typeof useLmsPages>;
