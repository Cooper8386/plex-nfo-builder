import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
export default function LogsView() {
  const [paused, setPaused] = useState(false);
  const [search, setSearch] = useState("");
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["logs"],
    queryFn: api.logs,
    refetchInterval: paused ? false : 3000,
  });
  const lines = (data?.lines ?? []).filter((line) =>
    line.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <div className="page flex flex-col h-full">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Diagnostics</p>
          <h1 className="page-title">Application log</h1>
          <p className="text-sm text-slate-500 mt-2">
            Latest 400 lines. Provider credentials and access tokens are
            redacted.
          </p>
        </div>
        <button
          className="btn"
          onClick={() => setPaused((value) => !value)}
          aria-pressed={paused}
        >
          {paused ? "Resume updates" : "Pause updates"}
        </button>
      </div>
      <label className="mb-4">
        <span className="sr-only">Filter log lines</span>
        <input
          type="search"
          className="field w-full max-w-sm"
          placeholder="Filter log lines…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </label>
      {error ? (
        <div role="alert" className="notice error-notice">
          {error.message}
          <button className="btn ml-3" onClick={() => refetch()}>
            Retry
          </button>
        </div>
      ) : (
        <pre
          className="panel flex-1 min-h-40 overflow-auto p-4 text-[11px] leading-6 text-slate-400"
          tabIndex={0}
          aria-label="Application log"
        >
          {isPending
            ? "Loading log…"
            : lines.length
              ? lines.join("\n")
              : "No matching log entries."}
        </pre>
      )}
    </div>
  );
}
