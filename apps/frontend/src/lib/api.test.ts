import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  GRAPH_LIMIT,
  PAGE_SIZE,
  getComposer,
  getConnections,
  listComposerWorks,
  listComposers,
} from "./api";

const COMPOSER_ID = "11111111-1111-4111-8111-111111111111";
const WORK_ID = "22222222-2222-4222-8222-222222222222";
const NEIGHBOUR_ID = "33333333-3333-4333-8333-333333333333";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("api client", () => {
  const originalApiUrl = process.env.GOLD_API_URL;
  let fetchMock: ReturnType<typeof vi.fn<typeof fetch>>;

  beforeEach(() => {
    delete process.env.GOLD_API_URL;
    fetchMock = vi.fn<typeof fetch>();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    if (originalApiUrl === undefined) {
      delete process.env.GOLD_API_URL;
    } else {
      process.env.GOLD_API_URL = originalApiUrl;
    }
  });

  it("uses GOLD_API_URL without trailing slashes and defaults to localhost", async () => {
    const page = { items: [], limit: PAGE_SIZE, page: 1, total: 0 };
    fetchMock.mockResolvedValueOnce(jsonResponse(page));
    await listComposers();
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://127.0.0.1:8000/v1/composers?page=1&limit=${PAGE_SIZE}`,
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    );

    process.env.GOLD_API_URL = "http://gold.internal:9000///";
    fetchMock.mockResolvedValueOnce(jsonResponse(page));
    await listComposers({ q: "Bach", page: 2, sort: "concerts" });
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://gold.internal:9000/v1/composers?q=Bach&page=2&limit=${PAGE_SIZE}&sort=concerts`,
      expect.any(Object),
    );
  });

  it("parses composer pages and rejects malformed responses", async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({
        items: [
          {
            id: COMPOSER_ID,
            label: "Johann Sebastian Bach",
            created_at: "2026-01-01T00:00:00Z",
          },
        ],
        limit: PAGE_SIZE,
        page: 1,
        total: 1,
      }),
    );

    const page = await listComposers();
    expect(page.total).toBe(1);
    expect(page.items[0]?.concert_count).toBe(0);
    expect(page.items[0]?.recording_count).toBe(0);

    fetchMock.mockResolvedValueOnce(jsonResponse({ unexpected: true }));
    await expect(listComposers()).rejects.toThrow();
  });

  it("fetches a composer detail and returns null on 404 or 422", async () => {
    const detail = {
      id: COMPOSER_ID,
      kind: "person",
      label: "Clara Schumann",
      created_at: "2026-01-01T00:00:00Z",
      claims: [],
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(detail));
    await expect(getComposer(COMPOSER_ID)).resolves.toEqual(detail);

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Not found" }, 404));
    await expect(getComposer(COMPOSER_ID)).resolves.toBeNull();

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Invalid UUID" }, 422));
    await expect(getComposer("not a uuid/1")).resolves.toBeNull();
    expect(fetchMock).toHaveBeenLastCalledWith(
      "http://127.0.0.1:8000/v1/composers/not%20a%20uuid%2F1",
      expect.any(Object),
    );
  });

  it("throws ApiError with status and detail on server errors", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "db offline" }, 503));
    await expect(getComposer(COMPOSER_ID)).rejects.toMatchObject({
      name: "ApiError",
      status: 503,
      message: expect.stringContaining('"db offline"'),
    });

    fetchMock.mockResolvedValueOnce(new Response("Bad Gateway", { status: 502 }));
    await expect(getComposer(COMPOSER_ID)).rejects.toMatchObject({
      name: "ApiError",
      status: 502,
      message: `GET http://127.0.0.1:8000/v1/composers/${COMPOSER_ID} failed with 502`,
    });
  });

  it("lists composer works with optional page and sort parameters", async () => {
    const worksPage = {
      composer_id: COMPOSER_ID,
      composer_label: "Bach",
      items: [
        {
          id: WORK_ID,
          canonical_title: "Goldberg Variations",
          catalogue: "BWV 988",
          mention_count: 14,
          musical_key: "G major",
          number: null,
          opus_number: null,
          premiere_date: null,
          proof: [],
          work_type: "keyboard",
        },
      ],
      limit: PAGE_SIZE,
      page: 2,
      total: 1,
    };
    fetchMock.mockResolvedValueOnce(jsonResponse(worksPage));

    const result = await listComposerWorks(COMPOSER_ID, { page: 2, sort: "mentions" });
    expect(result.items[0]?.identifiers).toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith(
      `http://127.0.0.1:8000/v1/composers/${COMPOSER_ID}/works?page=2&limit=${PAGE_SIZE}&sort=mentions`,
      expect.any(Object),
    );
  });

  it("fetches entity connections, builds optional query params, and handles 404/422", async () => {
    const connections = {
      entity_id: COMPOSER_ID,
      kind: "person",
      label: "Bach",
      limit: GRAPH_LIMIT,
      rank: "affinity",
      total: 1,
      items: [
        {
          direction: "in",
          entity_id: NEIGHBOUR_ID,
          kind: "person",
          label: "Glenn Gould",
          relation: "performed",
          relations: ["performed"],
          roles: ["pianist"],
          score: 0.95,
          via: [],
          weight: 5,
        },
      ],
    };

    fetchMock.mockResolvedValueOnce(jsonResponse(connections));
    await expect(getConnections(COMPOSER_ID, { minWeight: 1 })).resolves.toEqual(connections);
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://127.0.0.1:8000/v1/entities/${COMPOSER_ID}/connections?limit=${GRAPH_LIMIT}`,
      expect.any(Object),
    );

    fetchMock.mockResolvedValueOnce(jsonResponse(connections));
    await getConnections(COMPOSER_ID, {
      limit: 12,
      perRelation: 4,
      minWeight: 3,
      rank: "weight",
    });
    expect(fetchMock).toHaveBeenLastCalledWith(
      `http://127.0.0.1:8000/v1/entities/${COMPOSER_ID}/connections?limit=12&per_relation=4&min_weight=3&rank=weight`,
      expect.any(Object),
    );

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Not found" }, 404));
    await expect(getConnections(COMPOSER_ID)).resolves.toBeNull();

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Invalid UUID" }, 422));
    await expect(getConnections("bad-id")).resolves.toBeNull();

    fetchMock.mockResolvedValueOnce(jsonResponse({ detail: "Server error" }, 500));
    await expect(getConnections(COMPOSER_ID)).rejects.toBeInstanceOf(ApiError);
  });

  it("wraps network errors and timeouts in ApiError without HTTP status", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("fetch failed"));
    await expect(listComposers()).rejects.toMatchObject({
      name: "ApiError",
      status: undefined,
      message: expect.stringContaining("Could not reach the composer API"),
    });

    fetchMock.mockRejectedValueOnce(new DOMException("The operation timed out", "TimeoutError"));
    await expect(listComposers()).rejects.toMatchObject({
      name: "ApiError",
      status: undefined,
      message: expect.stringContaining("did not respond within 10s"),
    });
  });
});
