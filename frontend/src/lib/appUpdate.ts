/**
 * App update lifecycle (design/05 update-check-and-install, 06 update-ui-state-basic).
 *
 * Framework-free and in memory only: the React hook subscribes to it, and the tests drive it
 * with a mocked plugin API. It never touches the DOM, so an update notice cannot steal focus,
 * open a window or make a sound; it only changes this state, which the top bar shows as a chip.
 *
 * Install order after the user's click, each step able to stop the flow:
 *   busy check (tracked jobs + Rust guard) -> download -> guard again -> prepare_for_update
 *   -> install -> relaunch.
 * Once `prepare_for_update` has returned Ok the sidecar is stopped, so the only way out is the
 * install result: a failure restarts the backend (`backend_restart`) and shows a retry.
 */
import { formatBytes } from './format.ts';

/** How often the app asks for a new release. */
export const CHECK_INTERVAL_MS = 6 * 60 * 60 * 1000;
/** How long "나중에" hides the chip. */
export const LATER_HIDE_MS = 24 * 60 * 60 * 1000;

export const BUSY_NOTICE = '작업이 끝난 뒤 다시 시도해 주세요';
export const DOWNLOAD_FAILED = '업데이트를 내려받지 못했어요. 인터넷 연결을 확인하고 다시 시도해 주세요.';
export const PREPARE_FAILED = '업데이트를 준비하지 못했어요. 다시 시도해 주세요.';
export const BACKEND_STARTING = '앱이 아직 시작하는 중이에요. 잠시 후 다시 시도해 주세요.';
export const INSTALL_FAILED = '업데이트를 설치하지 못했어요. 지금 쓰던 버전은 그대로 쓸 수 있어요. 다시 시도해 주세요.';
export const INSTALL_FAILED_RESTART = '업데이트를 설치하지 못했어요. 앱을 한 번 닫았다가 다시 열어 주세요.';
export const RELAUNCH_FAILED = '업데이트는 설치됐어요. 앱을 닫았다가 다시 열면 새 버전으로 시작해요.';
export const NO_NOTES = '변경 내용이 따로 적혀 있지 않아요.';

export interface DownloadProgress {
  downloaded: number;
  /** Unknown when the server sends no length. */
  total: number | null;
}

/** An update announced by the release feed, as the plugin hands it over. */
export interface PendingUpdate {
  version: string;
  notes: string | null;
  download(onProgress: (progress: DownloadProgress) => void): Promise<void>;
  install(): Promise<void>;
  /** Frees what the plugin holds for this update. */
  close(): Promise<void>;
}

export type InstallGuard = { status: 'allowed' } | { status: 'refused'; reason?: string };

/** Everything the controller needs from the plugins and the Rust commands (mocked in tests). */
export interface UpdateApi {
  check(): Promise<PendingUpdate | null>;
  /** `update_install_guard` */
  installGuard(): Promise<InstallGuard>;
  /** `prepare_for_update`: rejects with `{ code, reason? }` when it did not stop the sidecar. */
  prepare(): Promise<void>;
  /**
   * `backend_restart`, then waits until the backend has settled and forgets the cached connection
   * (port and token change). Rejects when the backend did not come back ready.
   */
  restartBackend(): Promise<void>;
  relaunch(): Promise<void>;
}

export type UpdatePhase =
  | 'idle'
  | 'checking'
  | 'available'
  | 'downloading'
  | 'guarding'
  | 'installing'
  | 'relaunching'
  | 'failed';

export interface UpdateState {
  phase: UpdatePhase;
  version: string | null;
  notes: string | null;
  /** "나중에" was chosen: the chip stays hidden until the 24 h are over. */
  hidden: boolean;
  progress: DownloadProgress | null;
  /** Shown in the panel when the update has to wait for running work. */
  notice: string | null;
  error: string | null;
  /** Whether the failed state offers "다시 시도". */
  canRetry: boolean;
}

export const INITIAL_UPDATE_STATE: UpdateState = {
  phase: 'idle',
  version: null,
  notes: null,
  hidden: false,
  progress: null,
  notice: null,
  error: null,
  canRetry: false,
};

export interface TimerApi {
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
  setInterval(callback: () => void, ms: number): unknown;
  clearInterval(handle: unknown): void;
}

export interface UpdateControllerDeps {
  api: UpdateApi;
  /** True while a tracked backend job (download, convert, subtitles, install of components) runs. */
  isWorkBusy: () => boolean;
  timers?: TimerApi;
  log?: (message: string, detail?: unknown) => void;
}

const REAL_TIMERS: TimerApi = {
  setTimeout: (callback, ms) => globalThis.setTimeout(callback, ms),
  clearTimeout: (handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>),
  setInterval: (callback, ms) => globalThis.setInterval(callback, ms),
  clearInterval: (handle) => globalThis.clearInterval(handle as ReturnType<typeof setInterval>),
};

type Listener = () => void;

/** `{ code }` of a rejected `prepare_for_update`; anything else counts as unknown. */
function prepareErrorCode(error: unknown): string | null {
  if (typeof error === 'object' && error !== null && 'code' in error) {
    const code = (error as { code: unknown }).code;
    if (typeof code === 'string') return code;
  }
  return null;
}

export function createUpdateController({ api, isWorkBusy, timers = REAL_TIMERS, log = () => {} }: UpdateControllerDeps) {
  let state: UpdateState = INITIAL_UPDATE_STATE;
  let pending: PendingUpdate | null = null;
  let running = false;
  let flowInFlight = false;
  let checkTimer: unknown = null;
  let laterTimer: unknown = null;
  const listeners = new Set<Listener>();

  function set(patch: Partial<UpdateState>) {
    state = { ...state, ...patch };
    listeners.forEach((listener) => listener());
  }

  function release(update: PendingUpdate | null) {
    if (update) void update.close().catch(() => {});
  }

  /** Asks for a new release. Only from Idle: one check or install in flight, never during a dismissal. */
  async function check(): Promise<void> {
    if (state.phase !== 'idle') return;
    set({ phase: 'checking' });
    try {
      const found = await api.check();
      if (!found) {
        log('updater: no newer version');
        set({ phase: 'idle' });
        return;
      }
      log(`updater: version ${found.version} is available`);
      release(pending);
      pending = found;
      set({ phase: 'available', version: found.version, notes: found.notes, hidden: false, progress: null, notice: null, error: null, canRetry: false });
    } catch (error) {
      // Offline, no release yet, rate limit: nothing to show. The next check is in 6 h.
      log('updater: check failed', error);
      set({ phase: 'idle' });
    }
  }

  function fail(error: string, canRetry = true) {
    set({ phase: 'failed', error, canRetry, progress: null, notice: null });
  }

  /** The update has to wait: back to the plain "available" panel with the reason. */
  function wait() {
    set({ phase: 'available', progress: null, notice: BUSY_NOTICE, error: null, canRetry: false });
  }

  /**
   * The guard refused after the download: free the downloaded bytes (close), ask again for the
   * update and go back to Available (design/05, 06). When the new check finds nothing, the next
   * click asks again first.
   */
  async function discardAndWait(update: PendingUpdate) {
    release(update);
    pending = null;
    try {
      const again = await api.check();
      if (again) {
        pending = again;
        set({ version: again.version, notes: again.notes });
      }
    } catch (error) {
      log('updater: re-check after the guard refused failed', error);
    }
    wait();
  }

  /** The update to install: the held one, or a fresh one after its bytes were discarded. */
  async function currentUpdate(): Promise<PendingUpdate | null> {
    if (pending) return pending;
    try {
      pending = await api.check();
    } catch (error) {
      log('updater: check before the install failed', error);
    }
    if (pending) set({ version: pending.version, notes: pending.notes });
    return pending;
  }

  /** True when the update may go on: no tracked job and the Rust guard allows. */
  async function mayInstall(): Promise<boolean | 'error'> {
    if (isWorkBusy()) return false;
    try {
      const guard = await api.installGuard();
      return guard.status === 'allowed';
    } catch (error) {
      log('updater: install guard failed', error);
      return 'error';
    }
  }

  async function runInstall(update: PendingUpdate): Promise<void> {
    set({ phase: 'guarding', notice: null, error: null, canRetry: false, progress: null });
    const first = await mayInstall();
    if (first === 'error') return fail(PREPARE_FAILED);
    if (!first) return wait();

    set({ phase: 'downloading', progress: { downloaded: 0, total: null } });
    try {
      await update.download((progress) => set({ progress }));
    } catch (error) {
      log('updater: download failed', error);
      return fail(DOWNLOAD_FAILED);
    }

    // Work may have started while the bytes came in; the downloaded bytes are dropped by the next try.
    set({ phase: 'guarding', progress: null });
    const second = await mayInstall();
    if (second === 'error') return fail(PREPARE_FAILED);
    if (!second) return discardAndWait(update);

    set({ phase: 'installing' });
    try {
      await api.prepare();
    } catch (error) {
      const code = prepareErrorCode(error);
      log('updater: prepare failed', error);
      if (code === 'refused') return discardAndWait(update);
      if (code === 'backend_starting') return fail(BACKEND_STARTING);
      if (code === 'backend_unavailable') return fail(PREPARE_FAILED);
      // Unknown failure: it is not certain nothing was stopped, so bring the backend back.
      return (await restoreBackend()) ? fail(PREPARE_FAILED) : fail(INSTALL_FAILED_RESTART, false);
    }

    // From here the sidecar is stopped. Only the install result decides what happens next.
    try {
      await update.install();
    } catch (error) {
      log('updater: install failed', error);
      const restored = await restoreBackend();
      // The "old version keeps working" copy only once the backend is really back; otherwise the
      // copy asks to reopen the app, and no retry is offered next to it.
      return restored ? fail(INSTALL_FAILED) : fail(INSTALL_FAILED_RESTART, false);
    }

    set({ phase: 'relaunching' });
    try {
      await api.relaunch();
    } catch (error) {
      log('updater: relaunch failed', error);
      fail(RELAUNCH_FAILED, false);
    }
  }

  async function restoreBackend(): Promise<boolean> {
    try {
      await api.restartBackend();
      return true;
    } catch (error) {
      log('updater: backend restart failed', error);
      return false;
    }
  }

  return {
    getState: (): UpdateState => state,
    subscribe(listener: Listener): () => void {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },

    /** Checks now (app launch) and then every 6 h. */
    start() {
      if (running) return;
      running = true;
      void check();
      checkTimer = timers.setInterval(() => void check(), CHECK_INTERVAL_MS);
    },

    /** Stops the timers; nothing is persisted. */
    stop() {
      running = false;
      if (checkTimer !== null) timers.clearInterval(checkTimer);
      if (laterTimer !== null) timers.clearTimeout(laterTimer);
      checkTimer = null;
      laterTimer = null;
    },

    check,

    /** "지금 업데이트" (or "다시 시도"): one flow at a time. */
    async update(): Promise<void> {
      const ready = state.phase === 'available' || (state.phase === 'failed' && state.canRetry);
      if (!ready || flowInFlight || (!pending && state.version === null)) return;
      flowInFlight = true;
      try {
        set({ phase: 'guarding', notice: null, error: null, canRetry: false, progress: null });
        const update = await currentUpdate();
        if (!update) return fail(DOWNLOAD_FAILED);
        await runInstall(update);
      } finally {
        flowInFlight = false;
      }
    },

    /** "나중에": hide the chip for 24 h, then ask again. */
    later() {
      if (state.phase !== 'available' && !(state.phase === 'failed' && state.canRetry)) return;
      set({ phase: 'available', hidden: true, notice: null, error: null, canRetry: false, progress: null });
      if (laterTimer !== null) timers.clearTimeout(laterTimer);
      laterTimer = timers.setTimeout(() => {
        laterTimer = null;
        release(pending);
        pending = null;
        set({ ...INITIAL_UPDATE_STATE });
        void check();
      }, LATER_HIDE_MS);
    },
  };
}

export type UpdateController = ReturnType<typeof createUpdateController>;

/** Whether the top bar shows the chip. */
export function isChipVisible(state: UpdateState): boolean {
  return state.version !== null && !state.hidden && state.phase !== 'idle' && state.phase !== 'checking';
}

/** Whether an install is under way (the panel shows progress instead of buttons). */
export function isInstalling(phase: UpdatePhase): boolean {
  return phase === 'downloading' || phase === 'guarding' || phase === 'installing' || phase === 'relaunching';
}

export function progressPercent(progress: DownloadProgress | null): number | null {
  if (!progress || progress.total === null || progress.total <= 0) return null;
  return Math.min(100, Math.floor((progress.downloaded / progress.total) * 100));
}

export function progressText(progress: DownloadProgress | null): string {
  const percent = progressPercent(progress);
  if (percent !== null) return `내려받는 중이에요 ${percent}%`;
  if (progress && progress.downloaded > 0) return `내려받는 중이에요 ${formatBytes(progress.downloaded)}`;
  return '내려받는 중이에요';
}

/** The chip text: the version only, plus a short step while an install runs. */
export function chipLabel(state: UpdateState): string {
  const base = `새 버전 ${state.version ?? ''}`.trim();
  switch (state.phase) {
    case 'downloading': {
      const percent = progressPercent(state.progress);
      return percent === null ? `${base} · 받는 중` : `${base} · 받는 중 ${percent}%`;
    }
    case 'guarding':
      return `${base} · 확인 중`;
    case 'installing':
      return `${base} · 설치 중`;
    case 'relaunching':
      return `${base} · 다시 시작 중`;
    case 'failed':
      return state.canRetry ? `${base} · 다시 시도 필요` : `${base} · 앱을 다시 열어 주세요`;
    default:
      return base;
  }
}

/** The one status line of the panel while an install runs. */
export function statusText(state: UpdateState): string | null {
  switch (state.phase) {
    case 'downloading':
      return progressText(state.progress);
    case 'guarding':
      return '업데이트할 수 있는지 확인하는 중이에요';
    case 'installing':
      return '설치하는 중이에요. 잠시만 기다려 주세요';
    case 'relaunching':
      return '앱을 다시 시작하는 중이에요';
    default:
      return null;
  }
}
