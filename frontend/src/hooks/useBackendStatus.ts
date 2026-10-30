import { useCallback, useEffect, useState } from 'react';
import { backendSupervisionStatus, resetBackendConnection, restartBackend } from '../lib/desktop';
import { startPolling } from '../lib/polling';
import type { BackendSupervisionStatus } from '../lib/types';

const STARTING: BackendSupervisionStatus = { state: 'starting', mode: 'checking', error: null, log_path: null };

/**
 * Tracks the Tauri-managed Python backend (starting / ready / failed) and
 * exposes a retry. Outside Tauri the backend is assumed ready immediately.
 */
export function useBackendStatus(): { status: BackendSupervisionStatus; retry: () => Promise<void>; refresh: () => void } {
  const [status, setStatus] = useState<BackendSupervisionStatus>(STARTING);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    return startPolling<BackendSupervisionStatus>({
      intervalMs: 500,
      initialDelayMs: 0,
      fetch: backendSupervisionStatus,
      isTerminal: (value) => value.state !== 'starting',
      onUpdate: (value) => {
        // Port/token change on every managed start, so never reuse a cached connection.
        resetBackendConnection();
        setStatus(value);
      },
      onError: (error) => {
        setStatus({
          state: 'failed',
          mode: 'unknown',
          error: error instanceof Error ? error.message : String(error),
          log_path: null,
        });
      },
    });
  }, [attempt]);

  const retry = useCallback(async () => {
    setStatus(STARTING);
    resetBackendConnection();
    try {
      await restartBackend();
    } catch (error) {
      setStatus({
        state: 'failed',
        mode: 'failed',
        error: error instanceof Error ? error.message : String(error),
        log_path: null,
      });
      return;
    }
    setAttempt((current) => current + 1);
  }, []);

  /** Reads the status again (the backend was restarted elsewhere, e.g. by the update recovery). */
  const refresh = useCallback(() => setAttempt((current) => current + 1), []);

  return { status, retry, refresh };
}
