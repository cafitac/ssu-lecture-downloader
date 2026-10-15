/** Header the backend checks when Tauri launched it with a per-launch token. */
export const API_TOKEN_HEADER = 'X-SSU-Token';

/** JSON request headers plus the backend token when one is known (Tauri-managed backend only). */
export function buildRequestHeaders(token: string | null, extra?: HeadersInit): Headers {
  const headers = new Headers({ 'Content-Type': 'application/json' });
  new Headers(extra).forEach((value, key) => headers.set(key, value));
  if (token) headers.set(API_TOKEN_HEADER, token);
  return headers;
}
