/**
 * Whether the UI knows of backend work that an app update would cut off. The backend has no
 * "list active jobs" endpoint, so this only counts what the UI tracks itself (design/00 G1).
 * The Rust `update_install_guard` covers the watch queue and native downloads separately.
 */
export interface TrackedWork {
  /** A download, conversion or subtitle job (single, batch or artifact) is starting or running. */
  downloadActive: boolean;
  /** Media components (ffmpeg, subtitle engine) are being installed. */
  componentsInstalling: boolean;
  /** A page attachment is being saved. */
  attachmentSaving: boolean;
}

export function isTrackedWorkBusy(work: TrackedWork): boolean {
  return work.downloadActive || work.componentsInstalling || work.attachmentSaving;
}

/**
 * The busy flag the App holds between workspaces. It survives a workspace unmount (a component
 * install may still run behind the login screen) but not a backend that is no longer ready:
 * no job survives a dead sidecar.
 */
export function busyAfterBackendStatus(held: boolean, backendState: 'starting' | 'ready' | 'failed'): boolean {
  return backendState === 'ready' ? held : false;
}

/** True when any file of any page is still being saved (`transfers` as `usePageAttachments` keeps them). */
export function anyTransferBusy(pages: Record<string, { transfers: Record<string, string> }>): boolean {
  return Object.values(pages).some((page) => Object.values(page.transfers).includes('busy'));
}
