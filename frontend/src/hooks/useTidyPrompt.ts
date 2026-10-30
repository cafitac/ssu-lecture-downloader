import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchLegacySummary } from '../lib/api';
import { appVersion } from '../lib/desktop';
import { legacyCountOf, markTidyPromptAsked, readTidyPromptAsked, shouldShowTidyPrompt, wasTidyPromptAsked, writeTidyPromptAsked } from '../lib/tidyPrompt';
import type { TidyPromptAsked } from '../lib/tidyPrompt';

interface Options {
  /** The download folder, once it is final (`useDownloadDir().ready`); the summary is not read before. */
  dir: string;
  folderReady: boolean;
  /** Another dialog is open (problem prompt, update panel, settings, tidy-up, watch-all). */
  otherDialogOpen: boolean;
  /** A download, install or tidy-up is running. */
  busy: boolean;
}

/**
 * The once-per-folder-and-version "파일 정리" prompt. It reads the legacy-summary of the download
 * folder after login; a failed or unsupported summary means no prompt. A prompt that is due waits
 * while anything else is open or running, counts as asked the moment it shows, and steps aside
 * (without being used up again) if another dialog opens meanwhile.
 */
export function useTidyPrompt({ dir, folderReady, otherDialogOpen, busy }: Options) {
  const [version, setVersion] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ dir: string; count: number | null } | null>(null);
  // The folder whose prompt is open; it counts as asked from the moment it opened.
  const [openFor, setOpenFor] = useState<string | null>(null);
  const askedRef = useRef<TidyPromptAsked | null | undefined>(undefined);

  useEffect(() => {
    let cancelled = false;
    void appVersion().then((value) => { if (!cancelled) setVersion(value); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!folderReady || version === null) return undefined;
    let cancelled = false;
    fetchLegacySummary(dir)
      .then((result) => { if (!cancelled) setSummary({ dir, count: legacyCountOf(result) }); })
      .catch(() => { if (!cancelled) setSummary({ dir, count: null }); });
    return () => { cancelled = true; };
  }, [dir, folderReady, version]);

  const count = summary?.dir === dir ? summary.count : null;
  useEffect(() => {
    if (openFor === dir || version === null) return;
    if (askedRef.current === undefined) askedRef.current = readTidyPromptAsked();
    const asked = wasTidyPromptAsked(askedRef.current, version, dir);
    if (!shouldShowTidyPrompt({ count, asked, otherDialogOpen, busy })) return;
    askedRef.current = markTidyPromptAsked(askedRef.current, version, dir);
    writeTidyPromptAsked(askedRef.current);
    setOpenFor(dir);
  }, [busy, count, dir, openFor, otherDialogOpen, version]);

  const close = useCallback(() => setOpenFor(null), []);
  // A folder change while the prompt waits drops it; it is shown for the new folder if due.
  const visible = openFor === dir && count !== null && !otherDialogOpen && !busy;
  return { visible, count: count ?? 0, close };
}
