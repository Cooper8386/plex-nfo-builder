import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";

export default function LogsView() {
  const { data } = useQuery({
    queryKey: ["logs"],
    queryFn: () => api.logs(),
    refetchInterval: 3000,
  });
  return (
    <div className="p-4">
      <h2 className="text-lg font-semibold mb-2">App log (tail 400)</h2>
      <pre className="text-xs bg-slate-950 border border-slate-800 rounded p-3 h-[80vh] overflow-auto">
        {(data?.lines ?? []).join("\n")}
      </pre>
    </div>
  );
}
