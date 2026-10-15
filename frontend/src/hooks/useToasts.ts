import { useCallback, useEffect, useRef, useState } from 'react';

export type ToastKind = 'info' | 'success' | 'error';

export interface Toast {
  id: number;
  kind: ToastKind;
  text: string;
}

const LIFETIME_MS: Record<ToastKind, number> = { info: 4000, success: 4000, error: 8000 };

/** Short-lived messages for things that happen away from the control the user used. */
export function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
    setToasts((current) => current.filter((toast) => toast.id !== id));
  }, []);

  const push = useCallback((text: string, kind: ToastKind = 'info') => {
    const id = nextId.current++;
    // Identical message already visible: keep one copy.
    setToasts((current) => (current.some((toast) => toast.text === text) ? current : [...current.slice(-2), { id, kind, text }]));
    timers.current.set(id, setTimeout(() => dismiss(id), LIFETIME_MS[kind]));
  }, [dismiss]);

  useEffect(() => {
    const active = timers.current;
    return () => {
      active.forEach((timer) => clearTimeout(timer));
      active.clear();
    };
  }, []);

  return { toasts, push, dismiss };
}

export type Toasts = ReturnType<typeof useToasts>;
