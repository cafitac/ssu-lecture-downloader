export interface PollOptions<T> {
  fetch: () => Promise<T>;
  /** Called with each response, only while the poller is still active. */
  onUpdate: (value: T) => void | Promise<void>;
  onError?: (error: unknown) => void | Promise<void>;
  /** Polling stops after the first terminal value. */
  isTerminal: (value: T) => boolean;
  intervalMs?: number;
  /** Delay before the first request (defaults to `intervalMs`). */
  initialDelayMs?: number;
}

export const DEFAULT_POLL_INTERVAL_MS = 1500;

/**
 * Poll with a `setTimeout` chain: the next request is scheduled only after the
 * previous one (and its `onUpdate`) finished, so responses can never overlap or
 * arrive out of order. After the returned `cancel()` runs, no further request
 * is made and a response that was already in flight is dropped without calling
 * `onUpdate`/`onError`, so callers never write stale state.
 */
export function startPolling<T>(options: PollOptions<T>): () => void {
  const intervalMs = options.intervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  let active = true;
  let timer: ReturnType<typeof setTimeout> | null = null;

  const schedule = (delayMs: number) => {
    if (!active) return;
    timer = setTimeout(() => {
      timer = null;
      void tick();
    }, delayMs);
  };

  const report = async (error: unknown) => {
    if (!active || !options.onError) return;
    try {
      await options.onError(error);
    } catch {
      // A failing error handler must not break the polling chain.
    }
  };

  const tick = async () => {
    if (!active) return;
    let value: T;
    try {
      value = await options.fetch();
    } catch (error) {
      await report(error);
      schedule(intervalMs);
      return;
    }
    if (!active) return;
    try {
      await options.onUpdate(value);
    } catch (error) {
      await report(error);
    }
    if (!active || options.isTerminal(value)) return;
    schedule(intervalMs);
  };

  schedule(options.initialDelayMs ?? intervalMs);
  return () => {
    active = false;
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
  };
}
