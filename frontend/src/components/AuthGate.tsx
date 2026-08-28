import { useCallback, useEffect, useState } from "react";
import { authFetch, clearToken, getToken, onUnauthorized, setToken } from "../lib/auth";

type Status = "checking" | "authed" | "login";

/**
 * Fail-closed gate (v0.14.0). The backend refuses every /api call without a
 * valid token, so nothing in the app can render until we've verified one.
 * Wrap the whole app in this: it validates any stored token on mount, shows a
 * token-entry screen otherwise, and drops back to that screen whenever a
 * request comes back 401.
 */
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("checking");
  const [tokenInput, setTokenInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Validate the currently stored token against a cheap authenticated route.
  // Returns true when authed; sets an error message otherwise.
  const verify = useCallback(async (): Promise<boolean> => {
    try {
      const res = await authFetch("/api/health");
      if (res.ok) return true;
      if (res.status === 503) {
        setError(
          "Server has no API_TOKEN configured. Set the API_TOKEN environment " +
            "variable on the container, then reload."
        );
      } else if (res.status === 401) {
        setError("Invalid token.");
      } else {
        setError(`Unexpected server response (${res.status}).`);
      }
    } catch {
      setError("Could not reach the server.");
    }
    return false;
  }, []);

  // On mount: try a stored token; skip straight to the login screen if none.
  useEffect(() => {
    let alive = true;
    (async () => {
      if (!getToken()) {
        if (alive) setStatus("login");
        return;
      }
      const ok = await verify();
      if (!alive) return;
      setStatus(ok ? "authed" : "login");
    })();
    return () => {
      alive = false;
    };
  }, [verify]);

  // A 401 mid-session (token revoked/changed) forces re-login. authFetch has
  // already cleared the stored token by the time this fires.
  useEffect(
    () =>
      onUnauthorized((reason) => {
        setError(
          reason === "expired"
            ? "Session expired. Enter the API token again."
            : null
        );
        setStatus("login");
      }),
    []
  );

  const submit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      const t = tokenInput.trim();
      if (!t) return;
      setBusy(true);
      setError(null);
      setToken(t);
      const ok = await verify();
      if (ok) {
        setStatus("authed");
        setTokenInput("");
      } else {
        clearToken();
      }
      setBusy(false);
    },
    [tokenInput, verify]
  );

  if (status === "checking") {
    return (
      <div className="h-full flex items-center justify-center text-slate-400">
        Loading…
      </div>
    );
  }

  if (status === "login") {
    return (
      <div className="h-full flex items-center justify-center bg-slate-950 p-4">
        <form
          onSubmit={submit}
          className="w-full max-w-sm rounded-lg border border-slate-800 bg-slate-900 p-6 shadow-xl"
        >
          <h1 className="text-lg font-semibold text-slate-100">
            Plex NFO Builder
          </h1>
          <p className="mt-1 text-sm text-slate-400">
            Enter your API token to continue.
          </p>
          <input
            type="password"
            autoFocus
            value={tokenInput}
            onChange={(e) => setTokenInput(e.target.value)}
            placeholder="API token"
            className="mt-4 w-full rounded border border-slate-700 bg-slate-800 px-3 py-2 text-slate-100 outline-none focus:border-indigo-500"
          />
          {error && <p className="mt-3 text-sm text-rose-400">{error}</p>}
          <button
            type="submit"
            disabled={busy || !tokenInput.trim()}
            className="mt-4 w-full rounded bg-indigo-600 px-3 py-2 font-medium text-white transition hover:bg-indigo-500 disabled:opacity-50"
          >
            {busy ? "Checking…" : "Unlock"}
          </button>
          <p className="mt-4 text-xs text-slate-500">
            The token is the <code>API_TOKEN</code> set on the container. It's
            stored in this browser only.
          </p>
        </form>
      </div>
    );
  }

  return <>{children}</>;
}
