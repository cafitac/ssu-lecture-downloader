import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { createUpdateController } from '../lib/appUpdate.ts';
import type { UpdateApi, UpdateController, UpdateState } from '../lib/appUpdate.ts';
import { isTauriRuntime } from '../lib/desktop.ts';
import { tauriUpdateApi } from '../lib/updater.ts';

export interface AppUpdate {
  state: UpdateState;
  update: UpdateController['update'];
  later: UpdateController['later'];
}

/**
 * The app update notice: checks at launch and every 6 h while the app runs (desktop app only),
 * and offers the install flow. `workBusy` is whether the UI tracks running backend jobs.
 * It only exposes state; it never focuses, opens or announces anything by itself.
 */
export function useAppUpdate(workBusy: boolean, { api = tauriUpdateApi, onRecoveryAttempted }: { api?: UpdateApi; onRecoveryAttempted?: () => void } = {}): AppUpdate {
  const busyRef = useRef(workBusy);
  const restoredRef = useRef(onRecoveryAttempted);
  useEffect(() => {
    busyRef.current = workBusy;
    restoredRef.current = onRecoveryAttempted;
  });
  const [controller] = useState(() => createUpdateController({
    api,
    isWorkBusy: () => busyRef.current,
    onRecoveryAttempted: () => restoredRef.current?.(),
    log: (message, detail) => {
      if (detail === undefined) console.info(message);
      else console.warn(message, detail);
    },
  }));
  useEffect(() => {
    if (!isTauriRuntime()) return undefined;
    controller.start();
    return () => controller.stop();
  }, [controller]);
  const state = useSyncExternalStore(controller.subscribe, controller.getState);
  return { state, update: controller.update, later: controller.later };
}
