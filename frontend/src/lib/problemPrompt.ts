/**
 * The app-level "문제가 생겼어요" prompt: when the app notices something went wrong it asks, once,
 * whether to save a diagnostics file. Nothing is uploaded; the file goes to a fixed folder.
 * Pure (no React, no Tauri), so Node can load it in the contract tests.
 */
import { DIAGNOSTICS_SAVED_TEXT, runDiagnosticsSave } from './diagnosticsSave.ts';
import type { DiagnosticsSaveState } from './diagnosticsSave.ts';
import type { ChromeWatchNotice, SavedDiagnostics, WatchQueueNotice } from './types.ts';

/** What was noticed. Each kind is asked at most once per app session. */
export type ProblemKind = 'chrome-watch' | 'chrome-skip' | 'app-watch' | 'backend' | 'previous-crash';

export const PROBLEM_TITLE = '문제가 생겼어요';
export const PROBLEM_HINT = '진단 파일을 저장해서 전달해 주시겠어요? 어디로도 자동 전송되지 않아요.';
export const PROBLEM_SAVED_TEXT = DIAGNOSTICS_SAVED_TEXT;
export const PROBLEM_SAVE_FAILED_TEXT = '진단 파일을 저장하지 못했어요. 설정 → 문제 보내기에서 다시 시도해 주세요.';

const SENTENCES: Record<ProblemKind, string> = {
  'chrome-watch': '자동 재생이 오류로 멈췄어요.',
  'chrome-skip': '재생이 멈춰서 강의를 건너뛰었어요.',
  'app-watch': '재생이 멈춰서 강의를 건너뛰었어요.',
  backend: '내부 서버가 멈췄어요.',
  'previous-crash': '지난번에 앱이 비정상적으로 종료됐어요.',
};

/** The one sentence about what happened. */
export function problemSentence(kind: ProblemKind): string {
  return SENTENCES[kind];
}

/** Run error codes of a stop the user caused by closing the Chrome window; that is no problem. */
const USER_CLOSED_CODES: ReadonlySet<string> = new Set(['chrome_window_closed']);

/**
 * Whether a notice of the Chrome-window watch is a problem worth asking about:
 * - `failed`: the run ended with an error, unless the login ran out (the login screen's business)
 *   or the user closed the Chrome window (`chrome_window_closed`). A lost Chrome or DevTools
 *   connection (`chrome_closed`) does count.
 * - `skipped`: only a lecture skipped because of a problem (the final stall, a plan error) carries
 *   a notice; the user's own skip has none.
 * - `stalled` does not: the first stall reloads the page and usually recovers; if it does not, the
 *   run then skips the lecture and that notice asks.
 * `errorCode` is the run's error code, which belongs to a `failed` notice only.
 */
export function chromeNoticeIsProblem(notice: Pick<ChromeWatchNotice, 'kind'>, authLost: boolean, errorCode: string | null = null): boolean {
  if (notice.kind === 'failed') return !authLost && !(errorCode !== null && USER_CLOSED_CODES.has(errorCode));
  return notice.kind === 'skipped';
}

/** The prompt kind of a Chrome notice that is a problem: a skip has its own sentence. */
export function chromeProblemKind(notice: Pick<ChromeWatchNotice, 'kind'>): ProblemKind {
  return notice.kind === 'skipped' ? 'chrome-skip' : 'chrome-watch';
}

/** Whether a notice of the in-app watch runtime reports a failed or skipped lecture. */
export function appWatchNoticeIsProblem(notice: Pick<WatchQueueNotice, 'kind' | 'auth_expired'>): boolean {
  return notice.kind === 'error' && !notice.auth_expired;
}

export interface ProblemPromptState {
  /** The kind whose dialog is open; `null` when none is. */
  kind: ProblemKind | null;
}

export interface ProblemPromptController {
  getState: () => ProblemPromptState;
  subscribe: (listener: () => void) => () => void;
  /**
   * Something went wrong. Opens the dialog unless this kind was already asked this session or a
   * dialog is open (a newcomer is dropped, not queued, and does not use up its once). Returns
   * whether the dialog opened. With `wait`, a report that finds a dialog open is kept and shown as
   * soon as that one closes (for a one-shot signal that cannot be asked for again).
   */
  report: (kind: ProblemKind, options?: { wait?: boolean }) => boolean;
  /** 닫기: the dialog goes away; the kind stays asked. */
  close: () => void;
}

/** One controller per app session: it remembers which kinds were asked. */
export function createProblemPromptController(): ProblemPromptController {
  let state: ProblemPromptState = { kind: null };
  const asked = new Set<ProblemKind>();
  const pending: ProblemKind[] = [];
  const listeners = new Set<() => void>();
  const set = (next: ProblemPromptState) => {
    state = next;
    listeners.forEach((listener) => listener());
  };
  return {
    getState: () => state,
    subscribe: (listener) => {
      listeners.add(listener);
      return () => { listeners.delete(listener); };
    },
    report: (kind, options) => {
      if (asked.has(kind)) return false;
      if (state.kind !== null) {
        if (options?.wait && !pending.includes(kind) && state.kind !== kind) pending.push(kind);
        return false;
      }
      asked.add(kind);
      set({ kind });
      return true;
    },
    close: () => {
      if (state.kind === null) return;
      const next = pending.shift();
      if (next !== undefined) asked.add(next);
      set({ kind: next ?? null });
    },
  };
}

export interface AutoSaveDeps {
  /** Writes the zip to the fixed folder (`~/Downloads/SSU 진단/ssu-진단-<yyyyMMdd-HHmm>.zip`). */
  save: () => Promise<SavedDiagnostics | null>;
  /** Shows the file in Finder. */
  reveal: (path: string) => Promise<void>;
}

/**
 * [진단 파일 저장]: save without a save dialog, then reveal the file. The states are the same
 * busy → result sequence as the Settings button. A Finder that does not open is no failure: the
 * file is saved either way.
 */
export function runProblemSave({ save, reveal }: AutoSaveDeps, emit: (state: DiagnosticsSaveState) => void): Promise<void> {
  return runDiagnosticsSave(async () => {
    const saved = await save();
    if (saved) {
      try {
        await reveal(saved.path);
      } catch {
        // Saved; only the folder did not open.
      }
    }
    return saved;
  }, emit, PROBLEM_SAVE_FAILED_TEXT);
}
