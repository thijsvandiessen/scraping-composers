import type { Connection } from "./schemas";

/**
 * Radial layout for one entity's neighbourhood.
 *
 * Two channels carry meaning, and only two: distance from the centre is rank
 * (the strongest connection sits closest), and the sector a node falls in is
 * its relation. Everything else — node size, stroke width — restates the
 * weight, so nothing is encoded that the list below the graph doesn't also
 * spell out in words.
 *
 * The layout is a plain function of the API's response: same connections, same
 * picture, on the server and on a reload. There is no simulation to settle and
 * no client-side state, which is what lets the graph be server-rendered SVG
 * that works with scripting off.
 */

export const VIEWBOX = 760;

const CENTRE = VIEWBOX / 2;
const INNER_RADIUS = 96; // closest a neighbour is drawn to the centre
const OUTER_RADIUS = 300; // ... and furthest, leaving room for labels
const CENTRE_NODE_RADIUS = 30;

/** Relations get a fixed slot each, so a colour means the same thing on every page. */
export const RELATION_ORDER = ["performed", "performed_by", "appeared_with"] as const;

export interface GraphNode {
  connection: Connection;
  x: number;
  y: number;
  radius: number;
  /** Which end of the label to anchor, so text grows away from the centre. */
  anchor: "start" | "end";
  /** Where the label sits relative to the node, in px along x. */
  labelOffset: number;
  strokeWidth: number;
  /** Index into the relation palette; claim relations share the last slot. */
  group: number;
}

export interface Graph {
  nodes: GraphNode[];
  centre: { x: number; y: number; radius: number };
  /** Relations present, in drawing order — the legend renders from this. */
  relations: string[];
  /** Fitted to what was actually drawn, labels included. See `fit`. */
  viewBox: string;
}

const LABEL_FONT_SIZE = 13;
const CENTRE_LABEL_FONT_SIZE = 15;
/** Rough advance width per character at the label size, for the fitted box. */
const LABEL_CHAR_WIDTH = 0.55;
const FIT_PADDING = 14;

/**
 * The box that actually contains the drawing, labels included.
 *
 * A fixed square would leave a small neighbourhood marooned in the middle of a
 * lot of nothing, and would clip a long name on the right-hand rim. Measuring
 * what was placed — the radial layout never fills its nominal square — lets the
 * same code serve a three-node graph and a fifty-node one.
 *
 * Label widths are estimated from character count rather than measured: this
 * runs during SSR, where there is no text metrics API, and being a few pixels
 * generous costs nothing but whitespace.
 */
function fit(nodes: GraphNode[], centre: { x: number; y: number; radius: number }): string {
  let left = centre.x - centre.radius;
  let right = centre.x + centre.radius;
  let top = centre.y - centre.radius;
  // the centre's own label hangs below its circle
  let bottom = centre.y + centre.radius + CENTRE_LABEL_FONT_SIZE + 12;

  for (const node of nodes) {
    const labelWidth = node.connection.label.length * LABEL_CHAR_WIDTH * LABEL_FONT_SIZE;
    const labelEnd =
      node.x + node.labelOffset + (node.anchor === "start" ? labelWidth : -labelWidth);
    left = Math.min(left, node.x - node.radius, labelEnd);
    right = Math.max(right, node.x + node.radius, labelEnd);
    top = Math.min(top, node.y - node.radius, node.y - LABEL_FONT_SIZE);
    bottom = Math.max(bottom, node.y + node.radius, node.y + LABEL_FONT_SIZE);
  }

  const x = left - FIT_PADDING;
  const y = top - FIT_PADDING;
  const width = right - left + 2 * FIT_PADDING;
  const height = bottom - top + 2 * FIT_PADDING;
  return `${x.toFixed(1)} ${y.toFixed(1)} ${width.toFixed(1)} ${height.toFixed(1)}`;
}

/** Node size by weight, on a square-root scale so a hub can't swamp the page. */
function nodeRadius(weight: number, maxWeight: number): number {
  if (maxWeight <= 0) return 6;
  return 6 + 10 * Math.sqrt(Math.min(weight, maxWeight) / maxWeight);
}

function strokeWidth(weight: number, maxWeight: number): number {
  if (maxWeight <= 0) return 1;
  return 1 + 3 * Math.sqrt(Math.min(weight, maxWeight) / maxWeight);
}

/**
 * Relations in a stable drawing order: the performance relations first, in the
 * fixed order above, then whatever claim predicates came back, alphabetically.
 * A page for one composer and a page for another then colour the same relation
 * the same way.
 */
export function relationsOf(connections: Connection[]): string[] {
  const present = new Set(connections.map((c) => c.relation));
  const known = RELATION_ORDER.filter((relation) => present.has(relation));
  const claims = [...present]
    .filter((relation) => !RELATION_ORDER.includes(relation as never))
    .sort();
  return [...known, ...claims];
}

/**
 * Place `connections` (already ranked best-first by the API) around a centre.
 *
 * Each relation is given an arc proportional to how many neighbours it has, so
 * a lone `born_in` edge still gets a wedge of its own instead of being buried
 * among two dozen performance edges.
 */
export function layout(connections: Connection[]): Graph {
  const relations = relationsOf(connections);
  const centre = { x: CENTRE, y: CENTRE, radius: CENTRE_NODE_RADIUS };
  if (connections.length === 0) return { nodes: [], centre, relations, viewBox: fit([], centre) };

  const maxWeight = Math.max(...connections.map((c) => c.weight));
  // Rank over the whole neighbourhood, not within a sector: the innermost node
  // is the single strongest connection whatever relation it happens to be.
  const rankOf = new Map(connections.map((connection, index) => [connection, index]));

  const nodes: GraphNode[] = [];
  let angle = -Math.PI / 2; // start at twelve o'clock and sweep clockwise
  for (const relation of relations) {
    const group = connections.filter((c) => c.relation === relation);
    const sector = (2 * Math.PI * group.length) / connections.length;
    // Half a step of padding at each end keeps neighbouring sectors from
    // butting up against each other.
    const step = sector / group.length;
    group.forEach((connection, index) => {
      const theta = angle + step * (index + 0.5);
      const rank = rankOf.get(connection) ?? 0;
      const spread = connections.length > 1 ? rank / (connections.length - 1) : 0;
      const distance = INNER_RADIUS + spread * (OUTER_RADIUS - INNER_RADIUS);
      const x = centre.x + distance * Math.cos(theta);
      const y = centre.y + distance * Math.sin(theta);
      const radius = nodeRadius(connection.weight, maxWeight);
      const rightHalf = x >= centre.x;
      nodes.push({
        connection,
        x,
        y,
        radius,
        anchor: rightHalf ? "start" : "end",
        labelOffset: (rightHalf ? 1 : -1) * (radius + 6),
        strokeWidth: strokeWidth(connection.weight, maxWeight),
        group: relations.indexOf(relation),
      });
    });
    angle += sector;
  }
  return { nodes, centre, relations, viewBox: fit(nodes, centre) };
}

const RELATION_LABELS: Record<string, string> = {
  performed: "performed their music",
  performed_by: "performed by",
  appeared_with: "shared a stage",
};

/** A phrase a reader can put after the entity's name. */
export function relationLabel(relation: string): string {
  const known = RELATION_LABELS[relation];
  if (known) return known;
  const words = relation.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * What an edge's weight counts, which is not the same thing for every relation:
 * performances for the performance edges, shared billings for a stage partner,
 * and — for a claim — the number of sources that independently assert it.
 */
export function weightLabel(relation: string, weight: number): string {
  const unit =
    relation === "appeared_with"
      ? "shared event"
      : relation === "performed" || relation === "performed_by"
        ? "performance"
        : "source";
  return `${weight} ${unit}${weight === 1 ? "" : "s"}`;
}
