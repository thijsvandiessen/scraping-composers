# cli

`composer-ingest`, the command-line pipeline. Global flags come before the
subcommand: `-v` for DEBUG (third-party libraries included), `--log-level`
to override `LOG_LEVEL`.

| Command | Does | Details |
| --- | --- | --- |
| `fetch <source>` | fetch a source into the bronze bucket (`--max-pages` for a smoke run) | [composer-bronze](../../packages/composer-bronze/README.md) |
| `process <source>` | load every loadable snapshot of a source into silver, oldest first (`--run-id` to pick one) | [composer-warehouse](../../packages/composer-warehouse/README.md#ingest-flow) |
| `stats`, `runs` | dataset counts, and the ingest run log | |
| `claims <name>` | an entity's claims and which source asserts each (`--kind`, `--predicate`) | |
| `works <query>` | resolved works by composer or title, with the titles they were seen under | |
| `review`, `rematch` | the work mentions the matcher was unsure about (`--accept`, `--new`), and re-running matching | [composer-warehouse](../../packages/composer-warehouse/README.md#works) |
| `work-identity` | per source: mentions, works, and the share of works with a catalogue number, an external id, either, and a link to a parent work | [composer-warehouse](../../packages/composer-warehouse/README.md#works) |
| `dedupe-persons`, `person-review` | link duplicate people, and review the pairs the model was unsure about | [composer-warehouse](../../packages/composer-warehouse/README.md#people-deduplication) |
| `person-train`, `person-eval` | refit the person linkage model, and score it on the evaluation set | [MODEL.md](../../packages/composer-warehouse/src/composer_warehouse/persons/MODEL.md) |
| `derive-concerts`, `derive-recordings` | group mentions into concerts and recordings (also run before every promote) | [composer-warehouse](../../packages/composer-warehouse/README.md#concerts-and-recordings) |
| `rebuild-silver` | replay the whole bucket into a fresh silver | [composer-warehouse](../../packages/composer-warehouse/README.md#rebuilding-silver) |
| `promote` | rebuild gold from silver (work links, concerts and recordings are re-derived first) | [composer-gold](../../packages/composer-gold/README.md) |
| `export-kumu`, `export-neo4j` | export gold to a Kumu blueprint or a Neo4j graph | [composer-gold](../../packages/composer-gold/README.md#mapping-gold-in-kumu), [composer-graph](../../packages/composer-graph/README.md) |

## Tests

```sh
uv run --directory apps/cli pytest --rootdir . --cov=composer_cli
```

`test_cli.py` calls each command function with an `argparse.Namespace` against a
`tmp_path` SQLite database and bucket. No network: sources are `FakeSource`s.

| Area | Covers |
| --- | --- |
| Queries | `claims` (per-source attribution, filters, record provenance, collapsing repeats, unknown names), `stats` (empty, entities, works, person dedupe counts), `runs`, `works`. |
| Ingest | `fetch` → `process` round trip, a failing source, `process` loading every loadable run, no runs to load, `derive-concerts`, and `rebuild-silver` (every bucket source, rejecting a URL with nothing to swap). |
| Works | `review` listing and `--new`, `rematch` (including a source's structured catalogue field), `work-identity` shares per source. |
| People | `dedupe-persons` (links, reviews, refused merges, `--reset`, `--recluster-only`), `person-review` accept and reject, `person-train` (model and dataset written, no dataset unless asked), `person-eval`. |
| Gold | `promote` passing thresholds, `export-kumu` (blueprint written, no gold database). |
| Entry point | Routing to subcommands, no subcommand, `-v` and `--log-level`, a bad `LOG_LEVEL`, `DATABASE_URL`. |

Not covered: `export-neo4j` (its load is tested in `composer-graph`), the
`export-kumu` failure path, and `review --accept`.
