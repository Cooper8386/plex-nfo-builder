import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { useConfirm } from "../components/confirm";

export default function LibraryMaintenance(props: {
  library: string;
  busy: string | null;
  setBusy: (v: string | null) => void;
  flash: (msg: string) => void;
  invalidateItems: () => void;
  btnHazard: string;
  btnHazardOutline: string;
}) {
  const [open, setOpen] = useState(false);
  const confirmDlg = useConfirm();
  const queryClient = useQueryClient();

  const runWipeNfo = async () => {
    if (props.busy) return;
    props.setBusy("Scanning library for NFOs and artwork…");
    let preview: { folder_count: number; file_count?: number };
    try {
      preview = await api.libraries.wipeNfo(props.library, { dry_run: true });
    } catch (e: unknown) {
      props.flash(
        `Wipe preview failed: ${e instanceof Error ? e.message : String(e)}`,
      );
      props.setBusy(null);
      return;
    }
    props.setBusy(null);
    if (!preview.file_count) {
      props.flash(
        `Nothing to wipe in "${props.library}" — checked ${preview.folder_count} folder(s).`,
      );
      return;
    }
    const ok = await confirmDlg({
      title: `Wipe NFOs + artwork across “${props.library}”?`,
      message:
        `This will delete ${preview.file_count} file(s) across ` +
        `${preview.folder_count} folder(s):\n` +
        `  • Every tvshow.nfo / movie .nfo / episode .nfo / season.nfo\n` +
        `  • Every poster.jpg / background.jpg / banner.jpg / clearlogo.png\n` +
        `  • Every Season<NN>-poster.jpg / season-specials-poster.jpg\n` +
        `  • Every <episode>-thumb.jpg next to a video file\n\n` +
        `Sidecars (.plex-nfo-builder.json) and your media files are NOT touched. ` +
        `Bindings + overrides survive — you can rebuild straight after.\n\n` +
        `This cannot be undone.`,
      confirmLabel: "Wipe",
      tone: "danger",
    });
    if (!ok) return;
    props.setBusy(
      `Wiping NFOs + artwork from ${preview.folder_count} folder(s)…`,
    );
    try {
      const res = await api.libraries.wipeNfo(props.library, {
        dry_run: false,
      });
      props.flash(
        `Wiped ${res.nfo_deleted ?? 0} NFO(s) + ${res.artwork_deleted ?? 0} artwork file(s) ` +
          `across ${res.folder_count} folder(s)` +
          (res.failed && res.failed.length
            ? ` — ${res.failed.length} folder(s) failed`
            : ""),
      );
      props.invalidateItems();
    } catch (e: unknown) {
      props.flash(`Wipe failed: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      props.setBusy(null);
    }
  };

  const runSweepOrphans = async () => {
    if (props.busy) return;
    props.setBusy("Scanning library for orphaned NFO + thumbnail sidecars…");
    let preview: {
      folder_count: number;
      affected_folder_count: number;
      nfo_removed: number;
      thumb_removed: number;
      folders: {
        folder_path: string;
        nfo_removed: number;
        thumb_removed: number;
      }[];
    };
    try {
      preview = await api.libraries.sweepOrphans(props.library, {
        dry_run: true,
      });
    } catch (e: unknown) {
      props.flash(
        `Orphan scan failed: ${e instanceof Error ? e.message : String(e)}`,
      );
      props.setBusy(null);
      return;
    }
    props.setBusy(null);
    const total = preview.nfo_removed + preview.thumb_removed;
    if (!total) {
      props.flash(
        `No orphaned sidecars found in "${props.library}" — checked ${preview.folder_count} folder(s).`,
      );
      return;
    }
    const sample = preview.folders
      .slice(0, 6)
      .map(
        (f) =>
          `  • ${f.folder_path.split(/[\\/]/).pop()} (${f.nfo_removed + f.thumb_removed})`,
      )
      .join("\n");
    const more =
      preview.affected_folder_count > 6
        ? `\n  … and ${preview.affected_folder_count - 6} more folder(s)`
        : "";
    const ok = await confirmDlg({
      title: `Sweep orphaned sidecars across “${props.library}”?`,
      message:
        `Found ${preview.nfo_removed} orphaned NFO(s) and ${preview.thumb_removed} ` +
        `orphaned thumbnail(s) across ${preview.affected_folder_count} folder(s):\n` +
        `${sample}${more}\n\n` +
        `These are companion files left behind when Sonarr/Radarr swapped a release — ` +
        `Plex reads the orphaned NFO's <uniqueid> and creates a duplicate library entry, ` +
        `which is the “my show appears twice” symptom.\n\n` +
        `Only ${"`<stem>.nfo`"} and ${"`<stem>-thumb.{jpg,jpeg,png}`"} files whose stem ` +
        `does not pair with a live video file will be deleted. tvshow.nfo, season.nfo, ` +
        `every show/season-level artwork file, and every video / subtitle / audio file ` +
        `are preserved.\n\n` +
        `This cannot be undone.`,
      confirmLabel: "Sweep orphans",
      tone: "danger",
    });
    if (!ok) return;
    props.setBusy(
      `Sweeping orphans across ${preview.affected_folder_count} folder(s)…`,
    );
    try {
      const res = await api.libraries.sweepOrphans(props.library, {
        dry_run: false,
        rescan: true,
      });
      props.flash(
        `Removed ${res.nfo_removed} orphaned NFO(s) and ${res.thumb_removed} orphaned ` +
          `thumb(s) across ${res.affected_folder_count} folder(s)` +
          (res.failed && res.failed.length
            ? ` — ${res.failed.length} folder(s) failed`
            : ""),
      );
      props.invalidateItems();
    } catch (e: unknown) {
      props.flash(
        `Orphan sweep failed: ${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      props.setBusy(null);
    }
  };

  const runWipeSidecars = async () => {
    if (props.busy) return;
    props.setBusy("Scanning library for sidecar files…");
    let preview: { sidecar_count: number; files?: string[] };
    try {
      preview = await api.libraries.wipeSidecars(props.library, {
        dry_run: true,
      });
    } catch (e: unknown) {
      props.flash(
        `Sidecar preview failed: ${e instanceof Error ? e.message : String(e)}`,
      );
      props.setBusy(null);
      return;
    }
    props.setBusy(null);
    if (!preview.sidecar_count) {
      props.flash(
        `No .plex-nfo-builder.json sidecars found in "${props.library}".`,
      );
      return;
    }
    const ok = await confirmDlg({
      title: `Blast every sidecar in “${props.library}”?`,
      message:
        `Found ${preview.sidecar_count} .plex-nfo-builder.json sidecar file(s) to delete.\n\n` +
        `The sidecar is the on-disk record of bindings, overrides, and manual ` +
        `artwork picks for each folder. After wiping them, the database keeps ` +
        `bindings and overrides, but artwork picks reset to automatic selection. If you ` +
        `later wipe the database too, you'll have to re-bind from scratch.\n\n` +
        `NFOs and artwork are NOT touched.\n\n` +
        `This cannot be undone.`,
      confirmLabel: "Blast sidecars",
      tone: "danger",
    });
    if (!ok) return;
    props.setBusy(`Deleting ${preview.sidecar_count} sidecar file(s)…`);
    try {
      const res = await api.libraries.wipeSidecars(props.library, {
        dry_run: false,
      });
      props.flash(
        `Deleted ${res.deleted?.length ?? 0} sidecar file(s) and cleared ` +
          `${res.artwork_selections_cleared ?? 0} artwork pick(s)` +
          (res.failed && res.failed.length
            ? ` — ${res.failed.length} failed`
            : ""),
      );
      await queryClient.invalidateQueries({ queryKey: ["artwork-candidates"] });
    } catch (e: unknown) {
      props.flash(
        `Sidecar wipe failed: ${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      props.setBusy(null);
    }
  };

  return (
    <div className="maintenance">
      <button
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between gap-3 px-4 py-2.5 text-left"
      >
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-flex items-center justify-center w-6 h-6 rounded bg-rose-950 text-rose-300 text-xs font-black"
            title="Hazard"
          >
            ⚠
          </span>
          <span className="text-sm font-semibold text-slate-300">
            Library maintenance
          </span>
          <span className="text-xs text-slate-400/70">preview required</span>
        </div>
        <span className="text-xs text-slate-400/80">
          {open ? "hide" : "show"}
        </span>
      </button>
      {open && (
        <div className="px-4 pb-4 pt-1 border-t border-slate-800 space-y-3">
          <p className="text-xs text-slate-300/80 leading-relaxed">
            These actions touch every folder tracked under{" "}
            <span className="font-mono text-slate-200">{props.library}</span>.
            Each one shows you exactly what it will delete and asks for
            confirmation before touching disk. Don't press unless you're sure.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={runSweepOrphans}
              disabled={!!props.busy}
              className={props.btnHazard}
              title="Delete orphaned <stem>.nfo and <stem>-thumb.* sidecars left behind by Sonarr/Radarr release upgrades. Live videos, tvshow.nfo, season.nfo, and show/season artwork are preserved."
            >
              Preview orphan cleanup
            </button>
            <button
              onClick={runWipeNfo}
              disabled={!!props.busy}
              className={props.btnHazard}
              title="Delete every generated NFO and artwork file across this whole library. Bindings survive via the sidecar."
            >
              Preview NFO + artwork wipe
            </button>
            <button
              onClick={runWipeSidecars}
              disabled={!!props.busy}
              className={props.btnHazardOutline}
              title="Delete every .plex-nfo-builder.json sidecar in this library. Database is untouched."
            >
              Preview sidecar deletion
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
