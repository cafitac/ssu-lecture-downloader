/**
 * The one-time "예전 방식 파일을 정리할까요?" dialog (TIDY-PROMPT): after login the app asks once per
 * download folder and app version when old-style files are found. Pure (no React, no Tauri), so Node
 * can load it in the contract tests.
 */

export const TIDY_PROMPT_TITLE = '파일 정리';
export const tidyPromptSentence = (count: number) => `예전 방식으로 저장된 파일이 ${count}개 있어요. 새 폴더 구조(과목/주차/강의)로 정리할까요?`;

export const TIDY_PROMPT_STORAGE_KEY = 'ssu.tidyPromptAsked';

/** Which folders were asked in which app version; a new version starts the list over. */
export interface TidyPromptAsked {
  version: string;
  folders: string[];
}

/** The file count of a legacy-summary answer; `null` for anything that is not a usable answer. */
export function legacyCountOf(summary: unknown): number | null {
  const count = (summary as { legacy_file_count?: unknown } | null)?.legacy_file_count;
  return typeof count === 'number' && Number.isFinite(count) && count >= 0 ? count : null;
}

export function wasTidyPromptAsked(asked: TidyPromptAsked | null, version: string, folder: string): boolean {
  return asked !== null && asked.version === version && asked.folders.includes(folder);
}

export function markTidyPromptAsked(asked: TidyPromptAsked | null, version: string, folder: string): TidyPromptAsked {
  const same = asked !== null && asked.version === version;
  const folders = same ? asked.folders : [];
  return { version, folders: folders.includes(folder) ? folders : [...folders, folder] };
}

export interface TidyPromptInput {
  /** Old-style files in the download folder; `null` while unknown or when the summary failed or is unsupported. */
  count: number | null;
  asked: boolean;
  /** Another dialog (problem prompt, update panel, settings, tidy-up, watch-all) is open. */
  otherDialogOpen: boolean;
  /** A download, install or tidy-up is running. */
  busy: boolean;
}

/** Ask only when there is something to tidy, it was not asked for this version + folder, and nothing else is going on. */
export function shouldShowTidyPrompt({ count, asked, otherDialogOpen, busy }: TidyPromptInput): boolean {
  return count !== null && count > 0 && !asked && !otherDialogOpen && !busy;
}

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function defaultStorage(): StorageLike | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    return null;
  }
}

export function readTidyPromptAsked(storage: StorageLike | null = defaultStorage()): TidyPromptAsked | null {
  try {
    const parsed: unknown = JSON.parse(storage?.getItem(TIDY_PROMPT_STORAGE_KEY) ?? 'null');
    if (typeof parsed !== 'object' || parsed === null) return null;
    const { version, folders } = parsed as Partial<TidyPromptAsked>;
    return typeof version === 'string' && Array.isArray(folders) && folders.every((folder) => typeof folder === 'string') ? { version, folders } : null;
  } catch {
    return null;
  }
}

export function writeTidyPromptAsked(asked: TidyPromptAsked, storage: StorageLike | null = defaultStorage()): void {
  try {
    storage?.setItem(TIDY_PROMPT_STORAGE_KEY, JSON.stringify(asked));
  } catch {
    // Remembering is a convenience; without it the next launch asks again.
  }
}
