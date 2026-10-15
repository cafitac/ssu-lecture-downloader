export function formatBytes(value: number | null | undefined): string {
  if (!value) return '-';
  const mb = value / 1024 / 1024;
  return `${mb.toFixed(1)} MB`;
}

export function formatDuration(value: number | null | undefined): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '-';
  const totalSeconds = Math.max(0, Math.round(value));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export function contentKindLabel(kind: string): string {
  switch (kind) {
    case 'video': return '영상';
    case 'material': return '자료';
    case 'assignment': return '과제';
    case 'quiz': return '퀴즈';
    case 'board': return '게시판';
    case 'page': return '페이지';
    case 'section': return '구분';
    default: return '기타';
  }
}

export function progressLabelPercent(percent: number): string {
  return `${Math.min(100, Math.max(0, percent))}%`;
}
