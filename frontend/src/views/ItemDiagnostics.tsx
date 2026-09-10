import { errorMessage } from "../lib/errors";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useConfirm } from "../components/confirm";

// v0.11.7 — inline diagnostic panel that explains *why* the folder has the
// nfo_status the library list is showing. Lazily fetches /items/nfo-explain
// when first opened. Renders a friendly summary line, the bulleted reason
// list, and (for series) a per-season coverage table including which
// specific episode files have no .nfo yet. Designed to make the difference
// between "partial", "foreign", "mixed", and "none" actionable instead of
// just a bucket label.
// v0.11.10 — Orphan companion sweeper. Renders inline only when the folder
// has at least one orphaned `<stem>.nfo` or `<stem>-thumb.*` left behind by
// a Sonarr/Radarr release upgrade. Cleanup requires review and explicit confirmation.
export function OrphansPanel({
  path,
  title,
  cachedOrphanCount,
}: {
  path: string;
  title: string;
  /**
   * v0.11.11 — the scanner caches the orphan count on item_state so we can
   * skip the per-folder disk walk on the detail page when there's nothing
   * to show. ``null`` means "unknown — fetch to confirm". ``0`` is the
   * fast path: don't render the panel and don't hit the backend at all.
   */
  cachedOrphanCount: number | null;
}) {
  const qc = useQueryClient();
  const confirmDlg = useConfirm();
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const skipFetch = cachedOrphanCount === 0;
  const q = useQuery({
    queryKey: ["orphans", path],
    queryFn: () => api.items.orphansPreview(path),
    staleTime: 60_000,
    enabled: !skipFetch,
  });
  if (skipFetch) return null;
  const total = (q.data?.nfo_removed ?? 0) + (q.data?.thumb_removed ?? 0);
  if (q.isLoading) return null;
  if (q.error)
    return (
      <p role="alert" className="panel p-3 mb-3 text-xs text-amber-300">
        Could not inspect orphaned companions: {q.error.message}{" "}
        <button className="btn" onClick={() => q.refetch()}>
          Retry
        </button>
      </p>
    );
  if (!q.data || total === 0) return null;

  const files = q.data.files ?? [];
  const head = files.slice(0, 12).join("\n  • ");
  const more = files.length > 12 ? `\n  … and ${files.length - 12} more` : "";

  const runSweep = async () => {
    if (busy) return;
    const ok = await confirmDlg({
      title: `Remove ${total} orphaned sidecar${total === 1 ? "" : "s"} for “${title}”?`,
      message:
        `These files have no matching video file in the folder — they were left ` +
        `behind when Sonarr/Radarr swapped a release. Orphaned metadata can ` +
        `cause duplicate Plex entries. Review the files before deleting.\n\n` +
        `Will delete:\n  • ${head}${more}\n\n` +
        `tvshow.nfo, season.nfo, every show/season-level artwork file, and every ` +
        `video / subtitle / audio file are preserved. This cannot be undone.`,
      confirmLabel: "Remove orphans",
      tone: "danger",
    });
    if (!ok) return;
    setBusy(true);
    setMsg("Removing orphaned sidecars…");
    try {
      const res = await api.items.orphansSweep({
        folder_path: path,
        dry_run: false,
        rescan: true,
      });
      setMsg(
        `Removed ${res.nfo_removed} orphaned NFO(s) and ${res.thumb_removed} orphaned ` +
          `thumbnail(s).`,
      );
      await qc.invalidateQueries({ queryKey: ["orphans", path] });
      await qc.invalidateQueries({ queryKey: ["detail", path] });
      await qc.invalidateQueries({ queryKey: ["nfo-explain", path] });
      await qc.invalidateQueries({ queryKey: ["items"] });
    } catch (e: unknown) {
      setMsg(`Orphan sweep failed: ${errorMessage(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mb-4 rounded border border-amber-500/40 bg-amber-500/[0.03] p-3">
      <div className="flex flex-wrap items-start gap-3">
        <span
          aria-hidden
          className="inline-flex items-center justify-center w-6 h-6 rounded bg-amber-400 text-black text-xs font-black flex-none"
          title="Hazard"
        >
          ⚠
        </span>
        <div className="flex-1 min-w-[200px]">
          <div className="text-sm font-semibold text-amber-200">
            Orphaned sidecars detected
          </div>
          <div className="text-xs text-slate-400 mt-0.5 leading-relaxed">
            {q.data.nfo_removed} NFO{q.data.nfo_removed === 1 ? "" : "s"} and{" "}
            {q.data.thumb_removed} thumbnail
            {q.data.thumb_removed === 1 ? "" : "s"} have no matching video.
            Review the files before cleanup.
          </div>
          {files.length > 0 && (
            <details className="mt-2 text-[11px] text-amber-100/90">
              <summary className="cursor-pointer text-amber-300/90 hover:text-amber-200">
                Show file list ({files.length})
              </summary>
              <ul className="mt-1 font-mono text-[11px] text-amber-100/80 max-h-40 overflow-auto pl-4 list-disc">
                {files.map((f) => (
                  <li key={f}>{f}</li>
                ))}
              </ul>
            </details>
          )}
          {msg && (
            <div className="text-[11px] text-amber-200/80 mt-2">{msg}</div>
          )}
        </div>
        <button
          onClick={runSweep}
          disabled={busy}
          className="btn btn-danger flex-none"
          title="Delete the orphaned <stem>.nfo and <stem>-thumb.* files for this folder. Live videos and show/season artwork are preserved."
        >
          Preview orphan cleanup
        </button>
      </div>
    </div>
  );
}

export function WhyStatusPanel({
  path,
  onClose,
}: {
  path: string;
  onClose: () => void;
}) {
  const q = useQuery({
    queryKey: ["nfo-explain", path],
    queryFn: () => api.items.nfoExplain(path),
    staleTime: 15_000,
  });
  const [expandSeasons, setExpandSeasons] = useState<Record<number, boolean>>(
    {},
  );

  return (
    <div className="mb-4 rounded-lg border border-slate-800 bg-slate-900/60">
      <div className="px-3 py-2 border-b border-slate-800 flex items-center gap-2">
        <div className="text-xs uppercase tracking-wide text-slate-300 font-semibold">
          Status breakdown
        </div>
        <div className="text-[11px] text-slate-500 flex-1">
          Why this folder is reported as <b>{q.data?.status ?? "…"}</b>.
        </div>
        <button
          type="button"
          onClick={() => q.refetch()}
          disabled={q.isFetching}
          className="text-[11px] px-2 py-0.5 rounded bg-slate-800 hover:bg-slate-700 border border-slate-700 disabled:opacity-50"
          title="Re-walk the folder and recompute the breakdown"
        >
          {q.isFetching ? "Recomputing…" : "Recompute"}
        </button>
        <button
          type="button"
          onClick={onClose}
          className="text-slate-400 hover:text-white text-sm"
          title="Hide breakdown"
        >
          ✕
        </button>
      </div>

      {q.isLoading && (
        <div className="p-3 text-xs text-slate-500">Walking folder…</div>
      )}
      {q.error && (
        <div className="p-3 text-xs text-rose-300">
          Couldn't compute breakdown: {errorMessage(q.error)}
        </div>
      )}
      {q.data && (
        <div className="p-3 space-y-3">
          {/* Top counters */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px]">
            <CounterCell label="Status" value={q.data.status} />
            {q.data.kind === "series" ? (
              <>
                <CounterCell
                  label="tvshow.nfo"
                  value={
                    !q.data.show_nfo?.present
                      ? "missing"
                      : q.data.show_nfo?.foreign
                        ? "foreign"
                        : "present"
                  }
                  tone={
                    !q.data.show_nfo?.present
                      ? "warn"
                      : q.data.show_nfo?.foreign
                        ? "warn"
                        : "ok"
                  }
                />
                <CounterCell
                  label="Episode NFOs"
                  value={`${q.data.nfo_count} / ${q.data.video_count}`}
                  tone={
                    q.data.video_count > 0 &&
                    q.data.nfo_count >= q.data.video_count
                      ? "ok"
                      : "warn"
                  }
                />
                <CounterCell
                  label="Foreign NFOs"
                  value={q.data.foreign_nfo_count}
                  tone={q.data.foreign_nfo_count === 0 ? "ok" : "warn"}
                />
              </>
            ) : (
              <>
                <CounterCell
                  label="movie.nfo"
                  value={
                    !q.data.movie_nfo?.present
                      ? "missing"
                      : q.data.movie_nfo?.foreign
                        ? "foreign"
                        : "present"
                  }
                  tone={
                    !q.data.movie_nfo?.present
                      ? "warn"
                      : q.data.movie_nfo?.foreign
                        ? "warn"
                        : "ok"
                  }
                />
                <CounterCell label="Video files" value={q.data.video_count} />
              </>
            )}
          </div>

          {/* Reasons */}
          {q.data.reasons && q.data.reasons.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
                Reasons
              </div>
              <ul className="text-xs text-slate-300 list-disc pl-5 space-y-0.5">
                {q.data.reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Per-season table */}
          {q.data.kind === "series" && q.data.seasons.length > 0 && (
            <div>
              <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
                Per-season coverage
              </div>
              <div className="rounded border border-slate-800 overflow-auto">
                <table className="w-full text-xs">
                  <thead className="bg-slate-900 text-slate-400">
                    <tr>
                      <th className="text-left px-2 py-1">Season</th>
                      <th className="text-right px-2 py-1">Videos</th>
                      <th className="text-right px-2 py-1">NFOs</th>
                      <th className="text-right px-2 py-1">Missing</th>
                      <th className="text-right px-2 py-1">Foreign</th>
                      <th className="text-right px-2 py-1">season.nfo</th>
                      <th className="px-2 py-1"></th>
                    </tr>
                  </thead>
                  <tbody>
                    {q.data.seasons.map((s) => {
                      const expanded = !!expandSeasons[s.season];
                      const hasDetails =
                        s.missing.length > 0 || s.foreign.length > 0;
                      const trouble =
                        s.nfo_count < s.video_count || s.foreign_nfo_count > 0;
                      return (
                        <tr
                          key={s.season}
                          className={`border-t border-slate-800 ${
                            trouble ? "bg-amber-950/30" : ""
                          }`}
                        >
                          <td className="px-2 py-1 align-top">
                            S{String(s.season).padStart(2, "0")}
                            {expanded && hasDetails && (
                              <ExpandedSeasonDetails s={s} />
                            )}
                          </td>
                          <td className="px-2 py-1 text-right align-top">
                            {s.video_count}
                          </td>
                          <td
                            className={`px-2 py-1 text-right align-top ${
                              s.nfo_count < s.video_count
                                ? "text-amber-300"
                                : ""
                            }`}
                          >
                            {s.nfo_count}
                          </td>
                          <td
                            className={`px-2 py-1 text-right align-top ${
                              s.missing_total > 0
                                ? "text-amber-300"
                                : "text-slate-500"
                            }`}
                          >
                            {s.missing_total}
                          </td>
                          <td
                            className={`px-2 py-1 text-right align-top ${
                              s.foreign_total > 0
                                ? "text-amber-300"
                                : "text-slate-500"
                            }`}
                          >
                            {s.foreign_total}
                          </td>
                          <td className="px-2 py-1 text-right align-top">
                            {s.season_nfo ? (
                              <span className="text-emerald-400">yes</span>
                            ) : (
                              <span className="text-slate-500">no</span>
                            )}
                          </td>
                          <td className="px-2 py-1 text-right align-top">
                            {hasDetails && (
                              <button
                                type="button"
                                className="text-[11px] text-indigo-300 hover:text-indigo-200"
                                onClick={() =>
                                  setExpandSeasons((prev) => ({
                                    ...prev,
                                    [s.season]: !prev[s.season],
                                  }))
                                }
                              >
                                {expanded ? "hide" : "show files"}
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {q.data.orphan_root_videos &&
            q.data.orphan_root_videos.length > 0 && (
              <div>
                <div className="text-[10px] uppercase tracking-wide text-slate-500 mb-1">
                  Loose videos at series root
                </div>
                <ul className="text-[11px] text-slate-400 font-mono list-disc pl-5 space-y-0.5">
                  {q.data.orphan_root_videos.slice(0, 25).map((n) => (
                    <li key={n} className="break-all">
                      {n}
                    </li>
                  ))}
                  {q.data.orphan_root_videos.length > 25 && (
                    <li>+{q.data.orphan_root_videos.length - 25} more…</li>
                  )}
                </ul>
              </div>
            )}

          <div className="text-[11px] text-slate-500 leading-snug">
            <b>partial</b>: tvshow.nfo exists but some episodes don't have NFOs.{" "}
            <b>mixed</b>: tvshow.nfo plus a mix of builder-written and outside
            NFOs. <b>foreign</b>: NFOs exist but none were written by
            plex-nfo-builder. Use <i>Build NFOs</i> to fill missing episode
            metadata. Foreign NFOs are preserved unless you enable overwriting
            them in Settings.
          </div>
        </div>
      )}
    </div>
  );
}

function ExpandedSeasonDetails({
  s,
}: {
  s: {
    season: number;
    missing: string[];
    missing_total: number;
    foreign: string[];
    foreign_total: number;
  };
}) {
  return (
    <div className="mt-2 space-y-1.5 text-[11px] font-mono text-slate-400 max-w-md">
      {s.missing.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-amber-300 font-sans">
            Missing NFO
          </div>
          <ul className="list-disc pl-5">
            {s.missing.map((n) => (
              <li key={`m-${n}`} className="break-all">
                {n}
              </li>
            ))}
            {s.missing_total > s.missing.length && (
              <li>+{s.missing_total - s.missing.length} more…</li>
            )}
          </ul>
        </div>
      )}
      {s.foreign.length > 0 && (
        <div>
          <div className="text-[10px] uppercase tracking-wide text-amber-300 font-sans">
            Foreign NFO (no provenance)
          </div>
          <ul className="list-disc pl-5">
            {s.foreign.map((n) => (
              <li key={`f-${n}`} className="break-all">
                {n}
              </li>
            ))}
            {s.foreign_total > s.foreign.length && (
              <li>+{s.foreign_total - s.foreign.length} more…</li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

function CounterCell({
  label,
  value,
  tone,
}: {
  label: string;
  value: string | number;
  tone?: "ok" | "warn";
}) {
  const toneCls =
    tone === "ok"
      ? "text-emerald-300"
      : tone === "warn"
        ? "text-amber-300"
        : "text-slate-200";
  return (
    <div className="bg-slate-950 border border-slate-800 rounded px-2 py-1.5">
      <div className="text-[9px] uppercase tracking-wide text-slate-500">
        {label}
      </div>
      <div className={`text-sm font-medium mt-0.5 ${toneCls}`}>
        {String(value)}
      </div>
    </div>
  );
}
