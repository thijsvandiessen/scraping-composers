import type { ZodType } from "zod";

import {
  ComposerDetailSchema,
  ComposerPageSchema,
  ComposerWorksPageSchema,
  type ComposerDetail,
  type ComposerPage,
  type ComposerWorksPage,
} from "./schemas";

export const PAGE_SIZE = 25;

export type ComposerSort = "label" | "concerts";
export type WorkSort = "label" | "mentions";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

// Read per request, not at module load: the standalone node server should pick
// up the environment it was started with, and tests can override it.
function apiBaseUrl(): string {
  return (process.env.GOLD_API_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "");
}

async function apiFetch<T>(path: string, schema: ZodType<T>): Promise<T> {
  const url = `${apiBaseUrl()}${path}`;
  let response: Response;
  try {
    response = await fetch(url);
  } catch (cause) {
    throw new ApiError(
      `Could not reach the composer API at ${apiBaseUrl()}. Is the gold API running? (${String(cause)})`,
    );
  }
  if (!response.ok) {
    let detail = "";
    try {
      const body: unknown = await response.json();
      if (body && typeof body === "object" && "detail" in body) {
        detail = ` — ${JSON.stringify(body.detail)}`;
      }
    } catch {
      // non-JSON error body; the status alone will have to do
    }
    throw new ApiError(`GET ${url} failed with ${response.status}${detail}`, response.status);
  }
  // parse() fails loudly on malformed responses instead of rendering garbage.
  return schema.parse(await response.json());
}

export function listComposers(
  opts: { q?: string; page?: number; sort?: ComposerSort } = {},
): Promise<ComposerPage> {
  const params = new URLSearchParams();
  if (opts.q) params.set("q", opts.q);
  params.set("page", String(opts.page ?? 1));
  params.set("limit", String(PAGE_SIZE));
  if (opts.sort) params.set("sort", opts.sort);
  return apiFetch(`/v1/composers?${params.toString()}`, ComposerPageSchema);
}

/** Fetch one composer; null when the id is unknown (404) or not a UUID (422). */
export async function getComposer(id: string): Promise<ComposerDetail | null> {
  try {
    return await apiFetch(`/v1/composers/${encodeURIComponent(id)}`, ComposerDetailSchema);
  } catch (err) {
    if (err instanceof ApiError && (err.status === 404 || err.status === 422)) return null;
    throw err;
  }
}

export function listComposerWorks(
  composerId: string,
  opts: { page?: number; sort?: WorkSort } = {},
): Promise<ComposerWorksPage> {
  const params = new URLSearchParams();
  params.set("page", String(opts.page ?? 1));
  params.set("limit", String(PAGE_SIZE));
  if (opts.sort) params.set("sort", opts.sort);
  return apiFetch(
    `/v1/composers/${encodeURIComponent(composerId)}/works?${params.toString()}`,
    ComposerWorksPageSchema,
  );
}
