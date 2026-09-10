import { errorMessage } from "../lib/errors";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, TvdbEpisode } from "../lib/api";
import RenameModal from "./RenameModal";

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
  const [allSeasonsFor, setAllSeasonsFor] = useState<string | null>(null);

  const provider = data.data?.provider ?? "tvdb";
  const providerLabel = provider === "tmdb" ? "TMDB" : "TVDB";
  const provEpisodes = useMemo(
    () => data.data?.tvdb_episodes ?? [],
    [data.data?.tvdb_episodes],
  );
  const episodesBySeason = useMemo(() => {
    const grouped = new Map<number, TvdbEpisode[]>();
    for (const episode of provEpisodes) {
      const season = episode.season ?? 0;
      const group = grouped.get(season) ?? [];
      group.push(episode);
      grouped.set(season, group);
    }
    return grouped;
  }, [provEpisodes]);
  const episodesById = useMemo(
    () => new Map(provEpisodes.map((episode) => [episode.id, episode])),
    [provEpisodes],
  );
  const locals = useMemo(() => data.data?.locals ?? [], [data.data?.locals]);

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
      <div className="text-sm text-amber-400">{errorMessage(data.error)}</div>
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
    } catch (e: unknown) {
      setMsg(errorMessage(e));
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div>
      <div className="panel p-3 flex flex-wrap items-center gap-3 mb-3">
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
          className="btn"
          disabled={!locals.length}
          title="Rename files on disk to match your scheme."
        >
          Preview rename…
        </button>
      </div>
      {msg && (
        <div role="status" className="text-xs text-slate-400 mb-2">
          {msg}
        </div>
      )}
      {filtered.length === 0 ? (
        <div className="text-sm text-slate-500">
          No local episode files detected. Make sure your folder layout has
          Season folders with video files inside, or drop episodes at the show
          root for short series / OVAs.
        </div>
      ) : (
        <div className="panel overflow-auto">
          <table className="w-full text-sm min-w-[720px]">
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
                const seasonEpisodes =
                  episodesBySeason.get(effSeason ?? l.parsed_season) ?? [];
                const currentMatch = episodesById.get(
                  l.matched_episode_id ?? "",
                );
                const choices =
                  allSeasonsFor === key
                    ? provEpisodes
                    : currentMatch && !seasonEpisodes.includes(currentMatch)
                      ? [...seasonEpisodes, currentMatch]
                      : seasonEpisodes;
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
                          key={`${key}-${effSeason}-${effEpisode}`}
                          season={effSeason}
                          episode={effEpisode}
                          disabled={busyKey !== null}
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
                        aria-label={`Provider episode for ${l.file_name}`}
                        value={l.matched_episode_id ?? ""}
                        disabled={busyKey !== null}
                        onChange={(e) => {
                          if (e.target.value === "__all_seasons__") {
                            setAllSeasonsFor(key);
                            return;
                          }
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
                        {choices.map((ep) => (
                          <option key={ep.id} value={ep.id}>
                            {labelEp(ep)}
                          </option>
                        ))}
                        {allSeasonsFor !== key &&
                          choices.length < provEpisodes.length && (
                            <option value="__all_seasons__">
                              Browse all seasons…
                            </option>
                          )}
                      </select>
                    </td>
                    <td className="p-2 text-right align-top">
                      {(isOverride || l.has_file_override) && (
                        <button
                          disabled={busyKey !== null}
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

function formatSE(
  s: number | null | undefined,
  e: number | null | undefined,
): string {
  if (s === null || s === undefined || e === null || e === undefined)
    return "—";
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
  const [e, setE] = useState<string>(
    episode != null ? String(episode ?? "") : "",
  );
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
        aria-label="Season number"
        inputMode="numeric"
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
        aria-label="Episode number"
        inputMode="numeric"
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
