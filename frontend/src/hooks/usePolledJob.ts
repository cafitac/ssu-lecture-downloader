import { useEffect, useRef } from 'react';
import { DEFAULT_POLL_INTERVAL_MS, startPolling } from '../lib/polling';

export interface PolledJobOptions<T extends { job_id: string }> {
  fetch: (jobId: string) => Promise<T>;
  isTerminal: (job: T) => boolean;
  /** Receives only responses for the job currently being polled. */
  onUpdate: (job: T) => void | Promise<void>;
  onError?: (error: unknown) => void | Promise<void>;
  intervalMs?: number;
}

/**
 * Poll one background job until it reaches a terminal status. Polling restarts
 * only when the job id changes or the job becomes terminal; callbacks are read
 * through a ref so they are never stale.
 */
export function usePolledJob<T extends { job_id: string }>(job: T | null, options: PolledJobOptions<T>): void {
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  });

  const jobId = job?.job_id ?? null;
  const polling = job !== null && !options.isTerminal(job);

  useEffect(() => {
    if (!jobId || !polling) return;
    return startPolling<T>({
      intervalMs: optionsRef.current.intervalMs ?? DEFAULT_POLL_INTERVAL_MS,
      fetch: () => optionsRef.current.fetch(jobId),
      isTerminal: (value) => optionsRef.current.isTerminal(value),
      onUpdate: (value) => {
        // Job-id guard: a response for a different job must never overwrite state.
        if (value.job_id !== jobId) return;
        return optionsRef.current.onUpdate(value);
      },
      onError: (error) => optionsRef.current.onError?.(error),
    });
  }, [jobId, polling]);
}
