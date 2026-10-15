const KB = 1024;
const MB = KB * 1024;
const GB = MB * 1024;

/** Compact size such as `340MB` or `7.0GB`; `-` when the size is unknown. */
export function formatBytes(value: number | null | undefined): string {
  if (!value || value < 0) return '-';
  if (value >= GB) return `${(value / GB).toFixed(1)}GB`;
  if (value >= MB) return `${(value / MB).toFixed(value >= 100 * MB ? 0 : 1)}MB`;
  return `${Math.max(1, Math.round(value / KB))}KB`;
}

/** `m:ss` or `h:mm:ss`; `-` when the length is unknown. */
export function formatDuration(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '-';
  const totalSeconds = Math.round(value);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

/** Last path segment, e.g. `/Users/me/Downloads` -> `Downloads`. */
export function shortFolderName(path: string): string {
  const trimmed = path.trim().replace(/[\\/]+$/, '');
  if (!trimmed) return '폴더';
  const segment = trimmed.split(/[\\/]/).pop();
  return segment && segment !== '~' ? segment : trimmed;
}
