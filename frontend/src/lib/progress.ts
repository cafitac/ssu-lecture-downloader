import type { BatchDownloadResult, DownloadArtifactResult, DownloadResult } from './types';

export const TERMINAL_DOWNLOAD_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);

export function isActiveJobStatus(status: string | undefined | null): boolean {
  return Boolean(status) && !TERMINAL_DOWNLOAD_STATUSES.has(status as string);
}

type AnyDownloadResult = DownloadResult | BatchDownloadResult | DownloadArtifactResult | null | undefined;

export function isArtifactDownloadResult(result: AnyDownloadResult): result is DownloadArtifactResult {
  return Boolean(result && 'artifacts' in result && 'download' in result);
}

export function isBatchDownloadResult(result: AnyDownloadResult): result is BatchDownloadResult {
  return Boolean(result && !isArtifactDownloadResult(result) && 'items' in result);
}

/** MP3/TXT jobs wrap the download result; everything else already is one. */
export function downloadPayloadFromArtifact(result: AnyDownloadResult): DownloadResult | BatchDownloadResult | null | undefined {
  return isArtifactDownloadResult(result) ? result.download : result;
}
