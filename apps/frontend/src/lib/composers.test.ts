import { describe, expect, it } from "vitest";

import { activityCounts, composerListUrl, parseComposerListQuery } from "./composers";

describe("parseComposerListQuery", () => {
  it("returns defaults when query parameters are absent", () => {
    const query = parseComposerListQuery(new URL("http://localhost/composers"));
    expect(query).toEqual({ q: "", sort: "label", page: 1 });
  });

  it("parses valid q, sort, and page parameters", () => {
    const url = new URL("http://localhost/composers?q=%20Bach%20&sort=concerts&page=3");
    expect(parseComposerListQuery(url)).toEqual({
      q: "Bach",
      sort: "concerts",
      page: 3,
    });
  });

  it("accepts recordings sort", () => {
    const url = new URL("http://localhost/composers?sort=recordings");
    expect(parseComposerListQuery(url).sort).toBe("recordings");
  });

  it("falls back to label for unknown or empty sort", () => {
    expect(parseComposerListQuery(new URL("http://localhost/composers?sort=popular")).sort).toBe(
      "label",
    );
    expect(parseComposerListQuery(new URL("http://localhost/composers?sort=")).sort).toBe("label");
  });

  it("trims whitespace-only search queries to empty string", () => {
    const url = new URL("http://localhost/composers?q=%20%20%09");
    expect(parseComposerListQuery(url).q).toBe("");
  });

  it("falls back to page 1 for non-positive, fractional, or non-numeric pages", () => {
    for (const bad of ["0", "-2", "1.5", "abc", "", "NaN", "Infinity"]) {
      const url = new URL(`http://localhost/composers?page=${encodeURIComponent(bad)}`);
      expect(parseComposerListQuery(url).page, `page=${bad}`).toBe(1);
    }
  });
});

describe("composerListUrl", () => {
  it("omits all parameters when at defaults", () => {
    expect(composerListUrl({ q: "", sort: "label", page: 1 })).toBe("/composers");
  });

  it("includes only non-default parameters", () => {
    expect(composerListUrl({ q: "Mozart", sort: "label", page: 1 })).toBe("/composers?q=Mozart");
    expect(composerListUrl({ q: "", sort: "concerts", page: 1 })).toBe("/composers?sort=concerts");
    expect(composerListUrl({ q: "", sort: "label", page: 2 })).toBe("/composers?page=2");
  });

  it("omits page when page is 1 or less", () => {
    expect(composerListUrl({ q: "", sort: "label", page: 0 })).toBe("/composers");
    expect(composerListUrl({ q: "", sort: "label", page: -5 })).toBe("/composers");
  });

  it("encodes all parameters and round-trips through parseComposerListQuery", () => {
    const input = { q: "Clara Schumann", sort: "recordings" as const, page: 4 };
    const path = composerListUrl(input);
    expect(path).toBe("/composers?q=Clara+Schumann&sort=recordings&page=4");
    expect(parseComposerListQuery(new URL(path, "http://localhost"))).toEqual(input);
  });
});

describe("activityCounts", () => {
  it("returns empty string when both counts are zero or negative", () => {
    expect(activityCounts(0, 0)).toBe("");
    expect(activityCounts(-1, 0)).toBe("");
  });

  it("formats singular and plural concert counts alone", () => {
    expect(activityCounts(1, 0)).toBe("1 concert");
    expect(activityCounts(19, 0)).toBe("19 concerts");
  });

  it("formats singular and plural album counts alone", () => {
    expect(activityCounts(0, 1)).toBe("1 album");
    expect(activityCounts(0, 310)).toBe("310 albums");
  });

  it("joins concerts and albums with a middle dot", () => {
    expect(activityCounts(1, 1)).toBe("1 concert · 1 album");
    expect(activityCounts(19, 310)).toBe("19 concerts · 310 albums");
  });
});
