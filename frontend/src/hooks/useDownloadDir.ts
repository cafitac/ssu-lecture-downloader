import { useCallback, useEffect, useState } from 'react';
import { defaultDownloadDirectory, pickDownloadDirectory } from '../lib/desktop';

const STORAGE_KEY = 'ssu.downloadDir';
const APP_FOLDER = 'SSU Lecture Downloader';
const FALLBACK = `~/Downloads/${APP_FOLDER}`;

function appFolderIn(downloads: string): string {
  return `${downloads.replace(/\/+$/, '')}/${APP_FOLDER}`;
}

function forget(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing remembered.
  }
}

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

/** Download folder: the one the user picked last time, otherwise an app folder inside the OS Downloads folder. */
export function useDownloadDir() {
  const [dir, setDir] = useState<string>(() => readStored() ?? FALLBACK);

  useEffect(() => {
    let cancelled = false;
    defaultDownloadDirectory()
      .then((path) => {
        if (cancelled || !path) return;
        const stored = readStored();
        // Older versions remembered the bare Downloads folder as the default; move those to the app folder.
        if (stored && stored.replace(/\/+$/, '') !== path.replace(/\/+$/, '')) return;
        if (stored) forget();
        setDir(appFolderIn(path));
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
