# composer-models

The SQLAlchemy schema shared by silver and gold (gold is a curated copy of
silver, so they are the same schema), the engine helpers, the dedup keys and
seeded UUIDs that define entity identity, and the Alembic migrations.

## Data model

Entities connected by claims (the Wikidata pattern), on top of a raw
provenance layer. Silver records what sources say, verbatim, plus the matching
passes over it; curation happens when it is promoted into gold.

- **`sources`** — where data comes from: one row per source.
- **`ingest_runs`** — the collection log: one row per ingest, with source,
  timestamps, status, and seen/new counts.
- **`entity_records`** — raw records per source, unique on
  `(source, external_id)`. Stores the original payload as JSON plus
  `first_seen_at`/`last_seen_at` timestamps and run ids. Re-ingesting is idempotent.
- **`entities`** — canonical, deduplicated nodes. `kind` says what a node is:
  `person`, `profession`, `period`, `genre`, `place`, `work`, `ensemble`,
  `publisher`, `instrumentation` (an open set — `kind` is a plain string column,
  and a claim naming a new one creates it). Records from different sources link
  here via `(kind, dedup_key)` — a normalized label (`normalize.py`:
  name-order flipping, diacritic stripping, case/punctuation folding) — so
  adding a second source attaches to existing entities instead of duplicating
  them. Entity ids are `uuid5(kind + dedup_key)`, so they are stable across
  rebuilds; a label guarded into `ensemble` by
  [`composer_schema.kinds`](../composer-schema/README.md) gets a different id.
  `canonical_entity_id` points a duplicate person at its cluster canonical.
- **`claims`** — typed edges between entities, e.g.
  `person --has_profession--> profession`, `person --born_in--> place`,
  `person --composed--> work`. The object is either another entity
  (`object_id`) or a literal (`value`, e.g. a date string). Every claim
  carries the source and the raw record it was extracted from, so two sources
  asserting conflicting facts coexist as two claims.
- **`raw_work_mentions`** — one row per `(composer, title)` a source reported,
  with the full performance context kept in `raw`, idempotent on
  `(source, external_id)`. Each carries the matcher's decision: `match_status`
  (`auto_matched`/`needs_review`/`created`/`manual_matched`), score and method.
- **`works`** and **`work_titles`** — canonical compositions and every title a
  work was seen under. Work ids are assigned at creation.
- **`person_matches`** — scored person pairs and review decisions from
  `dedupe-persons`.
- **`concerts`** and **`recordings`** (with their participants and
  programmes) — derived from the mentions in silver, copied into gold.

## Schema and migrations

The two backends get schema changes differently, and both answers are
deliberate.

**SQLite has no migrations, and doesn't need any.** The schema comes from
`Base.metadata.create_all`, and a schema change means running `rebuild-silver`:
bronze holds the full documents, so nothing in silver is irreplaceable. SQLite
has no real `ALTER`, so every non-trivial migration would be a table-copy
recipe.

**Postgres uses Alembic** (`migrations/`), because a hosted database is not
disposable the same way. `uv run alembic upgrade head` is the whole bootstrap
for an empty database — it creates the schema itself, not just the tables in
it. `rebuild-silver` still builds its staging schema from the models directly
and stamps it at head, so a rebuilt database looks migrated rather than
hand-made.

What keeps the two from drifting apart is a test, not discipline:
`test_head_matches_the_models` asserts that `alembic upgrade head` produces
exactly what `create_all` would. Add a column to a model without writing a
revision and it fails. **Always autogenerate against Postgres** — see
`CLAUDE.md` for the commands.

Moving an existing SQLite silver to Postgres is a **rebuild from bronze**, not
a dump and restore: `DATABASE_URL=postgresql+psycopg://… uv run composer-ingest
rebuild-silver`. Entity ids are deterministic, so they survive; work ids and
the run log do not.

Silver lives in its own schema (`SILVER_SCHEMA`, default `silver`), never in
`public`, because `rebuild-silver` replaces that schema wholesale. The role
needs `CREATE` on the database — `GRANT CREATE ON DATABASE composers TO app` —
and nothing more.

## Tests

```sh
uv run --directory packages/composer-models pytest --rootdir . --cov=composer_models
```

`test_migrations.py` only runs against Postgres (`COMPOSER_TEST_POSTGRES_URL`),
the backend where a database is migrated rather than replayed. `testing.py`
provides the Postgres fixtures the other members use.

| File | Covers |
| --- | --- |
| `test_normalize.py` | Dedup keys (name order, digits, commas, Wikidata suffixes, the Postgres index length bound), stable entity UUIDs per kind and key, stable concert/recording UUIDs per kind, source and key, and reading a QID back out of a key. |
| `test_db.py` | `init_db` creating every table, an entity round-trip, unbounded scraper-sourced columns, unique key columns, and rejecting a schema name that cannot be escaped. |
| `test_migrations.py` | Postgres only: `upgrade head` creating the schema from nothing, and head matching the models. |

This package's tests don't reach `stamp_head` in `alembic_support.py` or the
Postgres sequence resync in `db.py`; the first runs under the Postgres tests of
`composer-warehouse` (rebuild), the second under `composer-gold` (promote).
