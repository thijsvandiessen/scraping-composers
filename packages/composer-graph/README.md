# composer-graph

Loads the *whole* of gold into a local Neo4j as a property graph, for
exploration. It replaces whatever the database held before, so Neo4j is never a
source of truth.

```sh
make graph    # starts the neo4j container, then runs export-neo4j (~1 minute for the full gold.db)
```

That is `docker compose up -d --wait neo4j` followed by `uv run composer-ingest
export-neo4j`. Open Neo4j Browser at http://localhost:7475 and connect to
`neo4j://localhost:7688` as `neo4j` / `composers`.

People, ensembles, places, genres, movements, professions and periods become
labelled nodes, with their literal claims (`born_on`, `sitelink_count`, …) as
properties. Works, concerts and recordings are nodes too, linked by `COMPOSED_BY`,
`PERFORMED_IN {role}` and `PROGRAMMED`; a work node carries its `identifiers`
("BWV 1048", "wikidata Q…") and `wikidata_id`, and a part, arrangement or version
points at its parent with `PART_OF`, `ARRANGEMENT_OF` or `VERSION_OF {method}`. Object claims become relationships
(`BORN_IN`, `HAS_GENRE`, …) that carry the sources asserting them. That includes
the Wikidata person ↔ person relations: `STUDENT_OF`, `TEACHER_OF`,
`INFLUENCED_BY`, and the family links, and the genre and movement hierarchy as
`SUBCLASS_OF`, and an ensemble's `BASED_IN` (its home city) and `PART_OF`, so `(:Genre)<-[:SUBCLASS_OF*0..]-(:Genre)` is a genre and
everything below it. A node whose entity carries a Wikidata QID has it as
`wikidata_id`, the stable handle to match on, and ensembles their
`ensemble_type` (`orchestra`, `choir`, …). Nothing is pre-aggregated: how strongly
two entities are connected is a count over paths. [`queries.cypher`](queries.cypher)
has starter queries for that: performer → composer, composers programmed
together (raw and normalised), co-performers, shared birthplaces, genre roll-up and orphans, orchestras by home city, and Graph Data
Science similarity and community detection. The compose service ships the GDS
plugin.

The host ports are 7475/7688 rather than Neo4j's defaults, which Neo4j Desktop
tends to own already. Override the server with `NEO4J_URL`, `NEO4J_USER` and
`NEO4J_PASSWORD` (or the matching `--neo4j-*` flags).

`rows.py` reads gold out as node and relationship rows; `load.py` writes them to
Neo4j in batches.

## Tests

```sh
uv run --directory packages/composer-graph pytest --rootdir . --cov=composer_graph
```

| File | Covers |
| --- | --- |
| `test_rows.py` | Reading gold as graph rows: labelled entity nodes with literal claims as properties, concert and work nodes (with their identifiers and Wikidata id), `PART_OF` between works, `COMPOSED_BY`, `PERFORMED_IN` with the role, `PROGRAMMED`, object claims as typed relationships, `SUBCLASS_OF` between genres, the `wikidata_id` node property, and an anchored ensemble's `ensemble_type` and `BASED_IN`. |
| `test_load.py` | Loading into a real Neo4j: the event graph, performer → composer strength as a path count, composers connected by shared programmes, and a second load replacing the first. |

`test_load.py` is skipped unless `COMPOSER_TEST_NEO4J_URL` points at a server
(CI's `test-neo4j` job, or `docker compose up -d neo4j` locally with
`bolt://localhost:7688`). The load empties the database it is pointed at.
