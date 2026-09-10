import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";

export default function JobsView() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState("all");
  const { data, error, isPending, refetch } = useQuery({
    queryKey: ["jobs"],
    queryFn: api.jobs.list,
    refetchInterval: (query) =>
      query.state.data?.jobs.some((job) =>
        ["running", "queued"].includes(job.status),
      )
        ? 1500
        : 5000,
  });
  const previousActive = useRef(false);
  const active =
    data?.jobs.some((job) => ["running", "queued"].includes(job.status)) ??
    false;
  useEffect(() => {
    if (previousActive.current && !active) {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["detail"] });
    }
    previousActive.current = active;
  }, [active, qc]);
  const jobs =
    data?.jobs.filter(
      (job) =>
        filter === "all" ||
        (filter === "active"
          ? ["running", "queued"].includes(job.status)
          : ["error", "failed"].includes(job.status)),
    ) ?? [];
  return (
    <div className="page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Background work</p>
          <h1 className="page-title">Activity</h1>
          <p className="text-sm text-slate-500 mt-2">
            Build progress, results, and diagnostics for each title.
          </p>
        </div>
        <span className="badge">
          {active ? "Builds in progress" : "No active builds"}
        </span>
      </div>
      <div className="flex gap-2 mb-5">
        {["all", "active", "failed"].map((value) => (
          <button
            key={value}
            className={`btn capitalize ${filter === value ? "border-indigo-500 text-indigo-300" : ""}`}
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {value}
          </button>
        ))}
      </div>
      {error ? (
        <div role="alert" className="notice error-notice">
          {error.message}
          <button className="btn ml-3" onClick={() => refetch()}>
            Retry
          </button>
        </div>
      ) : isPending ? (
        <p role="status">Loading activity…</p>
      ) : !jobs.length ? (
        <div className="empty-state">
          <h2 className="text-slate-200 mb-2">
            {filter === "all" ? "No builds yet" : `No ${filter} builds`}
          </h2>
          <p className="text-sm">
            Build a title or library to follow its progress here.
          </p>
        </div>
      ) : (
        <div className="panel overflow-x-auto">
          <table className="w-full min-w-[650px] text-sm text-left">
            <thead className="text-xs text-slate-500">
              <tr>
                <th>Media item</th>
                <th>Status</th>
                <th>Progress</th>
                <th>Started</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr key={job.id} className="border-t border-slate-800">
                  <td>
                    <span className="block font-medium">
                      {job.folder.split(/[\\/]/).pop()}
                    </span>
                    <span className="block text-[10px] text-slate-500 font-mono mt-1">
                      {job.id} · {job.kind}
                    </span>
                  </td>
                  <td>
                    <span
                      className={`badge ${["error", "failed"].includes(job.status) ? "text-rose-300 border-rose-800" : ""}`}
                    >
                      {job.status}
                    </span>
                  </td>
                  <td>
                    <span className="text-xs text-slate-400">
                      {job.progress} / {job.total}
                    </span>
                    {job.total > 0 && (
                      <progress
                        aria-label={`Progress for ${job.folder}`}
                        max={job.total}
                        value={job.progress}
                        className="block mt-1 w-24 h-1 accent-amber-400"
                      />
                    )}
                  </td>
                  <td className="text-xs text-slate-400">
                    {job.started_at
                      ? new Date(job.started_at * 1000).toLocaleString()
                      : "Queued"}
                  </td>
                  <td className="max-w-md text-xs text-slate-400">
                    <details>
                      <summary>View messages ({job.messages.length})</summary>
                      <p className="break-all font-mono text-[10px] my-2">
                        {job.folder}
                      </p>
                      <ul className="space-y-1">
                        {job.messages.map((message, index) => (
                          <li key={index}>{message}</li>
                        ))}
                      </ul>
                    </details>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
