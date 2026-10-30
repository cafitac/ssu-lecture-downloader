import { useCallback, useState } from 'react';
import { openDownloadedFolder, saveDiagnosticsFile } from '../lib/desktop';
import { friendlyError } from '../lib/errors';

export const DIAGNOSTICS_SAVED_TEXT = '진단 파일을 저장했어요. 이 파일을 전달해 주세요.';
export const DIAGNOSTICS_FAILED_TEXT = '진단 파일을 저장하지 못했어요. 다른 폴더를 골라 다시 시도해 주세요.';

export type DiagnosticsOutcome = { ok: true; path: string } | { ok: false; text: string };

/** Saves the diagnostics zip where the user picks; `outcome` stays `null` while idle or after a cancelled dialog. */
export function useDiagnosticsSave() {
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<DiagnosticsOutcome | null>(null);

  const save = useCallback(async () => {
    setBusy(true);
    setOutcome(null);
    try {
      const saved = await saveDiagnosticsFile();
      if (saved) setOutcome({ ok: true, path: saved.path });
    } catch (failure) {
      setOutcome({ ok: false, text: friendlyError(failure, DIAGNOSTICS_FAILED_TEXT) });
    } finally {
      setBusy(false);
    }
  }, []);

  const reveal = useCallback(async () => {
    if (outcome?.ok) {
      try {
        await openDownloadedFolder(outcome.path);
      } catch {
        // The file is saved either way; the folder just did not open.
      }
    }
  }, [outcome]);

  return { busy, outcome, save, reveal };
}
