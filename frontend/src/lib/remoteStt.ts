import type { RemoteSttConfigPatch, RemoteSttKind, RemoteSttStatus } from './types';

/** Pure helpers for the remote subtitle server settings (unit-tested from Node, no React). */

export const REMOTE_STT_NOTE = '음성은 이 서버로만 보내져요. 외부 공개 주소 대신 SSH 터널 사용을 권장해요.';

export const REMOTE_KIND_LABEL: Record<RemoteSttKind, string> = {
  whisper_cpp_server: 'whisper.cpp 서버',
  openai_compatible: 'OpenAI형 (/v1/audio/transcriptions)',
};

/** "Mac Studio (whisper.cpp)": whisper-server reached through an SSH tunnel to macstudio. */
export const MAC_STUDIO_PRESET: RemoteSttConfigPatch = {
  enabled: true,
  kind: 'whisper_cpp_server',
  base_url: 'http://127.0.0.1:18795',
  tunnel: { host: 'macstudio', remote_port: 8795, local_port: 18795 },
};

const INTEL_HINT = ' 설정의 원격 자막 서버를 쓰면 만들 수 있어요.';

interface WhisperStatusLike {
  available: boolean;
  platform_supported?: boolean;
  install_hint?: string | null;
}

/**
 * Whether subtitles can be made, and what blocks them.
 * A remote server replaces the local engine entirely: the 자막 toggle is usable and nothing
 * local has to be installed, even when the server is down (the job then reports a clear error).
 */
export function subtitleSupport(whisper: WhisperStatusLike | undefined, remoteEnabled: boolean): { unsupportedReason: string | null; localMissing: boolean; viaRemote: boolean } {
  if (remoteEnabled) return { unsupportedReason: null, localMissing: false, viaRemote: true };
  const unsupported = whisper?.platform_supported === false;
  return {
    unsupportedReason: unsupported ? `${whisper?.install_hint ?? '이 기기에서는 자막 생성을 지원하지 않아요.'}${INTEL_HINT}` : null,
    localMissing: Boolean(whisper && !whisper.available && !unsupported),
    viaRemote: false,
  };
}

/** Editable form values; `apiKey` is only what the user typed (the saved key is never sent back to the UI). */
export interface RemoteSttDraft {
  kind: RemoteSttKind;
  baseUrl: string;
  model: string;
  apiKey: string;
  tunnelHost: string;
  tunnelRemotePort: string;
}

export function draftFromStatus(status: RemoteSttStatus | null): RemoteSttDraft {
  return {
    kind: status?.kind ?? 'whisper_cpp_server',
    baseUrl: status?.base_url ?? '',
    model: status?.model ?? '',
    apiKey: '',
    tunnelHost: status?.tunnel?.host ?? '',
    tunnelRemotePort: status?.tunnel ? String(status.tunnel.remote_port) : '',
  };
}

/** The config patch for a form. Returns an error text instead when the form cannot be saved. */
export function patchFromDraft(draft: RemoteSttDraft, status: RemoteSttStatus | null): { patch: RemoteSttConfigPatch } | { error: string } {
  const host = draft.tunnelHost.trim();
  const patch: RemoteSttConfigPatch = { kind: draft.kind, model: draft.kind === 'openai_compatible' ? draft.model.trim() : '' };
  if (host) {
    const port = Number(draft.tunnelRemotePort);
    if (!Number.isInteger(port) || port < 1 || port > 65535) return { error: '원격 포트는 1부터 65535 사이 숫자로 입력해 주세요.' };
    // The local end keeps its saved port (or stays automatic) so a manual tunnel on it is still found.
    patch.tunnel = { host, remote_port: port, local_port: status?.tunnel?.local_port ?? null };
  } else {
    patch.tunnel = null;
    patch.base_url = draft.baseUrl.trim();
  }
  if (draft.kind === 'openai_compatible' && draft.apiKey.trim()) patch.api_key = draft.apiKey.trim();
  return { patch };
}

/** One line for the "연결 확인" result. */
export function describeRemoteCheck(status: RemoteSttStatus): { ok: boolean; text: string } {
  if (status.reachable === true) return { ok: true, text: '서버에 연결됐어요.' };
  return { ok: false, text: status.last_error ?? '서버에 연결하지 못했어요.' };
}

export function remoteSummary(status: RemoteSttStatus | null): { label: string; tone: 'success' | 'warning' | 'neutral' } {
  if (!status || !status.enabled) return { label: '꺼짐', tone: 'neutral' };
  if (status.reachable === true) return { label: '연결됨', tone: 'success' };
  if (status.reachable === false) return { label: '연결 안 됨', tone: 'warning' };
  return { label: '연결 확인 필요', tone: 'warning' };
}
