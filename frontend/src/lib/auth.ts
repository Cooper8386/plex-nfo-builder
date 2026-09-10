// Client-side API-token plumbing (v0.14.0). The backend is fail-closed: every
// /api request needs the token. `fetch` calls send it as the `X-API-Token`
// header; <img> URLs that can't set headers carry it as the `api_token` query
// param via `mediaUrl`.

const TOKEN_KEY = "pnb.apiToken";
let memoryToken: string | null | undefined;

export function getToken(): string | null {
  if (memoryToken !== undefined) return memoryToken;
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(t: string): void {
  memoryToken = t;
  try {
    localStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* ignore (private mode / disabled storage) */
  }
}

export function clearToken(): void {
  memoryToken = null;
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

// Listeners fired when the session ends, so the UI can drop back to the login
// screen. "expired" = the server rejected our token (401); "manual" = the user
// clicked Sign out.
export type SignOutReason = "expired" | "manual";
type UnauthorizedListener = (reason: SignOutReason) => void;
const listeners = new Set<UnauthorizedListener>();

export function onUnauthorized(fn: UnauthorizedListener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Deliberate sign-out: forget the token and return to the login screen. */
export function signOut(): void {
  clearToken();
  listeners.forEach((fn) => fn("manual"));
}

/** Append the token to a same-origin `/api/...` URL for header-less loaders. */
export function mediaUrl(url: string): string {
  const t = getToken();
  if (!t || !url.startsWith("/api/")) return url;
  return `${url}${url.includes("?") ? "&" : "?"}api_token=${encodeURIComponent(t)}`;
}

/** Drop-in for `fetch` that attaches the token and reports 401s. */
export async function authFetch(
  input: RequestInfo | URL,
  init: RequestInit = {},
): Promise<Response> {
  const token = getToken();
  const url = new URL(
    input instanceof Request ? input.url : String(input),
    window.location.origin,
  );
  if (url.origin !== window.location.origin)
    throw new Error("Refusing to send the API token to another origin.");
  const headers = new Headers(
    init.headers ?? (input instanceof Request ? input.headers : undefined),
  );
  if (token) headers.set("X-API-Token", token);
  const res = await fetch(input, { ...init, headers });
  if (res.status === 401 && getToken() === token) {
    clearToken();
    listeners.forEach((fn) => fn("expired"));
  }
  return res;
}
