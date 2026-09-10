import { useState } from "react";
import { api, RenamePlanItem } from "../lib/api";
import Modal from "../components/Modal";
import { useConfirm } from "../components/confirm";

type RenameOptions = {
  template: string;
  series_type: "auto" | "standard" | "daily" | "anime";
  release_group: string;
};

/** A preview owns its options: edited inputs can never execute an older plan. */
export default function RenameModal({
  path,
  onClose,
  onApplied,
}: {
  path: string;
  onClose: () => void;
  onApplied: () => Promise<void>;
}) {
  const confirm = useConfirm();
  const [options, setOptions] = useState<RenameOptions>({
    template: "",
    series_type: "auto",
    release_group: "",
  });
  const [preview, setPreview] = useState<{
    options: RenameOptions;
    items: RenamePlanItem[];
  } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const current =
    preview !== null &&
    JSON.stringify(preview.options) === JSON.stringify(options);
  const selectedItems =
    preview?.items.filter(
      (item) => selected.has(item.src) && !item.unchanged && !item.conflict,
    ) ?? [];

  const load = async (next: RenameOptions, preserveMessage = false) => {
    setBusy(true);
    setError(null);
    if (!preserveMessage) setMessage(null);
    // Invalidate immediately, including when a provider or probe request fails.
    setPreview(null);
    try {
      const result = await api.episodes.rename.preview({
        folder_path: path,
        template: next.template || undefined,
        series_type: next.series_type,
        release_group: next.release_group.trim() || undefined,
      });
      setPreview({ options: next, items: result.items });
      setSelected(
        new Set(
          result.items
            .filter((item) => !item.unchanged && !item.conflict)
            .map((item) => item.src),
        ),
      );
    } catch (cause) {
      setError(
        `Preview failed: ${cause instanceof Error ? cause.message : String(cause)}`,
      );
    } finally {
      setBusy(false);
    }
  };

  const apply = async () => {
    if (!preview || !current || busy || selectedItems.length === 0) return;
    const approved = preview;
    const only_src = selectedItems.map((item) => item.src);
    setConfirming(true);
    const ok = await confirm({
      title: `Rename ${only_src.length} media file${only_src.length === 1 ? "" : "s"}?`,
      message: `Folder: ${path}\n\nThe selected names shown in this preview will be applied on disk. Matching NFOs, thumbnails and subtitles move with their media files. Overrides follow the new names. There is no automatic undo.`,
      confirmLabel: "Rename files",
      tone: "danger",
    });
    setConfirming(false);
    if (!ok) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api.episodes.rename.apply({
        folder_path: path,
        template: approved.options.template || undefined,
        series_type: approved.options.series_type,
        release_group: approved.options.release_group.trim() || undefined,
        only_src,
        expected_plan: selectedItems.map(({ src, dst }) => ({ src, dst })),
      });
      setMessage(
        `Renamed ${result.renamed.length} · skipped ${result.skipped.length} · failed ${result.failed.length}.${result.failed.length ? ` First failure: ${result.failed[0].reason}` : ""}`,
      );
      setPreview(null);
      await onApplied();
      await load(approved.options, true);
    } catch (cause) {
      setPreview(null);
      setError(
        `Rename failed: ${cause instanceof Error ? cause.message : String(cause)}. Refresh the preview to inspect the current files before retrying.`,
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="Rename to scheme"
      onClose={() => {
        if (!busy && !confirming) onClose();
      }}
      className="w-full max-w-5xl flex flex-col max-h-[90dvh]"
    >
      <p className="px-5 pt-3 text-xs text-slate-400 font-mono break-all">
        {path}
      </p>
      <div className="p-4 sm:px-5 border-b border-slate-800 space-y-3">
        <fieldset
          disabled={busy || confirming}
          className="grid grid-cols-1 sm:grid-cols-2 gap-3"
        >
          <label className="text-xs text-slate-400 space-y-1">
            Series type
            <select
              className="field block w-full"
              value={options.series_type}
              onChange={(event) =>
                setOptions({
                  ...options,
                  series_type: event.target
                    .value as RenameOptions["series_type"],
                })
              }
            >
              <option value="auto">Auto-detect per file</option>
              <option value="standard">Standard</option>
              <option value="daily">Daily</option>
              <option value="anime">Anime</option>
            </select>
          </label>
          <label className="text-xs text-slate-400 space-y-1">
            Release group override
            <input
              className="field block w-full"
              placeholder="Automatic · e.g. SubsPlease"
              value={options.release_group}
              onChange={(event) =>
                setOptions({ ...options, release_group: event.target.value })
              }
            />
          </label>
          <label className="sm:col-span-2 text-xs text-slate-400 space-y-1">
            Template override
            <input
              className="field block w-full font-mono"
              placeholder="Use configured templates for each series type"
              value={options.template}
              onChange={(event) =>
                setOptions({ ...options, template: event.target.value })
              }
            />
          </label>
        </fieldset>
        <div className="flex flex-wrap gap-3 items-center">
          <button
            className="btn btn-primary"
            disabled={busy || confirming}
            onClick={() => load(options)}
          >
            {busy ? "Working…" : "Generate preview"}
          </button>
          <span className="text-xs text-slate-400">
            Preview reads filenames and media information. No files change.
          </span>
        </div>
        {preview && !current && (
          <p role="status" className="text-xs text-amber-300">
            Options changed. Generate a new preview before renaming.
          </p>
        )}
      </div>
      <div className="overflow-auto flex-1 min-h-32">
        {!preview ? (
          <p className="p-6 text-sm text-slate-400">
            {busy
              ? "Reading files and preparing names…"
              : "Generate a preview to inspect every proposed filename."}
          </p>
        ) : preview.items.length === 0 ? (
          <p className="p-6 text-sm text-slate-400">
            No renameable files. Map unrecognized episodes before renaming.
          </p>
        ) : (
          <table className="w-full text-xs min-w-[580px]">
            <thead className="bg-slate-900 sticky top-0 text-left text-slate-400">
              <tr>
                <th className="p-3">
                  <span className="sr-only">Select</span>
                </th>
                <th className="p-3">Current filename</th>
                <th className="p-3">Proposed filename</th>
                <th className="p-3">Status</th>
              </tr>
            </thead>
            <tbody>
              {preview.items.map((item) => (
                <tr key={item.src} className="border-t border-slate-800">
                  <td className="p-3">
                    <input
                      type="checkbox"
                      aria-label={`Rename ${item.src_name}`}
                      checked={selected.has(item.src)}
                      disabled={
                        busy ||
                        confirming ||
                        !current ||
                        item.unchanged ||
                        !!item.conflict
                      }
                      onChange={(event) =>
                        setSelected((before) => {
                          const next = new Set(before);
                          if (event.target.checked) next.add(item.src);
                          else next.delete(item.src);
                          return next;
                        })
                      }
                    />
                  </td>
                  <td className="p-3 font-mono text-slate-400 break-all">
                    {item.src_name}
                  </td>
                  <td className="p-3 font-mono break-all">{item.dst_name}</td>
                  <td
                    className={`p-3 whitespace-nowrap ${item.conflict ? "text-rose-300" : "text-slate-400"}`}
                  >
                    {item.conflict
                      ? `Conflict: ${item.conflict}`
                      : item.unchanged
                        ? "Unchanged"
                        : "Ready"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <footer className="p-4 sm:px-5 border-t border-slate-800 space-y-3">
        {error && (
          <p role="alert" className="text-xs text-rose-300">
            {error}
          </p>
        )}
        {message && (
          <p role="status" className="text-xs text-slate-300">
            {message}
          </p>
        )}
        <div className="flex gap-2 items-center flex-wrap">
          <p className="text-xs text-slate-400 flex-1">
            {selectedItems.length} media files selected. Companions move with
            them.
          </p>
          <button
            className="btn"
            disabled={busy || confirming}
            onClick={onClose}
          >
            Close
          </button>
          <button
            className="btn btn-danger"
            disabled={!current || busy || confirming || !selectedItems.length}
            onClick={apply}
          >
            Rename {selectedItems.length} files…
          </button>
        </div>
      </footer>
    </Modal>
  );
}
