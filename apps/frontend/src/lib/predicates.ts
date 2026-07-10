// Human labels for the claim predicates the pipeline is known to emit
// (packages/composer-scrapers/.../wikidata/parse.py and friends). Unknown
// predicates fall back to a readable transform — nothing is hidden.

const PREDICATE_LABELS: Record<string, string> = {
  has_profession: "Profession",
  born_on: "Born",
  died_on: "Died",
  born_in: "Place of birth",
  died_in: "Place of death",
  citizen_of: "Citizenship",
  has_genre: "Genre",
  in_movement: "Movement",
  performs_as: "Performs as",
  sitelink_count: "Wikipedia sitelinks",
  statement_count: "Wikidata statements",
  identifier_count: "External identifiers",
  work_count: "Catalogued works",
};

// Popularity/bookkeeping counters, shown apart from the biographical facts.
export const METRIC_PREDICATES = new Set([
  "sitelink_count",
  "statement_count",
  "identifier_count",
  "work_count",
]);

export function predicateLabel(predicate: string): string {
  const known = PREDICATE_LABELS[predicate];
  if (known) return known;
  const words = predicate.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
