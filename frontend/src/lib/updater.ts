/**
 * The real plugin and command calls behind `UpdateApi` (see appUpdate.ts). This is the only
 * frontend file that touches the updater and process plugins and the two update commands; the
 * contract test pins that, and drives it through a mocked Tauri bridge.
 */
import { invoke } from '@tauri-apps/api/core';
import { relaunch } from '@tauri-apps/plugin-process';
import { check } from '@tauri-apps/plugin-updater';
import type { InstallGuard, PendingUpdate, UpdateApi } from './appUpdate.ts';
import { backendSupervisionStatus, resetBackendConnection, restartBackend } from './desktop.ts';
import { startPolling } from './polling.ts';
import type { BackendSupervisionStatus } from './types';

/** The Rust side gives a backend start this long (`BACKEND_STARTUP_TIMEOUT` in src-tauri/src/backend.rs). */
export const BACKEND_STARTUP_TIMEOUT_MS = 90_000;
/** The recovery waits for the whole startup bound plus a margin, so it never gives up on a start that is still allowed to finish. */
export const BACKEND_RECOVERY_TIMEOUT_MS = BACKEND_STARTUP_TIMEOUT_MS + 15_000;

export interface RecoveryOptions {
  /** How long to wait for the restarted backend to leave `starting`. */
  timeoutMs?: number;
  intervalMs?: number;
}

/** Polls the supervision status until it is no longer `starting`, or gives up after `timeoutMs`. */
export function waitForBackendSettled({ timeoutMs = BACKEND_RECOVERY_TIMEOUT_MS, intervalMs = 500 }: RecoveryOptions = {}): Promise<BackendSupervisionStatus | null> {
  return new Promise((resolve) => {
    let finished = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const done = (value: BackendSupervisionStatus | null) => {
      if (finished) return;
      finished = true;
      if (timer !== null) clearTimeout(timer);
      cancel();
      resolve(value);
    };
    const cancel = startPolling<BackendSupervisionStatus>({
      intervalMs,
      initialDelayMs: 0,
      fetch: backendSupervisionStatus,
      isTerminal: (value) => value.state !== 'starting',
      onUpdate: (value) => { if (value.state !== 'starting') done(value); },
    });
    timer = setTimeout(() => done(null), timeoutMs);
  });
}

/**
 * `backend_restart` after a failed install. The restarted sidecar has a new port and token, and a
 * request made while it is still starting would cache the old port with no token, so the cached
 * connection is dropped only once the backend has settled. Rejects unless it came back ready.
 */
export async function recoverBackend(options?: RecoveryOptions): Promise<void> {
  await restartBackend();
  const settled = await waitForBackendSettled(options);
  // Still `starting` at the bound: keep the cache untouched (a request now would cache the old port with no token).
  if (settled === null) throw new Error('backend was still starting after the recovery wait');
  resetBackendConnection();
  if (settled.state !== 'ready') throw new Error('backend did not come back after the restart');
}

export const tauriUpdateApi: UpdateApi = {
  async check(): Promise<PendingUpdate | null> {
    const update = await check();
    if (!update) return null;
    return {
      version: update.version,
      notes: update.body?.trim() ? update.body : null,
      async download(onProgress) {
        let downloaded = 0;
        let total: number | null = null;
        await update.download((event) => {
          if (event.event === 'Started') {
            total = typeof event.data.contentLength === 'number' ? event.data.contentLength : null;
          } else if (event.event === 'Progress') {
            downloaded += event.data.chunkLength;
          }
          onProgress({ downloaded, total });
        });
      },
      install: () => update.install(),
      close: () => update.close(),
    };
  },
  installGuard: () => invoke<InstallGuard>('update_install_guard'),
  prepare: () => invoke<void>('prepare_for_update'),
  restartBackend: () => recoverBackend(),
  relaunch,
};
