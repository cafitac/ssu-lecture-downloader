import { useCallback, useEffect, useState } from 'react';
import { defaultDownloadDirectory, pickDownloadDirectory } from '../lib/desktop';

const STORAGE_KEY = 'ssu.downloadDir';
const FALLBACK = '~/Downloads';

function readStored(): string | null {
  try {
    return window.localStorage.getItem(STORAGE_KEY) || null;
  } catch {
    return null;
  }
}

function store(path: string): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, path);
  } catch {
    // Remembering the folder is a convenience only.
  }
}

/** Download folder: the one the user picked last time, otherwise the OS Downloads folder. */
export function useDownloadDir() {
  const [dir, setDir] = useState<string>(() => readStored() ?? FALLBACK);

  useEffect(() => {
    if (readStored()) return;
    let cancelled = false;
    defaultDownloadDirectory()
      .then((path) => {
        if (!cancelled && path) setDir(path);
      })
      .catch(() => {
        // Keep the fallback.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  /** Opens the native folder picker; resolves `true` when a folder was chosen. */
  const pick = useCallback(async (): Promise<boolean> => {
    const selected = await pickDownloadDirectory();
    if (!selected) return false;
    setDir(selected);
    store(selected);
    return true;
  }, []);

  return { dir, pick };
}
