import { errorMessage } from "../lib/errors";
import { useEffect, useMemo, useState } from "react";
import { api, Library, Schedule, ScheduleAction, WatcherStatus } from "../lib/api";
import { useConfirm } from "../components/confirm";
import {
  Card,
  CardLabel,
  PaneHeader,
  SubHeader,
  Divider,
  Field,
} from "./SettingsControls";

export function WatcherPane() {
  const [status, setStatus] = useState<WatcherStatus | null>(null);
  const [debounce, setDebounce] = useState<number>(30);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

  const reload = async () => {
    try {
      const [st, settings] = await Promise.all([
        api.watcher.status(),
        api.settings.get(),
      ]);
      setStatus(st);
      const d =
        typeof settings.watcher_debounce_seconds === "number"
          ? settings.watcher_debounce_seconds
          : st.debounce_seconds;
      setDebounce(d);
    } catch (e: unknown) {
      setErr(errorMessage(e));
    }
  };

  useEffect(() => {
    reload();
    const t = setInterval(() => {
      api.watcher
        .status()
        .then(setStatus)
        .catch(() => {});
    }, 5000);
    return () => clearInterval(t);
  }, []);

  const onToggle = async (enabled: boolean) => {
    setBusy(true);
    setErr(null);
    try {
      const r = await api.watcher.toggle(enabled);
      setStatus(r.status);
      setSavedMsg(enabled ? "Watcher enabled." : "Watcher disabled.");
      setTimeout(() => setSavedMsg(null), 1800);
    } catch (e: unknown) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const onSaveDebounce = async () => {
    setBusy(true);
    setErr(null);
    try {
      const n = Math.max(1, Math.min(3600, Math.floor(debounce)));
      await api.settings.set({ watcher_debounce_seconds: n });
      await reload();
      setSavedMsg(`Debounce set to ${n}s.`);
      setTimeout(() => setSavedMsg(null), 1800);
    } catch (e: unknown) {
      setErr(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PaneHeader
        title="Watcher"
        subtitle="Watch every enabled library for new folders and media files, then automatically scan, match, and build NFOs. Builds that can't auto-match are queued for manual review on the Watcher page (top nav)."
      />

      {!status ? (
        <div className="text-xs text-slate-500">Loading watcher status…</div>
      ) : (
        <>
          {!status.available && (
            <div className="mb-4 rounded-md border border-amber-800 bg-amber-900/20 px-3 py-2 text-xs text-amber-200">
              The <code className="text-amber-100">watchdog</code> package is
              not available in this container. The watcher cannot run; rebuild
              the image with the latest <code>requirements.txt</code> to fix.
            </div>
          )}
          <Field label="Enable filesystem watcher">
            <div className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={!!status.enabled}
                disabled={busy || !status.available}
                onChange={(e) => onToggle(e.target.checked)}
                className="mt-1"
              />
              <span className="text-[11px] text-slate-500 max-w-xl leading-relaxed">
                When on, new folders and media files under every enabled library
                trigger the same scan → match → build pipeline that Schedules
                runs, after the debounce window expires.
              </span>
            </div>
          </Field>

          <Field label="Debounce window (seconds)">
            <div className="flex items-center gap-2">
              <input
                type="number"
                min={1}
                max={3600}
                className="bg-slate-800 px-2 py-1 rounded w-24"
                value={debounce}
                onChange={(e) =>
                  setDebounce(parseInt(e.target.value || "30", 10))
                }
              />
              <button
                type="button"
                className="text-xs px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded"
                onClick={onSaveDebounce}
                disabled={busy}
              >
                Save
              </button>
              <span className="text-[11px] text-slate-500 max-w-md">
                How long the folder has to be quiet before the pipeline fires.
                30s suits most Sonarr/Radarr setups; raise it if you regularly
                copy huge files manually.
              </span>
            </div>
          </Field>

          <Divider />

          <SubHeader>Runtime</SubHeader>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-2xl">
            <Card>
              <CardLabel>Status</CardLabel>
              <div className="text-sm text-slate-100">
                {status.paused_for_snapshot ? (
                  <span className="text-amber-300">Paused for library snapshot</span>
                ) : status.running ? (
                  <span className="text-emerald-300">Running</span>
                ) : status.enabled ? (
                  <span className="text-amber-300">Enabled, not running</span>
                ) : (
                  <span className="text-slate-400">Disabled</span>
                )}
              </div>
            </Card>
            <Card>
              <CardLabel>Active debounce</CardLabel>
              <div className="text-sm font-mono text-slate-100">
                {status.debounce_seconds}s
              </div>
            </Card>
            <Card>
              <CardLabel>Pending folders</CardLabel>
              <div className="text-sm font-mono text-slate-100">
                {status.pending_count}
              </div>
            </Card>
            <Card>
              <CardLabel>In-flight pipelines</CardLabel>
              <div className="text-sm font-mono text-slate-100">
                {status.in_flight_count}
              </div>
            </Card>
          </div>

          <div className="mt-4">
            <SubHeader>Watched paths</SubHeader>
            {status.watched_paths.length === 0 ? (
              <div className="text-xs text-slate-500">
                No paths are currently being watched. Enable a library in the
                sidebar to add it.
              </div>
            ) : (
              <ul className="text-xs font-mono text-slate-300 space-y-0.5">
                {status.watched_paths.map((p) => (
                  <li key={p} className="truncate">
                    {p}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}

      {err && <div className="mt-4 text-xs text-rose-400">{err}</div>}
      {savedMsg && (
        <div className="mt-2 text-xs text-emerald-400">{savedMsg}</div>
      )}
    </>
  );
}

const ACTION_LABELS: Record<ScheduleAction, string> = {
  scan_only: "Scan only",
  match_only: "Match only",
  build_only: "Build only",
  match_and_build: "Match + Build",
  full: "Full (scan + match + build)",
};

const CRON_PRESETS: { label: string; cron: string }[] = [
  { label: "Daily 3am UTC", cron: "0 3 * * *" },
  { label: "Sunday 3am UTC", cron: "0 3 * * 0" },
  { label: "Every 6 hours", cron: "0 */6 * * *" },
  { label: "Hourly", cron: "0 * * * *" },
];

function fmtTimestamp(ts: number | null): string {
  if (!ts) return "never";
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

export function SchedulesSection() {
  const confirmDlg = useConfirm();
  const [items, setItems] = useState<Schedule[] | null>(null);
  const [pausedForSnapshot, setPausedForSnapshot] = useState(false);
  const [libs, setLibs] = useState<Library[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<{
    library: string;
    cron: string;
    action: ScheduleAction;
    enabled: boolean;
  }>({
    library: "",
    cron: "0 3 * * *",
    action: "match_and_build",
    enabled: true,
  });

  const reload = async () => {
    try {
      const [s, l] = await Promise.all([
        api.schedules.list(),
        api.libraries.list(),
      ]);
      setItems(s.schedules);
      setPausedForSnapshot(!!s.paused_for_snapshot);
      setLibs(l.libraries);
    } catch (e: unknown) {
      setError(errorMessage(e));
    }
  };

  useEffect(() => {
    reload();
    const timer = setInterval(() => {
      api.schedules.list().then((result) => {
        setItems(result.schedules);
        setPausedForSnapshot(!!result.paused_for_snapshot);
      }).catch(() => {});
    }, 5000);
    return () => clearInterval(timer);
  }, []);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.schedules.create({
        library: draft.library || null,
        cron: draft.cron.trim(),
        action: draft.action,
        enabled: draft.enabled,
      });
      setDraft({ ...draft, library: "", cron: "0 3 * * *" });
      await reload();
    } catch (e: unknown) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const update = async (
    id: number,
    body: Parameters<typeof api.schedules.update>[1],
  ) => {
    setBusy(true);
    setError(null);
    try {
      await api.schedules.update(id, body);
      await reload();
    } catch (e: unknown) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: number) => {
    const ok = await confirmDlg({
      title: "Delete this schedule?",
      message:
        "The recurring run is removed immediately. You can recreate it later from this same panel.",
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (!ok) return;
    setBusy(true);
    try {
      await api.schedules.remove(id);
      await reload();
    } catch (e: unknown) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const runNow = async (id: number) => {
    setBusy(true);
    try {
      await api.schedules.run(id);
      setTimeout(reload, 1500);
    } catch (e: unknown) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PaneHeader
        title="Schedules"
        subtitle="Periodically scan, auto-match, and build NFOs for new or changed items. Cron expressions are evaluated in UTC. A schedule with no library applies to every enabled library."
      />
      {pausedForSnapshot && (
        <p role="status" className="mb-4 text-sm text-amber-300">
          Paused for library snapshot. Schedules resume automatically afterward;
          enabled settings are unchanged.
        </p>
      )}

      <div className="bg-slate-900/60 border border-slate-800 rounded-md p-3 mb-4">
        <div className="text-xs uppercase tracking-wide text-slate-500 mb-2">
          New schedule
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Library
            <select
              className="bg-slate-800 px-2 py-1 rounded text-sm text-slate-100"
              value={draft.library}
              onChange={(e) => setDraft({ ...draft, library: e.target.value })}
            >
              <option value="">All libraries</option>
              {libs.map((l) => (
                <option key={l.name} value={l.name}>
                  {l.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400">
            Action
            <select
              className="bg-slate-800 px-2 py-1 rounded text-sm text-slate-100"
              value={draft.action}
              onChange={(e) =>
                setDraft({ ...draft, action: e.target.value as ScheduleAction })
              }
            >
              {Object.entries(ACTION_LABELS).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-xs text-slate-400 sm:col-span-2">
            Cron (UTC, 5 fields)
            <input
              className="bg-slate-800 px-2 py-1 rounded text-sm font-mono text-slate-100"
              value={draft.cron}
              onChange={(e) => setDraft({ ...draft, cron: e.target.value })}
              placeholder="0 3 * * *"
            />
            <div className="flex flex-wrap gap-1.5 mt-1">
              {CRON_PRESETS.map((p) => (
                <button
                  key={p.cron}
                  type="button"
                  onClick={() => setDraft({ ...draft, cron: p.cron })}
                  className="text-[11px] px-2 py-0.5 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded"
                >
                  {p.label}
                </button>
              ))}
            </div>
          </label>
        </div>
        <div className="flex items-center gap-3 mt-3">
          <label className="text-xs text-slate-300 inline-flex items-center gap-2">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) =>
                setDraft({ ...draft, enabled: e.target.checked })
              }
              className="accent-indigo-500"
            />
            Enabled
          </label>
          <button
            type="button"
            onClick={create}
            disabled={busy || !draft.cron.trim()}
            className="px-3 py-1 bg-indigo-600 hover:bg-indigo-500 rounded text-xs disabled:opacity-50"
          >
            Add schedule
          </button>
          {error && <span className="text-xs text-rose-400">{error}</span>}
        </div>
      </div>

      {items === null ? (
        <div className="text-xs text-slate-500">Loading schedules…</div>
      ) : items.length === 0 ? (
        <div className="text-xs text-slate-500">No schedules configured.</div>
      ) : (
        <div className="space-y-2">
          {items.map((sch) => (
            <ScheduleRow
              key={sch.id}
              libs={libs}
              sch={sch}
              busy={busy}
              pausedForSnapshot={pausedForSnapshot}
              onUpdate={(body) => update(sch.id, body)}
              onRemove={() => remove(sch.id)}
              onRun={() => runNow(sch.id)}
            />
          ))}
        </div>
      )}
    </>
  );
}

function ScheduleRow({
  libs,
  sch,
  busy,
  pausedForSnapshot,
  onUpdate,
  onRemove,
  onRun,
}: {
  libs: Library[];
  sch: Schedule;
  busy: boolean;
  pausedForSnapshot: boolean;
  onUpdate: (body: {
    library?: string | null;
    cron?: string;
    action?: ScheduleAction;
    enabled?: boolean;
  }) => void;
  onRemove: () => void;
  onRun: () => void;
}) {
  const [cron, setCron] = useState(sch.cron);
  const dirty = cron !== sch.cron;

  const statusBadge = useMemo(() => {
    const status = sch.last_status;
    if (status === "running") {
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-700 text-amber-100 uppercase tracking-wide">
          running
        </span>
      );
    }
    if (status === "ok") {
      return (
        <span className="text-[10px] px-1.5 py-0.5 rounded bg-emerald-700 text-emerald-100 uppercase tracking-wide">
          ok
        </span>
      );
    }
    if (status === "error") {
      return (
        <span
          className="text-[10px] px-1.5 py-0.5 rounded bg-rose-700 text-rose-100 uppercase tracking-wide"
          title={sch.last_message ?? undefined}
        >
          error
        </span>
      );
    }
    return null;
  }, [sch.last_status, sch.last_message]);

  return (
    <div className="bg-slate-900/40 border border-slate-800 rounded-md p-3">
      <div className="flex flex-wrap items-center gap-3 mb-2">
        <span className="text-xs uppercase text-slate-500">#{sch.id}</span>
        <select
          className="bg-slate-800 px-2 py-0.5 rounded text-xs text-slate-100"
          value={sch.library ?? ""}
          onChange={(e) => onUpdate({ library: e.target.value || null })}
          disabled={busy}
        >
          <option value="">All libraries</option>
          {libs.map((l) => (
            <option key={l.name} value={l.name}>
              {l.name}
            </option>
          ))}
        </select>
        <select
          className="bg-slate-800 px-2 py-0.5 rounded text-xs text-slate-100"
          value={sch.action}
          onChange={(e) =>
            onUpdate({ action: e.target.value as ScheduleAction })
          }
          disabled={busy}
        >
          {Object.entries(ACTION_LABELS).map(([v, label]) => (
            <option key={v} value={v}>
              {label}
            </option>
          ))}
        </select>
        <label className="text-xs text-slate-300 inline-flex items-center gap-1.5">
          <input
            type="checkbox"
            checked={!!sch.enabled}
            onChange={(e) => onUpdate({ enabled: e.target.checked })}
            disabled={busy}
            className="accent-indigo-500"
          />
          Enabled
        </label>
        {statusBadge}
        <span className="text-[11px] text-slate-500">
          last run: {fmtTimestamp(sch.last_run)}
        </span>
        <div className="flex-1" />
        <button
          type="button"
          onClick={onRun}
          disabled={busy || pausedForSnapshot}
          className="text-xs px-2 py-0.5 bg-indigo-700 hover:bg-indigo-600 rounded disabled:opacity-50"
        >
          Run now
        </button>
        <button
          type="button"
          onClick={onRemove}
          disabled={busy}
          className="text-xs px-2 py-0.5 bg-rose-900/40 hover:bg-rose-900/70 border border-rose-800 text-rose-200 rounded disabled:opacity-50"
        >
          Delete
        </button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <input
          className="bg-slate-800 px-2 py-1 rounded text-xs font-mono text-slate-100 w-44"
          value={cron}
          onChange={(e) => setCron(e.target.value)}
          disabled={busy}
        />
        <button
          type="button"
          onClick={() => onUpdate({ cron: cron.trim() })}
          disabled={busy || !dirty}
          className="text-xs px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded disabled:opacity-30"
        >
          Save cron
        </button>
        {sch.last_message && sch.last_status === "error" && (
          <span
            className="text-[11px] text-rose-300 truncate"
            title={sch.last_message}
          >
            {sch.last_message}
          </span>
        )}
      </div>
    </div>
  );
}
