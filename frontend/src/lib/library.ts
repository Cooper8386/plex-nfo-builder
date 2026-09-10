import type { Item } from "./api";

export type LibFilter = "all" | "needs" | "complete";
const NEEDS_WORK_STATUSES = "none,partial,stale,foreign,mixed";

export function filterToParams(f: LibFilter): { status?: string } {
  if (f === "needs") return { status: NEEDS_WORK_STATUSES };
  if (f === "complete") return { status: "complete" };
  return {};
}

export function loadFilterFor(library: string | null): LibFilter {
  if (!library) return "all";
  try {
    const v = localStorage.getItem(`pnb.libFilter.${library}`);
    if (v === "needs" || v === "complete" || v === "all") return v;
  } catch {}
  return "all";
}

// v0.13.0 — library sort. Client-side over the (≤5000 row) items list; the
// choice persists per library, same pattern as the status filter above.
export type SortKey =
  | "title-asc"
  | "title-desc"
  | "added"
  | "updated"
  | "seasons";

export const SORT_OPTIONS: { key: SortKey; label: string }[] = [
  { key: "title-asc", label: "Title (A-Z)" },
  { key: "title-desc", label: "Title (Z-A)" },
  { key: "added", label: "Date Added" },
  { key: "updated", label: "Date Updated" },
  { key: "seasons", label: "Season Count (On disk)" },
];

const SORT_KEYS = new Set<string>(SORT_OPTIONS.map((o) => o.key));

export function loadSortFor(library: string | null): SortKey {
  if (!library) return "title-asc";
  try {
    const v = localStorage.getItem(`pnb.libSort.${library}`);
    if (v && SORT_KEYS.has(v)) return v as SortKey;
  } catch {}
  return "title-asc";
}

const titleOf = (i: Item) => (i.sort_title || i.title || "").toLowerCase();
const byTitle = (a: Item, b: Item) => titleOf(a).localeCompare(titleOf(b));

/** Descending on a numeric field; items without a value sink to the bottom
 *  regardless of direction, then tiebreak by title. */
function byNumberDesc(field: (i: Item) => number | null | undefined) {
  return (a: Item, b: Item) => {
    const av = field(a);
    const bv = field(b);
    const aMissing = av === null || av === undefined;
    const bMissing = bv === null || bv === undefined;
    if (aMissing && bMissing) return byTitle(a, b);
    if (aMissing) return 1;
    if (bMissing) return -1;
    if (bv! !== av!) return bv! - av!;
    return byTitle(a, b);
  };
}

export function sortItems(items: Item[], sort: SortKey): Item[] {
  const out = [...items];
  switch (sort) {
    case "title-asc":
      out.sort(byTitle);
      break;
    case "title-desc":
      out.sort((a, b) => byTitle(b, a));
      break;
    case "added":
      out.sort(byNumberDesc((i) => i.date_added));
      break;
    case "updated":
      out.sort(byNumberDesc((i) => i.date_updated));
      break;
    case "seasons":
      out.sort(byNumberDesc((i) => i.season_count_local));
      break;
  }
  return out;
}
