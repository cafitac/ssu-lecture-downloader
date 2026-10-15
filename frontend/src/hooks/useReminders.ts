import { useCallback, useEffect, useState } from 'react';
import { remindersStatus, sendTestReminder, setRemindersEnabled } from '../lib/desktop';
import { friendlyError } from '../lib/errors';
import type { RemindersStatus } from '../lib/types';

/** Deadline-reminder preference and state, read from the desktop shell. */
export function useReminders(enabled: boolean) {
  const [status, setStatus] = useState<RemindersStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [testSent, setTestSent] = useState(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    remindersStatus()
      .then((next) => {
        if (!cancelled) setStatus(next);
      })
      .catch((failure: unknown) => {
        if (!cancelled) setError(friendlyError(failure, '알림 설정을 불러오지 못했어요.'));
      });
    return () => {
      cancelled = true;
    };
  }, [enabled]);

  const setOn = useCallback(async (on: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setStatus(await setRemindersEnabled(on));
    } catch (failure) {
      setError(friendlyError(failure, '알림 설정을 바꾸지 못했어요.'));
    } finally {
      setBusy(false);
    }
  }, []);

  const sendTest = useCallback(async () => {
    setError(null);
    setTestSent(false);
    try {
      await sendTestReminder();
      setTestSent(true);
    } catch (failure) {
      setError(friendlyError(failure, '알림을 보내지 못했어요.'));
    }
  }, []);

  return { status, busy, error, testSent, setOn, sendTest };
}
