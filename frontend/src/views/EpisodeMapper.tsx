import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, RenamePlanItem, TvdbEpisode } from "../lib/api";

/** Episode mapping & rename UI. v0.10.0:
 *
 * - Each local file is its own row anchored to the actual file path so two
 *   unparsed files no longer collide on (S00,E00).
 * - Header label and the dropdown labels follow the binding's provider so a
 *   TMDB-bound show says "TMDB Episode" and not "TVDB Episode".
 * - Each row exposes inline season + episode pickers when the parser
 *   couldn't determine them from the filename (or whenever the user wants
 *   to retag a single file). The selection is sent through the new
 *   `/api/episodes/override-file` endpoint.
 * - "Rename to scheme" opens a diff modal that shows the source name next
 *   to the rendered target, supports per-row checkboxes, and warns about
 *   conflicts before writing anything to disk.
 */
export default function EpisodeMapper({ path }: { path: string }) {
  const qc = useQueryClient();
  const data = useQuery({
    queryKey: ["episodes", path],
    queryFn: () => api.episodes.list(path),
  });
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [seasonFilter, setSeasonFilter] = useState<number | "all">("all");
  const [showRename, setShowRename] = useState(false);

  const provider = data.data?.provider ?? "tvdb";
  const providerLabel = provider === "tmdb" ? "TMDB" : "TVDB";
  const provEpisodes = data.data?.tvdb_episodes ?? [];
  const locals = data.data?.locals ?? [];

  const seasonOptions = useMemo(() => {
    const set = new Set<number>();
    for (const l of locals) {
      const s = l.effective_season ?? l.parsed_season;
      if (s !== null && s !== undefined) set.add(s);
    }
    return Array.from(set).sort((a, b) => a - b);
  }, [locals]);

  const filtered = useMemo(() => {
    if (seasonFilter === "all") return locals;
    return locals.filter(
      (l) => (l.effective_season ?? l.parsed_season) === seasonFilter,
    );
  }, [locals, seasonFilter]);

  if (data.isLoading)
    return <div className="text-sm text-slate-500">Loading episodes…</div>;
  if (data.error)
    return (
      <div className="text-sm text-amber-400">
        {(data.error as any).message ?? "Failed to load episodes"}
      </div>
    );

  /** Persist a per-file override and refresh the table. */
  const setFileOverride = async (
    file_path: string,
    args: {
      season?: number | null;
      episode?: number | null;
      external_id?: string | null;
      clear?: boolean;
    },
    successMsg: string,
  ) => {
    setBusyKey(file_path);
    setMsg(null);
    try {
      await api.episodes.overrideFile({
        folder_path: path,
        file_path,
        ...args,
      });
      setMsg(successMsg);
      await qc.invalidateQueries({ queryKey: ["episodes", path] });
    } catch (e: any) {
      setMsg(e?.message ?? String(e));
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-3 mb-3">
        <span className="text-xs text-slate-500">
          {locals.length} local file{locals.length === 1 ? "" : "s"} ·{" "}
          {provEpisodes.length} {providerLabel} episodes
        </span>
        <div className="flex-1" />
        <label className="text-xs text-slate-400 flex items-center gap-1">
          Season
          <select
            value={String(seasonFilter)}
            onChange={(e) =>
              setSeasonFilter(
                e.target.value === "all" ? "all" : parseInt(e.target.value, 10),
              )
            }
            className="bg-slate-800 px-2 py-1 rounded text-sm border border-slate-700"
          >
            <option value="all">all</option>
            {seasonOptions.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <button
          onClick={() => setShowRename(true)}
          className="text-xs px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 rounded disabled:opacity-50"
          disabled={!locals.length}
          title="Rename files on disk to match your scheme."
        >
          Rename to scheme…
        </button>
      </div>
      {msg && <div className="text-xs text-slate-400 mb-2">{msg}</div>}
      {filtered.length === 0 ? (
        <div className="text-sm text-slate-500">
          No local episode files detected. Make sure your folder layout has
          Season folders with video files inside, or drop episodes at the
          show root for short series / OVAs.
        </div>
      ) : (
        <div className="border border-slate-800 rounded overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-slate-400 text-left">
              <tr>
                <th className="p-2 w-32">Local</th>
                <th className="p-2">File</th>
                <th className="p-2 w-24">Match</th>
                <th className="p-2">{providerLabel} Episode</th>
                <th className="p-2 w-24"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((l) => {
                const key = l.file_path;
                const isOverride = !!l.has_file_override;
                const isAuto = !!l.matched_episode_id && !isOverride;
                const isUnmatched = !l.matched_episode_id;
                const effSeason = l.effective_season;
                const effEpisode = l.effective_episode;
                const showInlineSE = l.unparsed; // only ask for s/e on unparsed rows
                return (
                  <tr
                    key={key}
                    className={`border-t border-slate-800 ${
                      isOverride
                        ? "bg-amber-900/10"
                        : isUnmatched
                          ? "bg-rose-900/10"
                          : ""
                    }`}
                  >
                    <td className="p-2 font-mono text-xs text-slate-300 align-top">
                      {showInlineSE ? (
                        <InlineSEPicker
                          season={effSeason}
                          episode={effEpisode}
                          disabled={busyKey === key}
                          onChange={(s, ep) =>
                            setFileOverride(
                              l.file_path,
                              { season: s, episode: ep },
                              `Set ${formatSE(s, ep)} for ${l.file_name}`,
                            )
                          }
                        />
                      ) : (
                        <span>{formatSE(effSeason, effEpisode)}</span>
                      )}
                    </td>
                    <td className="p-2 text-xs text-slate-400 truncate max-w-md align-top">
                      {l.file_name}
                    </td>
                    <td className="p-2 align-top">
                      {isOverride && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-700 text-amber-100">
                          override
                        </span>
                      )}
                      {isAuto && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-700 text-emerald-100">
                          auto
                        </span>
                      )}
                      {isUnmatched && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-rose-700 text-rose-100">
                          unmatched
                        </span>
                      )}
                    </td>
                    <td className="p-2 align-top">
                      <select
                        value={l.matched_episode_id ?? ""}
                        disabled={busyKey === key}
                        onChange={(e) => {
                          const val = e.target.value || null;
                          setFileOverride(
                            l.file_path,
                            {
                              external_id: val,
                              // Persist the parser's read-back season/episode
                              // so the row stays mapped even if the file
                              // itself moves later.
                              season: effSeason,
                              episode: effEpisode,
                            },
                            val
                              ? `Mapped ${l.file_name} to ${providerLabel} episode.`
                              : `Cleared mapping for ${l.file_name}.`,
                          );
                        }}
                        className="bg-slate-800 px-2 py-1 rounded text-xs border border-slate-700 w-full"
                      >
                        <option value="">— unmatched —</option>
                        {provEpisodes.map((ep) => (
                          <option key={ep.id} value={ep.id}>
                            {labelEp(ep)}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="p-2 text-right align-top">
                      {(isOverride || l.has_file_override) && (
                        <button
                          disabled={busyKey === key}
                          onClick={() =>
                            setFileOverride(
                              l.file_path,
                              { clear: true },
                              `Reset ${l.file_name}.`,
                            )
                          }
                          className="text-[10px] px-2 py-1 rounded bg-slate-800 border border-slate-700 hover:border-amber-500 disabled:opacity-40"
                        >
                          Reset
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
      {showRename && (
        <RenameModal
          path={path}
          onClose={() => setShowRename(false)}
          onApplied={async () => {
            await qc.invalidateQueries({ queryKey: ["episodes", path] });
            await qc.invalidateQueries({ queryKey: ["detail", path] });
          }}
        />
      )}
    </div>
  );
}

function formatSE(s: number | null | undefined, e: number | null | undefined): string {
  if (s === null || s === undefined || e === null || e === undefined) return "—";
  return `S${String(s).padStart(2, "0")}E${String(e).padStart(2, "0")}`;
}

function labelEp(ep: TvdbEpisode): string {
  const s = ep.season ?? 0;
  const n = ep.number ?? 0;
  const code = `S${String(s).padStart(2, "0")}E${String(n).padStart(2, "0")}`;
  return ep.name ? `${code} — ${ep.name}` : code;
}

function InlineSEPicker({
  season,
  episode,
  disabled,
  onChange,
}: {
  season: number | null | undefined;
  episode: number | null | undefined;
  disabled: boolean;
  onChange: (s: number, e: number) => void;
}) {
  const [s, setS] = useState<string>(season != null ? String(season) : "1");
  const [e, setE] = useState<string>(episode != null ? String(episode ?? "") : "");
  const commit = () => {
    const sn = parseInt(s, 10);
    const en = parseInt(e, 10);
    if (Number.isFinite(sn) && Number.isFinite(en) && sn >= 0 && en > 0) {
      onChange(sn, en);
    }
  };
  return (
    <span className="inline-flex items-center gap-1 font-mono text-[11px]">
      S
      <input
        value={s}
        disabled={disabled}
        onChange={(ev) => setS(ev.target.value.replace(/[^0-9]/g, ""))}
        onBlur={commit}
        onKeyDown={(ev) => {
          if (ev.key === "Enter") {
            ev.preventDefault();
            commit();
          }
        }}
        className="w-9 bg-slate-800 border border-slate-700 rounded px-1 py-0.5 text-center"
      />
      E
      <input
        value={e}
        disabled={disabled}
        onChange={(ev) => setE(ev.target.value.replace(/[^0-9]/g, ""))}
        onBlur={commit}
        onKeyDown={(ev) => {
          if (ev.key === "Enter") {
            ev.preventDefault();
            commit();
          }
        }}
        placeholder="—"
        className="w-12 bg-slate-800 border border-slate-700 rounded px-1 py-0.5 text-center placeholder:text-slate-600"
      />
    </span>
  );
}

function RenameModal({
  path,
  onClose,
  onApplied,
}: {
  path: string;
  onClose: () => void;
  onApplied: () => Promise<void>;
}) {
  const [items, setItems] = useState<RenamePlanItem[] | null>(null);
  const [template, setTemplate] = useState<string>("");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async (overrideTemplate?: string) => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.episodes.rename.preview({
        folder_path: path,
        template: overrideTemplate || undefined,
      });
      setItems(r.items);
      setTemplate(r.template);
      // Default-check every safe rename that actually changes the name.
      const next: Record<string, boolean> = {};
      for (const it of r.items) {
        if (!it.unchanged && !it.conflict) next[it.src] = true;
      }
      setSelected(next);
    } catch (e: any) {
      setMsg(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  // Eager-load on first mount.
  useMemo(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const apply = async () => {
    if (!items) return;
    const only_src = Object.entries(selected)
      .filter(([, v]) => v)
      .map(([k]) => k);
    if (only_src.length === 0) {
      setMsg("Nothing selected.");
      return;
    }
    if (
      !window.confirm(
        `Rename ${only_src.length} file${only_src.length === 1 ? "" : "s"} on disk?\n\nThis cannot be undone automatically — but per-file overrides and bindings are carried along to the new names.`,
      )
    )
      return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.episodes.rename.apply({
        folder_path: path,
        template: template || undefined,
        only_src,
      });
      const renamed = r.renamed.length;
      const skipped = r.skipped.length;
      const failed = r.failed.length;
      setMsg(
        `Renamed ${renamed} · skipped ${skipped} · failed ${failed}.${
          failed
            ? ` First failure: ${r.failed[0]?.reason}`
            : ""
        }`,
      );
      await onApplied();
      await load(template); // re-preview to show the new state
    } catch (e: any) {
      setMsg(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const toRenameCount = items
    ? items.filter((it) => !it.unchanged && !it.conflict && selected[it.src])
        .length
    : 0;

  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center px-4"
      onClick={onClose}
    >
      <div
        className="bg-slate-900 border border-slate-700 rounded-lg shadow-xl w-full max-w-4xl max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="p-4 border-b border-slate-800 flex items-center gap-3">
          <h3 className="text-base font-semibold flex-1">Rename files to scheme</h3>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-white text-sm"
            title="Close"
          >
            ✕
          </button>
        </div>
        <div className="p-4 border-b border-slate-800 space-y-2">
          <label className="text-xs uppercase tracking-wide text-slate-500">
            Template
          </label>
          <div className="flex gap-2">
            <input
              value={template}
              onChange={(e) => setTemplate(e.target.value)}
              className="flex-1 bg-slate-950 border border-slate-700 rounded px-2 py-1 text-sm font-mono"
              placeholder="{title} ({year}) - S{season:02}E{episode:02} - {episode_title}{ext}"
              spellCheck={false}
            />
            <button
              onClick={() => load(template)}
              className="px-3 py-1 text-xs bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded"
              disabled={busy}
            >
              Preview
            </button>
          </div>
          <p className="text-[11px] text-slate-500 leading-snug">
            Tokens: <code>{"{title}"}</code> <code>{"{year}"}</code>{" "}
            <code>{"{season}"}</code> <code>{"{season:02}"}</code>{" "}
            <code>{"{episode}"}</code> <code>{"{episode:02}"}</code>{" "}
            <code>{"{episode_title}"}</code> <code>{"{quality}"}</code>{" "}
            <code>{"{ext}"}</code>. Default matches Sonarr's standard scheme.
          </p>
        </div>
        <div className="flex-1 overflow-auto">
          {!items ? (
            <div className="p-6 text-sm text-slate-500">Building preview…</div>
          ) : items.length === 0 ? (
            <div className="p-6 text-sm text-slate-500">
              No renameable files — all rows are unparsed or have no override.
            </div>
          ) : (
            <table className="w-full text-xs">
              <thead className="bg-slate-900 text-slate-400 sticky top-0">
                <tr>
                  <th className="p-2 w-8"></th>
                  <th className="p-2">From</th>
                  <th className="p-2">To</th>
                  <th className="p-2 w-24">Status</th>
                </tr>
              </thead>
              <tbody>
                {items.map((it) => {
                  const isUnchanged = it.unchanged;
                  const isConflict = !!it.conflict;
                  const checked = !!selected[it.src];
                  return (
                    <tr
                      key={it.src}
                      className={`border-t border-slate-800 ${
                        isConflict
                          ? "bg-rose-900/10"
                          : isUnchanged
                            ? "opacity-50"
                            : ""
                      }`}
                    >
                      <td className="p-2 text-center">
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={isUnchanged || isConflict}
                          onChange={(e) =>
                            setSelected({ ...selected, [it.src]: e.target.checked })
                          }
                        />
                      </td>
                      <td className="p-2 font-mono text-slate-400 break-all">
                        {it.src_name}
                      </td>
                      <td className="p-2 font-mono text-slate-100 break-all">
                        {it.dst_name}
                      </td>
                      <td className="p-2">
                        {isUnchanged ? (
                          <span className="text-[10px] uppercase text-slate-500">
                            no change
                          </span>
                        ) : isConflict ? (
                          <span className="text-[10px] uppercase text-rose-300">
                            {it.conflict}
                          </span>
                        ) : (
                          <span className="text-[10px] uppercase text-emerald-300">
                            ready
                          </span>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <div className="p-4 border-t border-slate-800 flex items-center gap-3">
          <span className="text-xs text-slate-400 flex-1">
            {msg ?? `${toRenameCount} file${toRenameCount === 1 ? "" : "s"} selected.`}
          </span>
          <button
            onClick={onClose}
            disabled={busy}
            className="px-3 py-1.5 text-sm bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={apply}
            disabled={busy || toRenameCount === 0}
            className="px-3 py-1.5 text-sm bg-indigo-600 hover:bg-indigo-500 rounded disabled:opacity-50"
          >
            Rename {toRenameCount}
          </button>
        </div>
      </div>
    </div>
  );
}
