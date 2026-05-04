import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

const STATUS: Record<string, string> = {
  running: "bg-amber-700",
  completed: "bg-emerald-700",
  failed: "bg-rose-700",
};

export default function JobsView() {
  const { data } = useQuery({
    queryKey: ["jobs"],
    queryFn: () => api.jobs.list(),
    refetchInterval: 1500,
  });
  const jobs = data?.jobs ?? [];
  return (
    <div className="p-4">
      <h2 className="text-lg font-semibold mb-3">Build jobs</h2>
      <div className="border border-slate-800 rounded overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-900 text-slate-400 text-left">
            <tr>
              <th className="p-2">ID</th>
              <th>Kind</th>
              <th>Folder</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Started</th>
              <th>Messages</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <tr key={j.id} className="border-t border-slate-800">
                <td className="p-2 font-mono text-xs">{j.id}</td>
                <td>{j.kind}</td>
                <td className="text-xs truncate max-w-md">{j.folder}</td>
                <td>
                  <span
                    className={`text-[10px] px-2 py-0.5 rounded ${
                      STATUS[j.status] ?? "bg-slate-700"
                    }`}
                  >
                    {j.status}
                  </span>
                </td>
                <td className="text-xs">
                  {j.progress}/{j.total}
                </td>
                <td className="text-xs">
                  {j.started_at ? new Date(j.started_at * 1000).toLocaleTimeString() : ""}
                </td>
                <td className="text-xs text-slate-400">
                  {(j.messages || []).join(" • ")}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
