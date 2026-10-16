import type { CachedDeadline } from './reminders';
import type { BackendConnection, BackendSupervisionStatus, DownloadJobStatus, DownloadResult, NativeDownloadPlanResult, OfficialViewerBridgeCookie, OfficialViewerSessionBridgeResult, RemindersStatus, WatchQueueItemInput, WatchQueueState } from './types';
import { open } from '@tauri-apps/plugin-dialog';
import { invoke } from '@tauri-apps/api/core';
import { downloadDir } from '@tauri-apps/api/path';
import { WebviewWindow } from '@tauri-apps/api/webviewWindow';

const FALLBACK_DOWNLOAD_DIR = '~/Downloads';
const OFFICIAL_VIEWER_LABEL = 'official-viewer';

// Rust opens every viewer under a fresh `official-viewer-<n>` label (so closing the
// previous window can never race the next build); match by prefix to find it.
async function focusExistingOfficialViewer(): Promise<boolean> {
  const exact = await WebviewWindow.getByLabel(OFFICIAL_VIEWER_LABEL);
  const existing = exact ?? (await WebviewWindow.getAll()).find((window) => window.label.startsWith(`${OFFICIAL_VIEWER_LABEL}-`));
  if (!existing) return false;
  await existing.show();
  await existing.unminimize();
  await existing.setFocus();
  return true;
}

export function isTauriRuntime(): boolean {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
}

export async function defaultDownloadDirectory(): Promise<string> {
  if (!isTauriRuntime()) {
    return FALLBACK_DOWNLOAD_DIR;
  }
  try {
    const realUserDownloads = await invoke<string>('real_download_dir');
    if (realUserDownloads) return realUserDownloads;
  } catch {
    // Fall back to the Tauri path API if the native command is unavailable during development.
  }
  const base = await downloadDir();
  return base;
}

export async function pickDownloadDirectory(): Promise<string | null> {
  if (!isTauriRuntime()) {
    return null;
  }
  const selected = await open({
    directory: true,
    multiple: false,
    title: '강의 다운로드 폴더 선택',
  });
  return typeof selected === 'string' ? selected : null;
}

export async function backendSupervisionStatus(): Promise<BackendSupervisionStatus> {
  if (!isTauriRuntime()) {
    return { state: 'ready', mode: 'browser', error: null, log_path: null };
  }
  return invoke<BackendSupervisionStatus>('backend_supervision_status');
}

export async function restartBackend(): Promise<void> {
  if (!isTauriRuntime()) return;
  await invoke<void>('backend_restart');
}

export async function backendApiBaseUrl(): Promise<string> {
  if (!isTauriRuntime()) {
    return '';
  }
  return invoke<string>('backend_api_base_url');
}

let cachedBackendConnection: Promise<BackendConnection> | null = null;

/** Base URL and per-launch token of the Tauri-managed backend; empty under Vite/external backends. */
export function backendApiConnection(): Promise<BackendConnection> {
  if (!isTauriRuntime()) {
    return Promise.resolve({ baseUrl: '', token: null });
  }
  if (!cachedBackendConnection) {
    const pending = invoke<{ base_url: string; token: string | null }>('backend_api_connection')
      .then((connection) => ({ baseUrl: connection.base_url, token: connection.token ?? null }));
    // Do not cache failures.
    pending.catch(() => {
      if (cachedBackendConnection === pending) cachedBackendConnection = null;
    });
    cachedBackendConnection = pending;
  }
  return cachedBackendConnection;
}

/** Forget the cached connection; the port and token change whenever the backend restarts. */
export function resetBackendConnection(): void {
  cachedBackendConnection = null;
}

export async function openDownloadedFolder(path: string): Promise<void> {
  if (!isTauriRuntime()) {
    return;
  }
  await invoke<void>('open_path_in_finder', { path });
}

/**
 * Opens an LMS page in the system browser, where the user is usually signed in already. Only
 * for the explicit "브라우저로 열기" actions; pages normally open in the app (`openLmsPageWindow`).
 */
export async function openUrlInBrowser(address: string): Promise<void> {
  if (!isTauriRuntime()) {
    window.open(address, '_blank', 'noopener,noreferrer');
    return;
  }
  await invoke<void>('open_url_in_browser', { url: address });
}

export async function openOfficialViewer(url: string, title: string, sessionBridge?: OfficialViewerSessionBridgeResult): Promise<void> {
  if (!isTauriRuntime()) {
    window.open(url, '_blank', 'noopener,noreferrer');
    return;
  }
  if (sessionBridge) {
    await invoke<void>('open_official_viewer_with_session', { request: { ...sessionBridge, title: title || sessionBridge.title || '숭실 LMS 공식 시청 창' } });
    return;
  }
  if (await focusExistingOfficialViewer()) return;
  const webview = new WebviewWindow(OFFICIAL_VIEWER_LABEL, {
    url,
    title: title || '숭실 LMS 공식 시청 창',
    width: 1180,
    height: 760,
    minWidth: 900,
    minHeight: 620,
    center: true,
    focus: true,
    resizable: true,
    visible: true,
    decorations: true,
  });
  await new Promise<void>((resolve, reject) => {
    let resolved = false;
    const finish = (callback: () => void) => {
      if (resolved) return;
      resolved = true;
      callback();
    };
    void webview.once('tauri://created', () => finish(resolve));
    void webview.once('tauri://error', (event) => {
      finish(() => reject(new Error(`앱 내부 공식 플레이어 창을 만들 수 없습니다: ${String(event.payload ?? 'unknown')}`)));
    });
  });
}

// ----------------------------------------------------------------- sequential watch
// The queue itself (state, playback window, menu bar item) lives in the Rust shell; these are
// thin calls. Browser/Vite mode has no such shell.

export interface WatchQueueStartRequest {
  course_id: string;
  course_name: string;
  items: WatchQueueItemInput[];
}

export function watchQueueStart(request: WatchQueueStartRequest): Promise<WatchQueueState> {
  return invoke<WatchQueueState>('watch_queue_start', { request });
}

export function watchQueueNext(): Promise<WatchQueueState> {
  return invoke<WatchQueueState>('watch_queue_next');
}

export function watchQueueStop(): Promise<WatchQueueState> {
  return invoke<WatchQueueState>('watch_queue_stop');
}

export function watchQueueStatus(): Promise<WatchQueueState> {
  return invoke<WatchQueueState>('watch_queue_status');
}

export async function watchQueueShowWindow(): Promise<void> {
  await invoke<void>('watch_queue_show_window');
}

// ------------------------------------------------------------------- LMS page windows

export interface LmsPageWindowRequest {
  title: string;
  url: string;
  cookies: OfficialViewerBridgeCookie[];
}

/** Opens an LMS page (wiki page, board, assignment, quiz) in a normal app window with the LMS session. */
export async function openLmsPageWindow(request: LmsPageWindowRequest): Promise<void> {
  await invoke<void>('open_lms_page_with_session', { request });
}

export function startNativeDownloadJob(plan: NativeDownloadPlanResult): Promise<DownloadJobStatus> {
  return invoke<DownloadJobStatus>('native_start_download_job', {
    request: {
      result: stripNativeTransfer(plan),
      native_download: plan.native_download,
    },
  });
}

export function startNativeBatchDownloadJob(courseId: string | number, courseName: string, plans: NativeDownloadPlanResult[]): Promise<DownloadJobStatus> {
  return invoke<DownloadJobStatus>('native_start_batch_download_job', {
    request: {
      course_id: courseId,
      course_name: courseName,
      items: plans.map((plan) => ({ result: stripNativeTransfer(plan), native_download: plan.native_download })),
    },
  });
}

export function fetchNativeDownloadJob(jobId: string): Promise<DownloadJobStatus> {
  return invoke<DownloadJobStatus>('native_download_job_status', { jobId });
}

export function cancelNativeDownloadJob(jobId: string): Promise<DownloadJobStatus> {
  return invoke<DownloadJobStatus>('native_cancel_download_job', { jobId });
}

function stripNativeTransfer(plan: NativeDownloadPlanResult): DownloadResult {
  const { native_download: _nativeDownload, ...result } = plan;
  return result;
}

// ------------------------------------------------------------------ deadline reminders

/** Hands the fresh deadlines to the app so reminders keep working while it is closed. */
export async function writeDeadlineCache(items: CachedDeadline[]): Promise<void> {
  if (!isTauriRuntime()) return;
  await invoke<void>('deadline_cache_write', { items });
}

const BROWSER_REMINDERS: RemindersStatus = { enabled: false, agent_installed: false, last_saved_at: null, supported: false };

export async function remindersStatus(): Promise<RemindersStatus> {
  if (!isTauriRuntime()) return BROWSER_REMINDERS;
  return invoke<RemindersStatus>('reminders_status');
}

export async function setRemindersEnabled(enabled: boolean): Promise<RemindersStatus> {
  if (!isTauriRuntime()) return BROWSER_REMINDERS;
  return invoke<RemindersStatus>('reminders_set_enabled', { enabled });
}

export async function sendTestReminder(): Promise<void> {
  if (!isTauriRuntime()) return;
  await invoke<void>('reminders_test');
}
