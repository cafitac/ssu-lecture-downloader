/**
 * The real plugin and command calls behind `UpdateApi` (see appUpdate.ts). This is the only
 * frontend file that touches the updater and process plugins and the two update commands; the
 * contract test pins that, and drives it through a mocked Tauri bridge.
 */
import { invoke } from '@tauri-apps/api/core';
import { relaunch } from '@tauri-apps/plugin-process';
import { check } from '@tauri-apps/plugin-updater';
import type { InstallGuard, PendingUpdate, UpdateApi } from './appUpdate.ts';
import { restartBackend } from './desktop.ts';

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
  restartBackend,
  relaunch,
};
