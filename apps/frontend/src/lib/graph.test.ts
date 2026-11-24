import { describe, expect, it } from "vitest";

import {
  RELATION_ORDER,
  VIEWBOX,
  layout,
  relationLabel,
  relationsLabel,
  relationsOf,
  weightLabel,
} from "./graph";
import type { Connection } from "./schemas";

function makeConnection(overrides: Partial<Connection> = {}): Connection {
  return {
    direction: "out",
    entity_id: "00000000-0000-4000-8000-000000000001",
    kind: "person",
    label: "Clara Schumann",
    relation: "performed",
    relations: ["performed"],
    roles: ["pianist"],
    score: 1,
    via: [],
    weight: 10,
    ...overrides,
  };
}

describe("relationsOf", () => {
  it("returns empty list for no connections", () => {
    expect(relationsOf([])).toEqual([]);
  });

  it("orders known performance relations before alphabetical claim relations", () => {
    const connections = [
      makeConnection({ relation: "student_of" }),
      makeConnection({ relation: "appeared_with" }),
      makeConnection({ relation: "born_in" }),
      makeConnection({ relation: "performed" }),
      makeConnection({ relation: "performed" }),
      makeConnection({ relation: "performed_by" }),
    ];
    expect(relationsOf(connections)).toEqual([...RELATION_ORDER, "born_in", "student_of"]);
  });

  it("sorts claim-only relations alphabetically without duplicates", () => {
    const connections = [
      makeConnection({ relation: "teacher_of" }),
      makeConnection({ relation: "born_in" }),
      makeConnection({ relation: "teacher_of" }),
    ];
    expect(relationsOf(connections)).toEqual(["born_in", "teacher_of"]);
  });
});

describe("layout", () => {
  it("returns empty graph with centred viewBox when there are no connections", () => {
    const graph = layout([]);
    expect(graph.nodes).toEqual([]);
    expect(graph.relations).toEqual([]);
    expect(graph.centre).toEqual({ x: VIEWBOX / 2, y: VIEWBOX / 2, radius: 30 });

    const parts = graph.viewBox.split(" ").map(Number);
    expect(parts).toHaveLength(4);
    expect(parts.every((n) => Number.isFinite(n))).toBe(true);
    expect(parts[2]).toBeGreaterThan(60);
    expect(parts[3]).toBeGreaterThan(60);
  });

  it("places a single connection at inner radius with maximum weight styling", () => {
    const conn = makeConnection({ weight: 5, label: "Vienna" });
    const graph = layout([conn]);

    expect(graph.nodes).toHaveLength(1);
    const [node] = graph.nodes;
    expect(node).toBeDefined();
    if (!node) return;
    expect(node.rank).toBe(0);
    expect(node.group).toBe(0);
    expect(node.radius).toBeCloseTo(16);
    expect(node.strokeWidth).toBeCloseTo(4);

    const dist = Math.hypot(node.x - graph.centre.x, node.y - graph.centre.y);
    expect(dist).toBeCloseTo(96);
  });

  it("preserves global rank distance across relation sectors and scales by weight", () => {
    const strongest = makeConnection({ relation: "born_in", weight: 16, label: "Bonn" });
    const middle = makeConnection({ relation: "performed", weight: 4, label: "Liszt" });
    const weakest = makeConnection({ relation: "appeared_with", weight: 1, label: "Brahms" });

    const graph = layout([strongest, middle, weakest]);
    expect(graph.relations).toEqual(["performed", "appeared_with", "born_in"]);
    expect(graph.nodes.map((n) => n.connection.label)).toEqual(["Liszt", "Brahms", "Bonn"]);

    const byLabel = new Map(graph.nodes.map((n) => [n.connection.label, n]));
    const bonn = byLabel.get("Bonn");
    const liszt = byLabel.get("Liszt");
    const brahms = byLabel.get("Brahms");
    expect(bonn).toBeDefined();
    expect(liszt).toBeDefined();
    expect(brahms).toBeDefined();
    if (!bonn || !liszt || !brahms) return;

    expect(bonn.rank).toBe(0);
    expect(liszt.rank).toBe(1);
    expect(brahms.rank).toBe(2);

    expect(bonn.group).toBe(2);
    expect(liszt.group).toBe(0);
    expect(brahms.group).toBe(1);

    const dist = (n: typeof bonn) => Math.hypot(n.x - graph.centre.x, n.y - graph.centre.y);
    expect(dist(bonn)).toBeCloseTo(96);
    expect(dist(liszt)).toBeCloseTo(198);
    expect(dist(brahms)).toBeCloseTo(300);

    expect(bonn.radius).toBeCloseTo(16);
    expect(liszt.radius).toBeCloseTo(11);
    expect(brahms.radius).toBeCloseTo(8.5);
    expect(bonn.strokeWidth).toBeCloseTo(4);
    expect(liszt.strokeWidth).toBeCloseTo(2.5);
    expect(brahms.strokeWidth).toBeCloseTo(1.75);
  });

  it("anchors labels outward from the centre", () => {
    const connections = [
      makeConnection({ label: "One", relation: "performed", weight: 4 }),
      makeConnection({ label: "Two", relation: "performed", weight: 3 }),
      makeConnection({ label: "Three", relation: "performed", weight: 2 }),
      makeConnection({ label: "Four", relation: "performed", weight: 1 }),
    ];
    const graph = layout(connections);

    for (const node of graph.nodes) {
      if (node.x >= graph.centre.x) {
        expect(node.anchor).toBe("start");
        expect(node.labelOffset).toBeCloseTo(node.radius + 6);
      } else {
        expect(node.anchor).toBe("end");
        expect(node.labelOffset).toBeCloseTo(-(node.radius + 6));
      }
    }
  });

  it("handles zero maxWeight without dividing by zero", () => {
    const graph = layout([makeConnection({ weight: 0 })]);
    const [node] = graph.nodes;
    expect(node).toBeDefined();
    if (!node) return;
    expect(node.radius).toBe(6);
    expect(node.strokeWidth).toBe(1);
  });

  it("expands viewBox width to fit long labels and is deterministic", () => {
    const shortGraph = layout([makeConnection({ label: "Bach" })]);
    const longGraph = layout([
      makeConnection({ label: "Royal Concertgebouw Orchestra Amsterdam" }),
    ]);

    const shortWidth = Number(shortGraph.viewBox.split(" ")[2]);
    const longWidth = Number(longGraph.viewBox.split(" ")[2]);
    expect(longWidth).toBeGreaterThan(shortWidth);
    expect(layout([makeConnection({ label: "Bach" })])).toEqual(shortGraph);
  });
});

describe("relationLabel and relationsLabel", () => {
  it("formats known performance relations and falls back for claim relations", () => {
    expect(relationLabel("performed")).toBe("performed their music");
    expect(relationLabel("performed_by")).toBe("performed by");
    expect(relationLabel("appeared_with")).toBe("shared a stage");
    expect(relationLabel("born_in")).toBe("Born in");
    expect(relationLabel("student_of")).toBe("Student of");
    expect(relationLabel("")).toBe("");
  });

  it("joins all relations on a connection with a middle dot", () => {
    const conn = makeConnection({ relations: ["born_in", "died_in"] });
    expect(relationsLabel(conn)).toBe("Born in · Died in");
    expect(relationsLabel(makeConnection({ relations: [] }))).toBe("");
  });
});

describe("weightLabel", () => {
  it("uses relation-specific units with singular and plural forms", () => {
    expect(weightLabel("appeared_with", 1)).toBe("1 shared event");
    expect(weightLabel("appeared_with", 4)).toBe("4 shared events");
    expect(weightLabel("appeared_with", 0)).toBe("0 shared events");

    expect(weightLabel("performed", 1)).toBe("1 performance");
    expect(weightLabel("performed", 12)).toBe("12 performances");
    expect(weightLabel("performed_by", 1)).toBe("1 performance");
    expect(weightLabel("performed_by", 2)).toBe("2 performances");

    expect(weightLabel("born_in", 1)).toBe("1 source");
    expect(weightLabel("born_in", 3)).toBe("3 sources");
  });
});
