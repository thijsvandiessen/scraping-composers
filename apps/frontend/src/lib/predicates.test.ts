import { describe, expect, it } from "vitest";

import { HIDDEN_PREDICATES, METRIC_PREDICATES, predicateLabel } from "./predicates";

describe("predicateLabel", () => {
  it("returns human labels for known biographical predicates", () => {
    expect(predicateLabel("has_profession")).toBe("Profession");
    expect(predicateLabel("born_on")).toBe("Born");
    expect(predicateLabel("died_on")).toBe("Died");
    expect(predicateLabel("born_in")).toBe("Place of birth");
    expect(predicateLabel("died_in")).toBe("Place of death");
    expect(predicateLabel("citizen_of")).toBe("Citizenship");
  });

  it("returns human labels for work and edition predicates", () => {
    expect(predicateLabel("composed_in")).toBe("Year composed");
    expect(predicateLabel("duration_minutes")).toBe("Duration (minutes)");
    expect(predicateLabel("written_for")).toBe("Scored for");
    expect(predicateLabel("first_performed_on")).toBe("Premiered");
    expect(predicateLabel("program_note_by")).toBe("Programme note by");
    expect(predicateLabel("ismn")).toBe("ISMN");
    expect(predicateLabel("isbn")).toBe("ISBN");
  });

  it("returns subject-oriented labels for lineage and family predicates", () => {
    expect(predicateLabel("student_of")).toBe("Studied with");
    expect(predicateLabel("teacher_of")).toBe("Taught");
    expect(predicateLabel("child_of")).toBe("Parent");
    expect(predicateLabel("parent_of")).toBe("Child");
  });

  it("returns human labels for metric predicates", () => {
    expect(predicateLabel("sitelink_count")).toBe("Wikipedia sitelinks");
    expect(predicateLabel("statement_count")).toBe("Wikidata statements");
    expect(predicateLabel("identifier_count")).toBe("External identifiers");
    expect(predicateLabel("work_count")).toBe("Catalogued works");
  });

  it("falls back to sentence-cased words for unknown predicates", () => {
    expect(predicateLabel("award_received")).toBe("Award received");
    expect(predicateLabel("first_played_in_city")).toBe("First played in city");
    expect(predicateLabel("nickname")).toBe("Nickname");
  });

  it("handles empty string gracefully", () => {
    expect(predicateLabel("")).toBe("");
  });
});

describe("predicate sets", () => {
  it("lists all metric bookkeeping predicates", () => {
    expect([...METRIC_PREDICATES].sort()).toEqual([
      "identifier_count",
      "sitelink_count",
      "statement_count",
      "work_count",
    ]);
  });

  it("lists all hidden source-specific bookkeeping predicates", () => {
    expect([...HIDDEN_PREDICATES].sort()).toEqual(["first_season", "last_season", "program_count"]);
  });

  it("keeps metric and hidden predicate sets disjoint", () => {
    for (const predicate of METRIC_PREDICATES) {
      expect(HIDDEN_PREDICATES.has(predicate)).toBe(false);
    }
  });
});
