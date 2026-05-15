import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  api,
  WatcherEvent,
  WatcherReviewItem,
  WatcherStatus,
} from "../lib/api";
import { useConfirm } from "../components/ConfirmDialog";

type Tab = "activity" | "review";

export default function WatcherView(props: {
  onOpenDetail?: (library: string, folderPath: string) => void;
}) {
  const [tab, setTab] = useState<Tab>(() => {
    try {
      const t = localStorage.getItem("pnb.watcher.tab") as Tab | null;
      if (t === "review" || t === "activity") return t;
    } catch {
      /* ignore */
    }
    return "activity";
  });

  useEffect(() => {
    try {
      localStorage.setItem("pnb.watcher.tab", tab);
    } catch {
      /* ignore */
    }
  }, [tab]);

  const statusQ = useQuery({
    queryKey: ["watcher", "status"],
    queryFn: () => api.watcher.status(),
    refetchInterval: 5000,
  });
  const reviewQ = useQuery({
    queryKey: ["watcher", "review"],
    queryFn: () => api.watcher.review.list(),
    refetchInterval: 8000,
  });

  return (
    <div className="p-4 flex flex-col gap-4 h-full min-h-0">
      <Header
        status={statusQ.data ?? null}
        reviewCount={reviewQ.data?.items?.length ?? 0}
      />
      <div className="flex gap-1 border-b border-slate-800">
        <TabButton
          active={tab === "activity"}
          onClick={() => setTab("activity")}
          label="Activity"
        />
        <TabButton
          active={tab === "review"}
          onClick={() => setTab("review")}
          label={`Review queue${
            (reviewQ.data?.items?.length ?? 0) > 0
              ? ` (${reviewQ.data!.items.length})`
              : ""
          }`}
        />
      </div>
      <div className="flex-1 min-h-0 overflow-auto">
        {tab === "activity" ? (
          <ActivityTab />
        ) : (
          <ReviewTab onOpenDetail={props.onOpenDetail} />
        )}
      </div>
    </div>
  );
}

function Header({
  status,
  reviewCount,
}: {
  status: WatcherStatus | null;
  reviewCount: number;
}) {
  if (!status) {
    return (
      <div className="text-xs text-slate-500">Loading watcher status…</div>
    );
  }
  const dot = status.running
    ? "bg-emerald-500"
    : status.enabled
      ? "bg-amber-500"
      : "bg-slate-600";
  return (
    <div className="flex flex-wrap items-center gap-3">
      <h2 className="text-lg font-semibold flex items-center gap-2">
        <span className={`inline-block w-2.5 h-2.5 rounded-full ${dot}`} />
        Watcher
      </h2>
      <Pill>
        {status.running
          ? "Running"
          : status.enabled
            ? "Enabled, not running"
            : "Disabled"}
      </Pill>
      <Pill>{status.debounce_seconds}s debounce</Pill>
      <Pill>{status.watched_paths.length} path(s)</Pill>
      <Pill>{status.pending_count} pending</Pill>
      <Pill>{status.in_flight_count} in-flight</Pill>
      {reviewCount > 0 && (
        <Pill tone="warning">{reviewCount} awaiting review</Pill>
      )}
    </div>
  );
}

function Pill({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "warning";
}) {
  const cls =
    tone === "warning"
      ? "bg-amber-900/30 border border-amber-800 text-amber-200"
      : "bg-slate-900 border border-slate-800 text-slate-300";
  return (
    <span
      className={`text-[11px] font-mono uppercase tracking-wider rounded px-1.5 py-0.5 ${cls}`}
    >
      {children}
    </span>
  );
}

function TabButton({
  active,
  onClick,
  label,
}: {
  active: boolean;
  onClick: () => void;
  label: string;
}) {
  return (
    <button
      onClick={onClick}
      className={`px-3 py-1.5 text-sm rounded-t-md border-b-2 transition ${
        active
          ? "border-indigo-500 text-white"
          : "border-transparent text-slate-400 hover:text-slate-200"
      }`}
    >
      {label}
    </button>
  );
}

/* ----------------------- Activity tab ----------------------- */

function ActivityTab() {
  const eventsQ = useQuery({
    queryKey: ["watcher", "events"],
    queryFn: () => api.watcher.events(300),
    refetchInterval: 3000,
  });
  const events = eventsQ.data?.events ?? [];

  if (eventsQ.isLoading) {
    return <div className="text-xs text-slate-500">Loading activity…</div>;
  }
  if (events.length === 0) {
    return (
      <div className="text-xs text-slate-500">
        No watcher activity yet. New folders or files appearing under a watched
        library will show up here.
      </div>
    );
  }

  return (
    <ul className="divide-y divide-slate-800 border border-slate-800 rounded-md bg-slate-950/40">
      {events.map((e, i) => (
        <EventRow key={`${e.timestamp}-${i}`} event={e} />
      ))}
    </ul>
  );
}

function EventRow({ event: e }: { event: WatcherEvent }) {
  const tone = statusTone(e.status);
  return (
    <li className="px-3 py-2 flex items-start gap-3 text-sm">
      <span
        className={`mt-1 inline-block w-2 h-2 rounded-full shrink-0 ${tone.dot}`}
        title={e.status}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-2">
          <span
            className={`text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded ${tone.badge}`}
          >
            {e.event_type.replace(/_/g, " ")}
          </span>
          {e.library && (
            <span className="text-[11px] text-slate-400">{e.library}</span>
          )}
          <span className="text-[11px] text-slate-500 ml-auto shrink-0">
            {fmtTime(e.timestamp)}
          </span>
        </div>
        {e.folder_path && (
          <div className="font-mono text-xs text-slate-300 truncate mt-0.5">
            {e.folder_path}
          </div>
        )}
        <div className="text-xs text-slate-400 mt-0.5">{e.message}</div>
      </div>
    </li>
  );
}

function statusTone(status: WatcherEvent["status"]): {
  dot: string;
  badge: string;
} {
  switch (status) {
    case "success":
      return {
        dot: "bg-emerald-500",
        badge: "bg-emerald-900/40 text-emerald-200 border border-emerald-800",
      };
    case "warning":
      return {
        dot: "bg-amber-500",
        badge: "bg-amber-900/40 text-amber-200 border border-amber-800",
      };
    case "error":
      return {
        dot: "bg-rose-500",
        badge: "bg-rose-900/40 text-rose-200 border border-rose-800",
      };
    default:
      return {
        dot: "bg-slate-500",
        badge: "bg-slate-900 text-slate-300 border border-slate-700",
      };
  }
}

function fmtTime(ts: number): string {
  try {
    return new Date(ts * 1000).toLocaleString();
  } catch {
    return String(ts);
  }
}

/* ----------------------- Review tab ----------------------- */

function ReviewTab(props: {
  onOpenDetail?: (library: string, folderPath: string) => void;
}) {
  const qc = useQueryClient();
  const confirmDlg = useConfirm();
  const reviewQ = useQuery({
    queryKey: ["watcher", "review"],
    queryFn: () => api.watcher.review.list(),
    refetchInterval: 5000,
  });
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const items = reviewQ.data?.items ?? [];

  const grouped = useMemo(() => {
    const out: Record<string, WatcherReviewItem[]> = {};
    for (const it of items) {
      const k = it.library || "(unknown)";
      out[k] = out[k] || [];
      out[k].push(it);
    }
    return out;
  }, [items]);

  const retry = async (it: WatcherReviewItem) => {
    setBusy(it.folder_path);
    setErr(null);
    try {
      await api.watcher.review.retry(it.folder_path);
      // Don't remove the row optimistically — the watcher will clear it on
      // success or update `attempts` on another failure.
      qc.invalidateQueries({ queryKey: ["watcher", "review"] });
      qc.invalidateQueries({ queryKey: ["watcher", "events"] });
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const dismiss = async (it: WatcherReviewItem) => {
    setBusy(it.folder_path);
    setErr(null);
    try {
      await api.watcher.review.resolve(it.folder_path);
      qc.invalidateQueries({ queryKey: ["watcher", "review"] });
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  const clearAll = async () => {
    const ok = await confirmDlg({
      title: "Clear the entire review queue?",
      message:
        "Every queued folder will be dismissed. They will re-appear if the watcher detects them again and still can't auto-match.",
      confirmLabel: "Clear all",
      tone: "danger",
    });
    if (!ok) return;
    setBusy("__all__");
    setErr(null);
    try {
      await api.watcher.review.clear();
      qc.invalidateQueries({ queryKey: ["watcher", "review"] });
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setBusy(null);
    }
  };

  if (reviewQ.isLoading) {
    return <div className="text-xs text-slate-500">Loading review queue…</div>;
  }

  if (items.length === 0) {
    return (
      <div className="text-xs text-slate-500">
        Nothing to review. Folders the watcher couldn't auto-match will appear
        here so you can resolve them.
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="text-xs text-slate-400">
          {items.length} folder{items.length === 1 ? "" : "s"} need manual
          attention.
        </div>
        <button
          onClick={clearAll}
          disabled={busy === "__all__"}
          className="text-xs px-2 py-1 bg-rose-900/30 hover:bg-rose-900/60 border border-rose-800 text-rose-200 rounded disabled:opacity-50"
        >
          Clear all
        </button>
      </div>
      {err && <div className="text-xs text-rose-400">{err}</div>}
      {Object.entries(grouped).map(([lib, rows]) => (
        <div key={lib}>
          <h3 className="text-[11px] font-semibold uppercase tracking-wider text-slate-500 mb-2">
            {lib}
          </h3>
          <ul className="divide-y divide-slate-800 border border-slate-800 rounded-md bg-slate-950/40">
            {rows.map((it) => (
              <ReviewRow
                key={it.folder_path}
                item={it}
                busy={busy === it.folder_path}
                onRetry={() => retry(it)}
                onDismiss={() => dismiss(it)}
                onOpenDetail={
                  props.onOpenDetail && it.library
                    ? () => props.onOpenDetail!(it.library!, it.folder_path)
                    : undefined
                }
              />
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}

function ReviewRow({
  item,
  busy,
  onRetry,
  onDismiss,
  onOpenDetail,
}: {
  item: WatcherReviewItem;
  busy: boolean;
  onRetry: () => void;
  onDismiss: () => void;
  onOpenDetail?: () => void;
}) {
  const reasonTone =
    item.reason === "error"
      ? "bg-rose-900/40 text-rose-200 border border-rose-800"
      : item.reason === "no_match"
        ? "bg-amber-900/40 text-amber-200 border border-amber-800"
        : "bg-slate-900 text-slate-300 border border-slate-700";

  const folderName = item.folder_path.split("/").filter(Boolean).pop() || item.folder_path;

  return (
    <li className="px-3 py-2 flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-2">
        <span
          className={`text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded ${reasonTone}`}
        >
          {item.reason.replace(/_/g, " ")}
        </span>
        {item.kind && (
          <span className="text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded bg-slate-900 border border-slate-700 text-slate-400">
            {item.kind}
          </span>
        )}
        <span className="text-sm font-medium text-slate-100 truncate">
          {folderName}
        </span>
        <span className="text-[11px] text-slate-500 ml-auto shrink-0">
          {item.attempts} attempt{item.attempts === 1 ? "" : "s"} · last{" "}
          {fmtTime(item.last_attempt_at ?? item.detected_at)}
        </span>
      </div>
      <div className="font-mono text-xs text-slate-400 truncate">
        {item.folder_path}
      </div>
      {item.detail && (
        <div className="text-xs text-slate-400">{item.detail}</div>
      )}
      <div className="flex items-center gap-2 mt-1">
        <button
          onClick={onRetry}
          disabled={busy}
          className="text-xs px-2 py-1 bg-indigo-700 hover:bg-indigo-600 rounded disabled:opacity-50"
        >
          Retry
        </button>
        {onOpenDetail && (
          <button
            onClick={onOpenDetail}
            disabled={busy}
            className="text-xs px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded disabled:opacity-50"
          >
            Open detail
          </button>
        )}
        <button
          onClick={onDismiss}
          disabled={busy}
          className="text-xs px-2 py-1 bg-slate-800 hover:bg-slate-700 border border-slate-700 rounded text-slate-300 disabled:opacity-50"
        >
          Dismiss
        </button>
      </div>
    </li>
  );
}
