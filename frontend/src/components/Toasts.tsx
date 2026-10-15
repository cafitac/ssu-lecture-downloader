import type { Toasts as ToastState } from '../hooks/useToasts';
import { CloseIcon } from './Icons';

export function Toasts({ toasts, dismiss }: Pick<ToastState, 'toasts' | 'dismiss'>) {
  if (toasts.length === 0) return null;
  return (
    <div className="toasts" role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={`toast toast-${toast.kind}`}>
          <span>{toast.text}</span>
          <button type="button" className="btn btn-icon btn-ghost btn-sm" aria-label="닫기" onClick={() => dismiss(toast.id)}><CloseIcon /></button>
        </div>
      ))}
    </div>
  );
}
