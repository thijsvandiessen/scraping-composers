// URL state and display helpers for the /composers list page.

import type { ComposerSort } from "./api";

export interface ComposerListQuery {
  q: string;
  sort: ComposerSort;
  page: number;
}

/** Read the list query from the URL, falling back to defaults for bad input. */
export function parseComposerListQuery(url: URL): ComposerListQuery {
  const q = url.searchParams.get("q")?.trim() ?? "";
  const rawSort = url.searchParams.get("sort");
  const sort: ComposerSort = rawSort === "concerts" || rawSort === "recordings" ? rawSort : "label";
  const rawPage = Number(url.searchParams.get("page") ?? "1");
  const page = Number.isInteger(rawPage) && rawPage >= 1 ? rawPage : 1;
  return { q, sort, page };
}

/** Link to a page of the list, keeping the search and sort; defaults are omitted. */
export function composerListUrl({ q, sort, page }: ComposerListQuery): string {
  const params = new URLSearchParams();
  if (q) params.set("q", q);
  if (sort !== "label") params.set("sort", sort);
  if (page > 1) params.set("page", String(page));
  const query = params.toString();
  return query ? `/composers?${query}` : "/composers";
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

/** e.g. "19 concerts · 310 albums"; zero counts are left out. */
export function activityCounts(concerts: number, albums: number): string {
  const parts: string[] = [];
  if (concerts > 0) parts.push(plural(concerts, "concert"));
  if (albums > 0) parts.push(plural(albums, "album"));
  return parts.join(" · ");
}
