import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, TvdbEpisode } from "../lib/api";

export default function EpisodeMapper({ path }: { path: string }) {
  const qc = useQueryClient();
  const data = useQuery({
    queryKey: ["episodes", path],
    queryFn: () => api.episodes.list(path),
  });
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [seasonFilter, setSeasonFilter] = useState<number | "all">("all");

  const tvdb = data.data?.tvdb_episodes ?? [];
  const locals = data.data?.locals ?? [];

  const seasonOptions = useMemo(() => {
    const set = new Set<number>();
    for (const l of locals) set.add(l.parsed_season);
    return Array.from(set).sort((a, b) => a - b);
  }, [locals]);

  const filtered = useMemo(() => {
    if (seasonFilter === "all") return locals;
    return locals.filter((l) => l.parsed_season === seasonFilter);
  }, [locals, seasonFilter]);

  if (data.isLoading)
    return <div className="text-sm text-slate-500">Loading episodes…</div>;
  if (data.error)
    return (
      <div className="text-sm text-amber-400">
        {(data.error as any).message ?? "Failed to load episodes"}
      </div>
    );

  const setOverride = async (
    season: number,
    episode: number,
    tvdbId: string | null,
  ) => {
    const key = `${season}-${episode}`;
    setBusyKey(key);
    setMsg(null);
    try {
      await api.episodes.override({
        folder_path: path,
        season,
        episode,
        tvdb_episode_id: tvdbId,
      });
      setMsg(
        tvdbId
          ? `Override saved for S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}.`
          : `Cleared override for S${String(season).padStart(2, "0")}E${String(episode).padStart(2, "0")}.`,
      );
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
          {locals.length} local file{locals.length === 1 ? "" : "s"} · {tvdb.length} TVDB episodes
        </span>
        <div className="flex-1" />
        <label className="text-xs text-slate-400 flex items-center gap-1">
          Season
          <select
            value={String(seasonFilter)}
            onChange={(e) =>
              setSeasonFilter(e.target.value === "all" ? "all" : parseInt(e.target.value, 10))
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
      </div>
      {msg && <div className="text-xs text-slate-400 mb-2">{msg}</div>}
      {filtered.length === 0 ? (
        <div className="text-sm text-slate-500">
          No local episode files detected. Make sure your folder layout has Season folders
          with video files inside.
        </div>
      ) : (
        <div className="border border-slate-800 rounded overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-slate-900 text-slate-400 text-left">
              <tr>
                <th className="p-2 w-32">Local</th>
                <th className="p-2">File</th>
                <th className="p-2 w-20">Match</th>
                <th className="p-2">TVDB Episode</th>
                <th className="p-2 w-24"></th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((l) => {
                const key = `${l.parsed_season}-${l.parsed_episode}`;
                const isOverride = !!l.override_episode_id;
                const isAuto = !!l.matched_episode_id && !isOverride;
                const isUnmatched = !l.matched_episode_id;
                return (
                  <tr
                    key={key}
                    className={`border-t border-slate-800 ${
                      isOverride ? "bg-amber-900/10" : isUnmatched ? "bg-rose-900/10" : ""
                    }`}
                  >
                    <td className="p-2 font-mono text-xs text-slate-300">
                      S{String(l.parsed_season).padStart(2, "0")}E
                      {String(l.parsed_episode).padStart(2, "0")}
                    </td>
                    <td className="p-2 text-xs text-slate-400 truncate max-w-md">
                      {l.file_name}
                    </td>
                    <td className="p-2">
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
                    <td className="p-2">
                      <select
                        value={l.matched_episode_id ?? ""}
                        disabled={busyKey === key}
                        onChange={(e) =>
                          setOverride(
                            l.parsed_season,
                            l.parsed_episode,
                            e.target.value || null,
                          )
                        }
                        className="bg-slate-800 px-2 py-1 rounded text-xs border border-slate-700 w-full"
                      >
                        <option value="">— unmatched —</option>
                        {tvdb.map((ep) => (
                          <option key={ep.id} value={ep.id}>
                            {labelEp(ep)}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="p-2 text-right">
                      {isOverride && (
                        <button
                          disabled={busyKey === key}
                          onClick={() =>
                            setOverride(l.parsed_season, l.parsed_episode, null)
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
    </div>
  );
}

function labelEp(ep: TvdbEpisode): string {
  const s = ep.season ?? 0;
  const n = ep.number ?? 0;
  const code = `S${String(s).padStart(2, "0")}E${String(n).padStart(2, "0")}`;
  return ep.name ? `${code} — ${ep.name}` : code;
}
