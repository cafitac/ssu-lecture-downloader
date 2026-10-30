import { useCallback, useState } from 'react';
import { openDownloadedFolder, saveDiagnosticsFile } from '../lib/desktop';
import { IDLE_DIAGNOSTICS_STATE, runDiagnosticsSave } from '../lib/diagnosticsSave';
import type { DiagnosticsSaveState } from '../lib/diagnosticsSave';

/** Saves the diagnostics zip where the user picks; `outcome` stays `null` while idle or after a cancelled dialog. */
export function useDiagnosticsSave() {
  const [state, setState] = useState<DiagnosticsSaveState>(IDLE_DIAGNOSTICS_STATE);

  const save = useCallback(() => runDiagnosticsSave(() => saveDiagnosticsFile(), setState), []);

  const reveal = useCallback(async () => {
    if (state.outcome?.ok) {
      try {
        await openDownloadedFolder(state.outcome.path);
      } catch {
        // The file is saved either way; the folder just did not open.
      }
    }
  }, [state.outcome]);

  return { busy: state.busy, outcome: state.outcome, save, reveal };
}
