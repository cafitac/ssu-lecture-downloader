import { useCallback, useEffect, useState } from 'react';
import { checkRemoteStt, fetchRemoteSttStatus, saveRemoteSttConfig } from '../lib/api';
import { friendlyError } from '../lib/errors';
import { describeRemoteCheck } from '../lib/remoteStt';
import type { RemoteSttConfigPatch, RemoteSttStatus } from '../lib/types';

export interface RemoteCheckOutcome {
  ok: boolean;
  text: string;
}

/** Remote subtitle server settings: load, save and "연결 확인". Failures are shown as text, never thrown. */
export function useRemoteStt() {
  const [status, setStatus] = useState<RemoteSttStatus | null>(null);
  const [busy, setBusy] = useState<'saving' | 'checking' | null>(null);
  const [outcome, setOutcome] = useState<RemoteCheckOutcome | null>(null);

  const load = useCallback(async () => {
    try {
      setStatus(await fetchRemoteSttStatus());
    } catch {
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /** Returns true when the settings were saved. */
  const save = useCallback(async (patch: RemoteSttConfigPatch): Promise<boolean> => {
    setBusy('saving');
    setOutcome(null);
    try {
      setStatus(await saveRemoteSttConfig(patch));
      return true;
    } catch (error) {
      setOutcome({ ok: false, text: friendlyError(error, '설정을 저장하지 못했어요.') });
      return false;
    } finally {
      setBusy(null);
    }
  }, []);

  const check = useCallback(async () => {
    setBusy('checking');
    setOutcome(null);
    try {
      const next = await checkRemoteStt();
      setStatus(next);
      setOutcome(describeRemoteCheck(next));
    } catch (error) {
      setOutcome({ ok: false, text: friendlyError(error, '연결을 확인하지 못했어요.') });
    } finally {
      setBusy(null);
    }
  }, []);

  return { status, busy, outcome, enabled: status?.enabled === true, load, save, check };
}

export type RemoteStt = ReturnType<typeof useRemoteStt>;
