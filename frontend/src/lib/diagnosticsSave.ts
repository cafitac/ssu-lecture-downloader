import { friendlyError } from './errors.ts';
import type { SavedDiagnostics } from './types.ts';

export const DIAGNOSTICS_SAVED_TEXT = '진단 파일을 저장했어요. 이 파일을 전달해 주세요.';
export const DIAGNOSTICS_FAILED_TEXT = '진단 파일을 저장하지 못했어요. 다른 폴더를 골라 다시 시도해 주세요.';

export type DiagnosticsOutcome = { ok: true; path: string } | { ok: false; text: string };

export interface DiagnosticsSaveState {
  busy: boolean;
  outcome: DiagnosticsOutcome | null;
}

export const IDLE_DIAGNOSTICS_STATE: DiagnosticsSaveState = { busy: false, outcome: null };

/**
 * One save, as the sequence of states the UI goes through: busy at once, then idle with the result.
 * `saveFile` resolves to `null` when the user cancels the dialog (no result, no error).
 * `failedText` is the sentence for an error that has none of its own. Pure, so Node can load it in
 * the contract tests.
 */
export async function runDiagnosticsSave(
  saveFile: () => Promise<SavedDiagnostics | null>,
  emit: (state: DiagnosticsSaveState) => void,
  failedText: string = DIAGNOSTICS_FAILED_TEXT,
): Promise<void> {
  emit({ busy: true, outcome: null });
  try {
    const saved = await saveFile();
    emit({ busy: false, outcome: saved ? { ok: true, path: saved.path } : null });
  } catch (failure) {
    emit({ busy: false, outcome: { ok: false, text: friendlyError(failure, failedText) } });
  }
}
