import { errorMessage } from "../lib/errors";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type MatchResult } from "../lib/api";

import { providerPageUrl } from "./mediaLinks";

/** Prominent empty state shown when a folder has no binding yet. */
export function BindEmptyState({
  path,
  detectedKind,
  defaultProvider,
  onBound,
}: {
  path: string;
  detectedKind: "series" | "movie";
  defaultProvider?: "tvdb" | "tmdb";
  onBound: () => void;
}) {
  return (
    <div className="bg-indigo-950/30 border border-indigo-800/60 rounded-md p-4 mb-6">
      <div className="text-sm font-semibold text-indigo-100 mb-1">
        This folder isn't bound yet
      </div>
      <p className="text-xs text-indigo-200/80 mb-3">
        Bind it to a TVDB or TMDB title to download artwork, generate NFOs, and
        map episodes.
      </p>
      <MatchPanel
        path={path}
        detectedKind={detectedKind}
        defaultProvider={defaultProvider}
        onBound={onBound}
        initialOpen
      />
    </div>
  );
}

/** Search & bind UI. Used both as the empty-state body and the "Change match" panel. */
export function MatchPanel({
  path,
  detectedKind,
  defaultProvider = "tvdb",
  onBound,
  initialOpen,
}: {
  path: string;
  detectedKind: "series" | "movie";
  defaultProvider?: "tvdb" | "tmdb";
  onBound: () => void;
  initialOpen?: boolean;
}) {
  const qc = useQueryClient();
  const [kind, setKind] = useState(detectedKind);
  const [provider, setProvider] = useState<"tvdb" | "tmdb">(defaultProvider);
  const [title, setTitle] = useState("");
  const [search, setSearch] = useState<{
    title: string;
    kind: "series" | "movie";
    provider: "tvdb" | "tmdb";
  } | null>(null);
  const [binding, setBinding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const results = useQuery({
    queryKey: ["match-search", search],
    queryFn: () =>
      api.match.search(
        search!.title,
        search!.kind,
        undefined,
        undefined,
        search!.provider,
      ),
    enabled: search !== null,
  });
  return (
    <section className={initialOpen ? "" : "panel p-4 mb-5"}>
      {!initialOpen && (
        <h3 className="font-semibold mb-3">Change source match</h3>
      )}
      <form
        className="flex flex-wrap gap-2 mb-3"
        onSubmit={(event) => {
          event.preventDefault();
          if (title.trim() && !binding) {
            setError(null);
            setSearch({ title: title.trim(), kind, provider });
          }
        }}
      >
        <select
          aria-label="Metadata provider"
          className="field"
          value={provider}
          disabled={binding}
          onChange={(event) =>
            setProvider(event.target.value as "tvdb" | "tmdb")
          }
        >
          <option value="tvdb">TVDB</option>
          <option value="tmdb">TMDB</option>
        </select>
        <select
          aria-label="Media type"
          className="field"
          value={kind}
          disabled={binding}
          onChange={(event) =>
            setKind(event.target.value as "series" | "movie")
          }
        >
          <option value="series">Series</option>
          <option value="movie">Movie</option>
        </select>
        <input
          aria-label="Title to match"
          className="field flex-1 min-w-32"
          value={title}
          disabled={binding}
          placeholder="Search by title…"
          onChange={(event) => setTitle(event.target.value)}
        />
        <button
          className="btn btn-primary"
          disabled={binding || results.isFetching || !title.trim()}
        >
          {results.isFetching ? "Searching…" : "Search"}
        </button>
      </form>
      {(error || results.error) && (
        <p role="alert" className="text-xs text-rose-300 mb-3">
          {error ?? results.error?.message}
        </p>
      )}
      <div className="max-h-80 overflow-auto border border-slate-800 rounded">
        {!search && (
          <p className="p-4 text-xs text-slate-400">
            Search for a title, then check the year and source before matching.
          </p>
        )}
        {search &&
          !results.isFetching &&
          results.data?.results.length === 0 && (
            <p className="p-4 text-xs text-slate-400">
              No titles found. Try a shorter title or another provider.
            </p>
          )}
        {results.data?.results.map((match) => {
          const source = match.provider ?? search!.provider;
          const id = String(match.tvdb_id ?? match.id ?? "");
          return (
            <div
              key={source + id}
              className="flex items-center gap-3 p-3 border-b border-slate-800 last:border-0"
            >
              {match.image_url && (
                <img
                  alt=""
                  loading="lazy"
                  src={match.image_url}
                  className="w-10 h-14 rounded object-cover"
                />
              )}
              <div className="min-w-0 flex-1">
                <div className="text-sm font-medium">
                  {match.name} {match.year ? "(" + match.year + ")" : ""}
                </div>
                <p className="text-xs text-slate-400 mt-1">
                  {source.toUpperCase()} · {id} · {search!.kind}
                </p>
              </div>
              <button
                className="btn"
                disabled={binding || !id}
                onClick={async () => {
                  setBinding(true);
                  setError(null);
                  try {
                    await api.match.bind({
                      folder_path: path,
                      kind: search!.kind,
                      provider: source,
                      external_id: id,
                      title: match.name,
                      year: match.year,
                    });
                    await Promise.all(
                      [
                        "detail",
                        "episodes",
                        "artwork-candidates",
                        "overrides",
                        "nfo-explain",
                      ].map((key) =>
                        qc.invalidateQueries({ queryKey: [key, path] }),
                      ),
                    );
                    await qc.invalidateQueries({ queryKey: ["items"] });
                    onBound();
                  } catch (cause) {
                    setError(
                      "Match failed: " +
                        (cause instanceof Error
                          ? cause.message
                          : String(cause)),
                    );
                  } finally {
                    setBinding(false);
                  }
                }}
              >
                Match title
              </button>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-slate-400 mt-2">
        Matching saves the source only. Build NFOs when you're ready to write metadata and artwork.
      </p>
    </section>
  );
}

/**
 * Lets the user pin a manual TMDB id on a TVDB-bound show (or vice versa)
 * when the primary metadata record doesn't include a cross-reference. The
 * stored secondary id is used by the cross-provider artwork resolver, the
 * fanart.tv lookup, and the NFO ``<uniqueid>`` block. Persists to the sidecar
 * so it survives a DB wipe.
 */
export function SecondarySourcePanel({
  path,
  kind,
  primaryProvider,
  secondaryProvider,
  secondaryExternalId,
  onChanged,
}: {
  path: string;
  kind: "series" | "movie";
  primaryProvider: "tvdb" | "tmdb";
  secondaryProvider: string | null;
  secondaryExternalId: string | null;
  onChanged: () => void;
}) {
  const otherProvider: "tvdb" | "tmdb" =
    primaryProvider === "tvdb" ? "tmdb" : "tvdb";
  const otherLabel = otherProvider.toUpperCase();
  const hasSecondary =
    !!secondaryProvider &&
    !!secondaryExternalId &&
    secondaryProvider.toLowerCase() !== primaryProvider;
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [pasteId, setPasteId] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [matches, setMatches] = useState<MatchResult[]>([]);
  const attemptedDiscovery = useRef(false);

  const discover = useCallback(async () => {
    setBusy(true);
    setMsg("Looking for a linked source…");
    try {
      const result = await api.match.discoverSecondary(path);
      setMsg(result.found ? `${otherLabel} source linked.` :
        `No exact ${otherLabel} link found. Search or paste an ID below.`);
      if (result.found) onChanged();
    } catch (error) {
      setMsg(`Discovery failed: ${errorMessage(error)}`);
    } finally {
      setBusy(false);
    }
  }, [path, otherLabel, onChanged]);

  useEffect(() => {
    if (attemptedDiscovery.current) return;
    attemptedDiscovery.current = true;
    if (!hasSecondary) void discover();
  }, [hasSecondary, discover]);

  const linkUrl = providerPageUrl(secondaryProvider, secondaryExternalId, kind);

  const save = async (
    provider: "tvdb" | "tmdb" | null,
    externalId: string | null,
  ) => {
    setBusy(true);
    setMsg(null);
    try {
      await api.match.setSecondary({
        folder_path: path,
        provider,
        external_id: externalId,
      });
      setOpen(false);
      setPasteId("");
      setSearchQuery("");
      setMatches([]);
      onChanged();
    } catch (e: unknown) {
      setMsg(`Failed: ${errorMessage(e)}`);
    } finally {
      setBusy(false);
    }
  };

  const savePaste = async () => {
    const id = pasteId.trim();
    if (!id) {
      setMsg(`Enter a ${otherLabel} id (numbers only).`);
      return;
    }
    if (!/^\d+$/.test(id)) {
      setMsg(`${otherLabel} id should be all numbers.`);
      return;
    }
    await save(otherProvider, id);
  };

  const runSearch = async () => {
    if (!searchQuery.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.match.search(
        searchQuery,
        kind,
        undefined,
        undefined,
        otherProvider,
      );
      setMatches(r.results || []);
    } catch (e: unknown) {
      setMsg(`Search failed: ${errorMessage(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="bg-slate-900/40 border border-slate-800 rounded-md p-3 mb-6">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold mr-1">Secondary source</h3>
        {hasSecondary ? (
          <>
            <span className="text-xs px-2 py-0.5 rounded bg-slate-800 border border-slate-700 font-mono">
              {(secondaryProvider ?? "").toLowerCase()}-{secondaryExternalId}
            </span>
            {linkUrl && (
              <a
                href={linkUrl}
                target="_blank"
                rel="noreferrer"
                className="text-xs px-2 py-0.5 rounded border border-indigo-700 text-indigo-300 hover:bg-indigo-700/30"
                title={`Open on ${(secondaryProvider ?? "").toUpperCase()}`}
              >
                {(secondaryProvider ?? "").toUpperCase()} ↗
              </a>
            )}
            <button
              disabled={busy}
              className="text-xs px-2 py-0.5 rounded border border-slate-700 hover:bg-slate-800 disabled:opacity-50"
              onClick={() => setOpen((v) => !v)}
            >
              {open ? "Hide" : "Edit"}
            </button>
            <button
              disabled={busy}
              className="text-xs px-2 py-0.5 rounded border border-yellow-600/60 text-yellow-300 hover:bg-yellow-600/20 disabled:opacity-50"
              onClick={() => save(null, null)}
              title="Remove the manual secondary id"
            >
              Clear
            </button>
          </>
        ) : (
          <>
            <span className="text-xs text-slate-500">
              No {otherLabel} id linked.
            </span>
            <button disabled={busy} className="btn" onClick={discover}>
              {busy ? "Discovering…" : "Discover source"}
            </button>
            <button
              disabled={busy}
              className="text-xs px-2 py-0.5 rounded border border-slate-700 hover:bg-slate-800 disabled:opacity-50"
              onClick={() => setOpen((v) => !v)}
            >
              {open ? "Hide" : `Add ${otherLabel} id`}
            </button>
          </>
        )}
      </div>
      <p className="text-xs text-slate-500 mt-2">
        Finds {otherLabel} through provider cross-references and shared IMDb IDs.
        Search or paste an ID if no exact link exists. Saved links supply artwork
        and metadata IDs when you build.
      </p>

      {open && (
        <div className="mt-3 border-t border-slate-800 pt-3 space-y-3">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
              Paste {otherLabel} id
            </div>
            <div className="flex flex-wrap gap-2">
              <input
                aria-label={`${otherLabel} secondary ID`}
                value={pasteId}
                onChange={(e) => setPasteId(e.target.value)}
                placeholder={`${otherLabel} id (e.g. ${otherProvider === "tmdb" ? "12345" : "81189"})`}
                className="bg-slate-800 px-2 py-1 rounded text-sm flex-1 min-w-[12rem] border border-slate-700 font-mono"
                onKeyDown={(e) => {
                  if (e.key === "Enter") savePaste();
                }}
              />
              <button
                disabled={busy || !pasteId.trim()}
                className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-sm disabled:opacity-50"
                onClick={savePaste}
              >
                Save
              </button>
            </div>
          </div>

          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">
              Or search {otherLabel}
            </div>
            <div className="flex flex-wrap gap-2 mb-2">
              <input
                aria-label={`Search ${otherLabel}`}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={`${otherLabel} title`}
                className="bg-slate-800 px-2 py-1 rounded text-sm flex-1 min-w-[12rem] border border-slate-700"
                onKeyDown={(e) => {
                  if (e.key === "Enter") runSearch();
                }}
              />
              <button
                disabled={busy || !searchQuery.trim()}
                className="px-3 py-1 bg-slate-700 hover:bg-slate-600 rounded text-sm disabled:opacity-50"
                onClick={runSearch}
              >
                Search
              </button>
            </div>
            {matches.length > 0 && (
              <div className="max-h-72 overflow-auto border border-slate-800 rounded">
                {matches.map((m) => {
                  const externalId = String(m.tvdb_id ?? m.id ?? "");
                  return (
                    <div
                      key={`${otherProvider}-${externalId}`}
                      className="p-2 flex items-center gap-3 border-b border-slate-800 last:border-0"
                    >
                      {m.image_url && (
                        <img
                          alt=""
                          loading="lazy"
                          src={m.image_url}
                          className="w-10 h-14 object-cover rounded"
                        />
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="text-sm truncate">
                          {m.name} {m.year ? `(${m.year})` : ""}
                        </div>
                        <div className="text-xs text-slate-500 truncate">
                          <span
                            className={`mr-1 px-1 rounded ${
                              otherProvider === "tmdb"
                                ? "bg-emerald-800/60 text-emerald-100"
                                : "bg-blue-800/60 text-blue-100"
                            }`}
                          >
                            {otherProvider}
                          </span>
                          {otherProvider}-{externalId}
                        </div>
                      </div>
                      <button
                        disabled={busy || !externalId}
                        className="px-2 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-xs disabled:opacity-50"
                        onClick={() => save(otherProvider, externalId)}
                      >
                        Link
                      </button>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
      {msg && <div className="text-xs text-amber-400 mt-2">{msg}</div>}
    </div>
  );
}
