import { useState } from "react";
import { api, type Item } from "../lib/api";

const LABELS: Record<string, string> = {
  none: "Not built",
  partial: "Partial",
  complete: "Complete",
  stale: "Out of date",
  foreign: "External NFO",
  mixed: "Mixed sources",
};
export function StatusBadge({ status }: { status: Item["nfo_status"] }) {
  return (
    <span className={`status-pill status-${status ?? "none"}`}>
      {LABELS[status ?? "none"] ?? status}
    </span>
  );
}

type Props = {
  items: Item[];
  selected: Set<string>;
  onToggle: (path: string) => void;
  onOpen: (path: string) => void;
};
export function LibraryGrid({ items, selected, onToggle, onOpen }: Props) {
  return (
    <div className="poster-grid">
      {items.map((item) => (
        <Poster
          key={item.folder_path}
          item={item}
          checked={selected.has(item.folder_path)}
          onToggle={onToggle}
          onOpen={onOpen}
        />
      ))}
    </div>
  );
}
function Poster({
  item,
  checked,
  onToggle,
  onOpen,
}: {
  item: Item;
  checked: boolean;
  onToggle: Props["onToggle"];
  onOpen: Props["onOpen"];
}) {
  const [failedUrl, setFailedUrl] = useState<string | null>(null);
  const posterUrl = item.poster_path
    ? `${api.artwork.fileUrl(item.poster_path)}&t=${item.last_built ?? 0}`
    : null;
  return (
    <article
      className={`poster-tile group ${checked ? "ring-2 ring-indigo-400 ring-offset-4 ring-offset-slate-950" : ""}`}
    >
      <button
        className="poster-select"
        aria-label={`Select ${item.title}`}
        aria-pressed={checked}
        onClick={() => onToggle(item.folder_path)}
      >
        {checked ? "✓" : "+"}
      </button>
      <button
        className="block text-left w-full"
        onClick={() => onOpen(item.folder_path)}
        aria-label={`Open ${item.title}`}
      >
        <div className="poster-frame">
          {posterUrl && failedUrl !== posterUrl ? (
            <img
              src={posterUrl}
              alt=""
              loading="lazy"
              decoding="async"
              onError={() => setFailedUrl(posterUrl)}
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="px-5 text-center">
              <span
                className="block font-mono text-3xl text-slate-600 mb-5"
                aria-hidden="true"
              >
                {item.kind === "movie" ? "▶" : "▤"}
              </span>
              <span className="block text-sm font-medium text-slate-400 line-clamp-3">
                {item.title}
              </span>
              <span className="block uppercase tracking-widest text-[9px] text-slate-600 mt-3">
                Artwork pending
              </span>
            </div>
          )}
        </div>
        <div className="pt-3">
          <h2
            className="text-[13px] font-medium truncate text-slate-200"
            title={item.title}
          >
            {item.title}
          </h2>
          <div className="flex items-center justify-between gap-2 mt-1.5">
            <span className="text-[11px] text-slate-500">
              {item.year ?? "Unknown year"}
            </span>
            <StatusBadge status={item.nfo_status} />
          </div>
        </div>
      </button>
    </article>
  );
}
export function LibraryList({ items, selected, onToggle, onOpen }: Props) {
  return (
    <div className="panel overflow-x-auto">
      <table className="w-full text-sm text-left min-w-[600px]">
        <thead className="text-[11px] text-slate-500 uppercase tracking-wider">
          <tr>
            <th>
              <span className="sr-only">Select</span>
            </th>
            <th>Title</th>
            <th>Source</th>
            <th>On disk</th>
            <th>NFO status</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr
              key={item.folder_path}
              className={`border-t border-slate-800 hover:bg-slate-800/50 ${selected.has(item.folder_path) ? "bg-indigo-950/40" : ""}`}
            >
              <td className="w-10">
                <input
                  type="checkbox"
                  aria-label={`Select ${item.title}`}
                  checked={selected.has(item.folder_path)}
                  onChange={() => onToggle(item.folder_path)}
                />
              </td>
              <td>
                <button
                  className="text-left hover:text-indigo-300"
                  onClick={() => onOpen(item.folder_path)}
                >
                  <span className="font-medium">{item.title}</span>
                  <span className="text-xs text-slate-500 ml-2">
                    {item.year}
                  </span>
                </button>
              </td>
              <td className="text-xs text-slate-400">
                {item.external_id
                  ? `${item.provider?.toUpperCase()} · ${item.external_id}`
                  : "Unmatched"}
              </td>
              <td className="text-xs text-slate-400">
                {item.kind === "series"
                  ? `${item.episode_count_local ?? 0} episodes`
                  : "Movie"}
              </td>
              <td>
                <StatusBadge status={item.nfo_status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
