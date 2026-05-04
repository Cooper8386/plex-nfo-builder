import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ArtworkCandidate, ArtworkProvider } from "../lib/api";

const SLOT_LABELS: Record<string, string> = {
  poster: "Poster",
  background: "Background / Fanart",
  banner: "Banner",
  clearlogo: "Clear Logo",
  clearart: "Clear Art",
};

function slotLabel(slot: string): string {
  if (SLOT_LABELS[slot]) return SLOT_LABELS[slot];
  const m = slot.match(/^season-(\d+)-poster$/);
  if (m) return `Season ${parseInt(m[1], 10)} Poster`;
  return slot;
}

function aspectFor(slot: string): string {
  if (slot.includes("background") || slot.includes("banner")) return "aspect-[16/9]";
  return "aspect-[2/3]";
}

const PROVIDER_BADGES: Record<ArtworkProvider, { label: string; cls: string }> = {
  tvdb: { label: "TVDB", cls: "bg-blue-700/80 text-blue-50" },
  tmdb: { label: "TMDB", cls: "bg-emerald-700/80 text-emerald-50" },
  fanart: { label: "fanart", cls: "bg-purple-700/80 text-purple-50" },
  custom: { label: "Custom", cls: "bg-amber-600/80 text-amber-50" },
};

const PROVIDER_FILTERS: { key: "all" | ArtworkProvider; label: string }[] = [
  { key: "all", label: "All" },
  { key: "tvdb", label: "TVDB" },
  { key: "tmdb", label: "TMDB" },
  { key: "fanart", label: "fanart.tv" },
  { key: "custom", label: "Custom" },
];

export default function ArtworkPicker({
  path,
  kind,
}: {
  path: string;
  kind: "series" | "movie";
}) {
  const qc = useQueryClient();
  const candidates = useQuery({
    queryKey: ["artwork-candidates", path, kind],
    queryFn: () => api.artwork.candidates(path, kind),
  });
  const [activeSlot, setActiveSlot] = useState<string | null>(null);
  const [providerFilter, setProviderFilter] = useState<"all" | ArtworkProvider>("all");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  const slotKeys = useMemo(() => {
    const slots = candidates.data?.slots ?? {};
    const ordered: string[] = [];
    for (const s of ["poster", "background", "banner", "clearlogo", "clearart"]) {
      if (slots[s]) ordered.push(s);
    }
    const seasons = Object.keys(slots)
      .filter((k) => k.startsWith("season-") && k.endsWith("-poster"))
      .sort();
    return [...ordered, ...seasons];
  }, [candidates.data]);

  if (candidates.isLoading)
    return <div className="text-sm text-slate-500">Loading artwork…</div>;
  if (candidates.error)
    return (
      <div className="text-sm text-amber-400">
        {(candidates.error as any).message ?? "Failed to load artwork"}
      </div>
    );

  const slots = candidates.data?.slots ?? {};
  const selections = candidates.data?.selections ?? {};
  const current = activeSlot ?? slotKeys[0] ?? null;
  const allList: ArtworkCandidate[] = current ? slots[current] ?? [] : [];
  const list = providerFilter === "all"
    ? allList
    : allList.filter((c) => (c.provider ?? "tvdb") === providerFilter);
  const activeSelection = current ? selections[current] : undefined;

  const select = async (slot: string, c: ArtworkCandidate) => {
    setBusy(true);
    setMsg(null);
    try {
      await api.artwork.select({
        folder_path: path,
        slot,
        url: c.url,
        language: c.language ?? undefined,
        score: c.score,
      });
      setMsg(`Saved selection for ${slotLabel(slot)}. Run a build to apply.`);
      await qc.invalidateQueries({ queryKey: ["artwork-candidates", path] });
    } catch (e: any) {
      setMsg(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const clear = async (slot?: string) => {
    setBusy(true);
    setMsg(null);
    try {
      await api.artwork.clear({ folder_path: path, slot });
      setMsg(slot ? `Reset ${slotLabel(slot)} to auto.` : "Cleared all artwork selections.");
      await qc.invalidateQueries({ queryKey: ["artwork-candidates", path] });
    } catch (e: any) {
      setMsg(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleUpload = async (file: File) => {
    if (!current) {
      setMsg("Pick a slot first so we know where to attach the upload.");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      await api.artwork.upload(path, file, current);
      setMsg(`Uploaded ${file.name}. It is available under Custom.`);
      await qc.invalidateQueries({ queryKey: ["artwork-candidates", path] });
    } catch (e: any) {
      setMsg(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleAddUrl = async () => {
    if (!current) {
      setMsg("Pick a slot first so we know where to attach the URL.");
      return;
    }
    const url = window.prompt("Image URL (http or https)");
    if (!url) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.artwork.addUrl({ folder_path: path, url: url.trim(), slot: current });
      setMsg("Added image URL. It is available under Custom.");
      await qc.invalidateQueries({ queryKey: ["artwork-candidates", path] });
    } catch (e: any) {
      setMsg(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  const handleDeleteCustom = async (id: string) => {
    if (!window.confirm("Remove this custom artwork?")) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.artwork.deleteCustom(id);
      await qc.invalidateQueries({ queryKey: ["artwork-candidates", path] });
    } catch (e: any) {
      setMsg(e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <div className="flex flex-wrap gap-1">
          {slotKeys.map((s) => {
            const isActive = current === s;
            const hasSelection = !!selections[s];
            return (
              <button
                key={s}
                onClick={() => setActiveSlot(s)}
                className={`text-xs px-2.5 py-1 rounded border transition ${
                  isActive
                    ? "bg-indigo-600 border-indigo-500 text-white"
                    : "bg-slate-800 border-slate-700 hover:border-slate-500"
                }`}
              >
                {slotLabel(s)}
                {hasSelection && (
                  <span className="ml-1 text-[10px] text-amber-300">★</span>
                )}
              </button>
            );
          })}
        </div>
        <div className="flex-1" />
        {current && activeSelection && (
          <button
            disabled={busy}
            onClick={() => clear(current)}
            className="text-xs px-2 py-1 rounded bg-slate-800 border border-slate-700 hover:border-amber-500 disabled:opacity-40"
          >
            Reset {slotLabel(current)} to auto
          </button>
        )}
        <button
          disabled={busy || Object.keys(selections).length === 0}
          onClick={() => clear()}
          className="text-xs px-2 py-1 rounded bg-slate-800 border border-slate-700 hover:border-amber-500 disabled:opacity-40"
        >
          Clear all picks
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="text-[11px] uppercase tracking-wide text-slate-500 mr-1">Source</span>
        {PROVIDER_FILTERS.map((f) => {
          const isActive = providerFilter === f.key;
          const count = f.key === "all"
            ? allList.length
            : allList.filter((c) => (c.provider ?? "tvdb") === f.key).length;
          return (
            <button
              key={f.key}
              onClick={() => setProviderFilter(f.key)}
              disabled={f.key !== "all" && count === 0}
              className={`text-xs px-2 py-0.5 rounded border transition ${
                isActive
                  ? "bg-slate-700 border-slate-500 text-white"
                  : "bg-slate-900 border-slate-800 text-slate-300 hover:border-slate-600 disabled:opacity-30"
              }`}
            >
              {f.label}
              <span className="ml-1 text-[10px] text-slate-400">{count}</span>
            </button>
          );
        })}
        <div className="flex-1" />
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void handleUpload(f);
            e.target.value = "";
          }}
        />
        <button
          disabled={busy || !current}
          onClick={() => fileRef.current?.click()}
          className="text-xs px-2 py-1 rounded bg-emerald-700/70 border border-emerald-600 hover:bg-emerald-600 disabled:opacity-40"
          title={current ? `Upload to ${slotLabel(current)}` : "Pick a slot first"}
        >
          Upload image
        </button>
        <button
          disabled={busy || !current}
          onClick={handleAddUrl}
          className="text-xs px-2 py-1 rounded bg-slate-800 border border-slate-600 hover:border-emerald-500 disabled:opacity-40"
          title={current ? `Add URL to ${slotLabel(current)}` : "Pick a slot first"}
        >
          Add from URL
        </button>
      </div>

      {msg && <div className="text-xs text-slate-400 mb-2">{msg}</div>}
      {!current && (
        <div className="text-sm text-slate-500">No artwork slots available for this title yet.</div>
      )}
      {current && list.length === 0 && (
        <div className="text-sm text-slate-500">
          {providerFilter === "all"
            ? `No ${slotLabel(current).toLowerCase()} candidates for this title.`
            : `No ${PROVIDER_FILTERS.find((f) => f.key === providerFilter)?.label} candidates for ${slotLabel(current).toLowerCase()}.`}
        </div>
      )}
      {current && list.length > 0 && (
        <div
          className={`grid gap-3 ${
            current.includes("background") || current.includes("banner")
              ? "grid-cols-[repeat(auto-fill,minmax(220px,1fr))]"
              : "grid-cols-[repeat(auto-fill,minmax(140px,1fr))]"
          }`}
        >
          {list.map((c, i) => {
            const selected = activeSelection?.url === c.url;
            const provider = (c.provider ?? "tvdb") as ArtworkProvider;
            const badge = PROVIDER_BADGES[provider];
            return (
              <div
                key={(c.id ?? i) + ":" + c.url}
                className={`group relative rounded overflow-hidden border-2 transition ${
                  selected
                    ? "border-indigo-500 ring-2 ring-indigo-500/40"
                    : "border-slate-800 hover:border-indigo-500"
                }`}
              >
                <button
                  disabled={busy}
                  onClick={() => select(current, c)}
                  className="block w-full text-left"
                  title={`${badge.label}${c.language ? " · " + c.language : ""} · score ${c.score}${c.origin ? "\n" + c.origin : ""}`}
                >
                  <div className={`bg-slate-800 ${aspectFor(current)}`}>
                    <img
                      src={c.thumb}
                      alt=""
                      className="w-full h-full object-cover"
                      loading="lazy"
                    />
                  </div>
                  <div className="absolute top-1 left-1 flex gap-1">
                    <span className={`text-[10px] px-1 py-0.5 rounded ${badge.cls}`}>
                      {badge.label}
                    </span>
                    {c.language && (
                      <span className="bg-black/70 text-[10px] px-1 py-0.5 rounded text-slate-200">
                        {c.language}
                      </span>
                    )}
                  </div>
                  <div className="absolute top-1 right-1">
                    <span className="bg-black/70 text-[10px] px-1 py-0.5 rounded text-slate-300">
                      {c.score}
                    </span>
                  </div>
                  {selected && (
                    <div className="absolute bottom-1 left-1 right-1 text-center text-[10px] uppercase tracking-wide bg-indigo-600/90 rounded py-0.5">
                      selected
                    </div>
                  )}
                </button>
                {provider === "custom" && c.id && (
                  <button
                    disabled={busy}
                    onClick={(e) => {
                      e.stopPropagation();
                      void handleDeleteCustom(String(c.id));
                    }}
                    className="absolute bottom-1 right-1 text-[10px] px-1.5 py-0.5 rounded bg-rose-700/90 hover:bg-rose-600 text-white"
                    title="Remove custom artwork"
                  >
                    ✕
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
