import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Card, Field, PaneHeader } from "./SettingsControls";

export default function LibrariesSettings() {
  const [selected, setSelected] = useState("");
  const libraries = useQuery({
    queryKey: ["libraries"],
    queryFn: api.libraries.list,
  });
  const library = libraries.data?.libraries.find((item) => item.name === selected)
    ?? libraries.data?.libraries[0];

  return (
    <>
      <PaneHeader
        title="Libraries"
        subtitle="Preserve a library's sidecars before a clean-slate refresh."
      />
      {libraries.error ? (
        <div role="alert" className="text-sm text-rose-300">
          Libraries could not load: {libraries.error.message}
          <button type="button" className="btn ml-3" onClick={() => libraries.refetch()}>
            Retry
          </button>
        </div>
      ) : libraries.isPending ? (
        <p role="status" className="text-sm text-slate-400">Loading libraries…</p>
      ) : !library ? (
        <p className="text-sm text-slate-400">
          No libraries found. Detect libraries in the sidebar to get started.
        </p>
      ) : (
        <>
          <Field label="Library">
            <select
              className="bg-slate-800 px-2 py-1 rounded"
              value={library.name}
              onChange={(event) => setSelected(event.target.value)}
            >
              {libraries.data.libraries.map((item) => (
                <option key={item.name} value={item.name}>
                  {item.name}{item.enabled ? "" : " (disabled)"}
                </option>
              ))}
            </select>
          </Field>
          <LibrarySnapshots key={library.name} library={library.name} />
        </>
      )}
    </>
  );
}

function LibrarySnapshots({ library }: { library: string }) {
  const client = useQueryClient();
  const queryKey = ["library-snapshots", library];
  const snapshots = useQuery({
    queryKey,
    queryFn: () => api.libraries.snapshots.list(library),
    refetchInterval: (query) =>
      query.state.data?.jobs.some((job) => ["queued", "running"].includes(job.status))
        ? 2000
        : false,
  });
  const create = useMutation({
    mutationFn: () => api.libraries.snapshots.create(library),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey });
      void client.invalidateQueries({ queryKey: ["jobs"] });
    },
  });
  const active = snapshots.data?.jobs.some((job) => ["queued", "running"].includes(job.status));

  return (
    <div className="mt-5 space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Library Snapshot</h2>
        <p className="text-sm text-slate-400 mt-1">
          Save all non-media files in {library} as a ZIP, including NFOs,
          artwork, subtitles, metadata, and hidden files. Video and audio files
          are excluded. Folder paths are preserved.
        </p>
        <p className="text-sm text-slate-400 mt-3">
          Snapshots automatically pause the watcher and scheduled jobs across all
          libraries, then wait for active automation and queued builds to finish.
          Their previous states resume after all snapshots finish, including on failure.
        </p>
        <p className="text-sm text-amber-300 mt-3">
          Pause imports, manual builds, other file changes, and cleanup until the snapshot finishes.
          Wait for a completed ZIP before removing sidecars.
        </p>
        <button
          type="button"
          className="btn btn-primary mt-3"
          disabled={create.isPending || active || !snapshots.data || !!snapshots.error}
          onClick={() => create.mutate()}
        >
          {create.isPending ? "Starting snapshot…" : active ? "Snapshot in progress…" : "Create snapshot"}
        </button>
      </div>
      {create.error && (
        <p role="alert" className="text-sm text-rose-300">Snapshot could not start: {create.error.message}</p>
      )}
      {snapshots.error && (
        <div role="alert" className="text-sm text-rose-300">
          Snapshots could not load: {snapshots.error.message}
          <button type="button" className="btn ml-3" onClick={() => snapshots.refetch()}>
            Retry
          </button>
        </div>
      )}
      {snapshots.isPending && (
        <p role="status" className="text-sm text-slate-400">Loading snapshots…</p>
      )}
      {snapshots.data && (
        <>
          {snapshots.data.jobs.map((job) => (
            <div
              key={job.id}
              role={job.status === "error" ? "alert" : "status"}
              className={`rounded-md border border-slate-800 p-3 text-sm ${job.status === "error" ? "text-rose-300" : "text-slate-300"}`}
            >
              <p>Snapshot {job.status}{job.total > 0 ? ` · ${job.progress} / ${job.total} files` : ""}</p>
              {["queued", "running"].includes(job.status) && (
                <progress
                  aria-label="Snapshot progress"
                  className="w-full mt-2 accent-indigo-500"
                  max={job.total || 1}
                  value={job.total > 0 ? job.progress : undefined}
                />
              )}
              {job.messages.length > 0 && <p className="text-xs mt-1 break-words">{job.messages[job.messages.length - 1]}</p>}
            </div>
          ))}
          <div>
            <h2 className="text-sm font-semibold mb-2">Saved snapshots</h2>
            <p className="text-xs text-slate-400 mb-3 break-all">
              ZIPs are retained outside the library in <code>{snapshots.data.storage_path}</code>.
              Download a copy before deleting application data.
            </p>
            {snapshots.data.snapshots.length === 0 ? (
              <p className="text-sm text-slate-400">No snapshots for {library} yet.</p>
            ) : (
              <ul className="space-y-2">
                {snapshots.data.snapshots.map((snapshot) => (
                  <li key={snapshot.id}>
                    <Card>
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-sm text-slate-200 break-all">{snapshot.filename}</p>
                          <p className="text-xs text-slate-400 mt-1">
                            {new Date(snapshot.created_at).toLocaleString()} · {snapshot.file_count.toLocaleString()} files · {snapshot.size_bytes.toLocaleString()} bytes
                          </p>
                        </div>
                        <a
                          className="btn shrink-0"
                          href={api.libraries.snapshots.downloadUrl(library, snapshot.id)}
                          download={snapshot.filename}
                          aria-label={`Download ZIP ${snapshot.filename}`}
                        >
                          Download ZIP
                        </a>
                      </div>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
      <details className="text-sm text-slate-400">
        <summary className="cursor-pointer text-slate-300">Restore a snapshot</summary>
        <p className="mt-2">
          Pause all library writes, then extract the ZIP into the original library
          root and allow file replacement. Keep the original media filenames so
          sidecars still match. Extraction restores saved files; it does not
          remove files added after the snapshot. Rescan the library to recover
          sidecar state and update file status before resuming automation.
        </p>
        <p className="mt-2">
          Snapshots contain files only, not application settings or database
          records. File symlinks within the library are saved as regular files.
          Broken links, directory links, and links outside the library fail the snapshot;
          links to media files are excluded.
        </p>
      </details>
    </div>
  );
}
