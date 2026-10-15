import { useEffect, useRef } from 'react';
import { listen } from '@tauri-apps/api/event';
import { isTauriRuntime } from '../lib/desktop';

/**
 * Subscribe to a Tauri event for the lifetime of the component. `listen` is
 * async, so under React StrictMode the first effect can be cleaned up before
 * the listener is registered; the `disposed` flag unlistens such late
 * registrations immediately instead of leaking a duplicate handler.
 */
export function useTauriEvent<T>(eventName: string, handler: (payload: T) => void): void {
  const handlerRef = useRef(handler);
  useEffect(() => {
    handlerRef.current = handler;
  });

  useEffect(() => {
    if (!isTauriRuntime()) return;
    let disposed = false;
    let unlisten: (() => void) | null = null;
    listen<T>(eventName, (event) => {
      if (!disposed) handlerRef.current(event.payload);
    })
      .then((dispose) => {
        if (disposed) dispose();
        else unlisten = dispose;
      })
      .catch(() => {
        // Event bridge unavailable (browser/dev); nothing to clean up.
      });
    return () => {
      disposed = true;
      if (unlisten) unlisten();
      unlisten = null;
    };
  }, [eventName]);
}
