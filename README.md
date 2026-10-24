# composer-ingest

Ingests classical composer, work, concert and recording data from 22 sources into
a database, with full provenance: every record knows which source it came from,
when it was first and last seen, and which ingest run produced it.

This page is the overview. Each package and app has a README of its own with its
domain details and what its tests cover; the [layout](#layout) below links to
them.

## Why this exists

The project started from one question: what connects these composers in ways
that aren't obvious? Teacher and pupil, a shared publisher, a premiere by the
same orchestra, a work that kept being programmed decades later. Links like
these are spread over many sources and rarely sit side by side. The idea was to
put them on a timeline, where they would become visible.

The catch is that a timeline showing every kind of connection at once isn't
clear. Too many options, and the connections you were looking for get lost.
So the work now is on the ontology: a small, deliberate set of entity kinds and
claim predicates (`student_of`, `composed_by`, `performed`, `born_in`, …), each
with one clear meaning. Then a timeline can be built for a specific question,
like who taught whom or where a work was played, and show only the connections
that answer it.

The rest of the pipeline is groundwork for that. Collecting from many sources
with full provenance means each connection on a timeline can show who asserted
it, and two sources that disagree stay visible as two claims.

## How it fits together

Data lives in three tiers:

- **Bronze** — the raw NDJSON bucket (`raw-data/`), written by `fetch`:
  everything every source said, verbatim. It is the only way data enters the
  pipeline, so everything downstream can be rebuilt from it.
- **Silver** (`composers.db`) — the staging database built from bronze by
  `process`: raw records with provenance, plus the interpretation passes over
  them (entity resolution, claims, work matching, person dedupe, concerts and
  recordings, human review decisions).
- **Gold** (`gold.db`) — the curated copy rebuilt from silver by `promote`,
  which the product API and the frontend serve.

```
sources ──fetch──▶ bronze ──process──▶ silver ──promote──▶ gold ──▶ consumer API ──▶ frontend
                                         ▲                    └──▶ Kumu, Neo4j exports
                       rebuild-silver ───┘ (replays bronze)
```

Entities are connected by claims, the Wikidata pattern: two sources asserting
conflicting facts coexist as two claims, each carrying its source and the raw
record it came from.

## Usage

```sh
uv sync

# scrapers identify themselves (User-Agent) to the sites they crawl; a
# reachable contact email is required before fetching
export SCRAPER_CONTACT_EMAIL="you@example.com"

uv run composer-ingest fetch imslp                # source → bronze
uv run composer-ingest fetch imslp --max-pages 1  # quick test run
uv run composer-ingest process imslp              # bronze → silver (no network)
uv run composer-ingest dedupe-persons             # link duplicate people
uv run composer-ingest promote                    # silver → gold

uv run composer-ingest stats                      # what's in silver
uv run composer-ingest claims "Beethoven, Ludwig van" --kind person
```

The full command list is in [`apps/cli`](apps/cli/README.md). To browse the
data, run the [consumer API](apps/consumer-api/README.md) and the
[frontend](apps/frontend/README.md); to run the pipeline from a browser, the
[admin API](apps/admin-api/README.md) and the [dashboard](apps/dashboard/README.md).

Data lands in SQLite by default. Postgres is fully supported for silver and,
on a server of its own, for gold:

```sh
uv sync --extra postgres
docker compose up -d postgres postgres-gold
export DATABASE_URL="postgresql+psycopg://composers:composers@localhost:5433/composers"
export GOLD_DATABASE_URL="postgresql+psycopg://composers:composers@localhost:5434/composers_gold"
uv run alembic upgrade head          # creates the silver schema on a fresh database
```

Every setting is listed in [`composer-config`](packages/composer-config/README.md);
how silver's schema is migrated is in [`composer-models`](packages/composer-models/README.md#schema-and-migrations).

## Sources

`imslp`, `imslp_works`, `imslp_recordings`, `wikidata`, `wikidata_works`, `wikidata_places`,
`wikidata_genres`, `wikidata_ensembles`, `wikidata_catalogue`, `openopus`,
`classicalmusiconline`, `classicfm`, `concertgebouw_archive`, `rco`, `nyphil`,
`berlinphil`, `wienerphil`, `laphil`, `roh`, `decca`, `harmoniamundi`, `boosey`,
`baerenreiter` and `henle`. What each one reads, its refresh cadence, per-source
notes, and how to add a source are in
[`composer-scrapers`](packages/composer-scrapers/README.md).

## Layout

A [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/) split
by data tier, sharing one lockfile. Libraries under `packages/` depend only on
the tiers below them.

| Member | What it is |
| --- | --- |
| [`composer-config`](packages/composer-config/README.md) | the settings object every member reads |
| [`composer-schema`](packages/composer-schema/README.md) | source contracts: document types, the `SourceAdapter` interface, the ensemble guard, instrumentation parsing |
| [`composer-models`](packages/composer-models/README.md) | the silver/gold ORM schema, entity identity, and Postgres migrations |
| [`composer-http`](packages/composer-http/README.md) | polite, retrying HTTP, the `SourceSession`, and the page mirror |
| [`composer-bronze`](packages/composer-bronze/README.md) | the raw NDJSON bucket and the fetch workflow |
| [`composer-scrapers`](packages/composer-scrapers/README.md) | the per-source adapters and `REGISTRY` |
| [`composer-warehouse`](packages/composer-warehouse/README.md) | silver: ingest, work matching, person dedupe, concerts and recordings, `rebuild-silver` |
| [`composer-gold`](packages/composer-gold/README.md) | promotion into gold, and the Kumu export |
| [`composer-graph`](packages/composer-graph/README.md) | loading gold into Neo4j |
| [`consumer-api`](apps/consumer-api/README.md) | the read-only product API (gold) and staging API (silver) |
| [`admin-api`](apps/admin-api/README.md) | the fetch/load/promote orchestration API |
| [`cli`](apps/cli/README.md) | `composer-ingest`, the command-line pipeline |
| [`dashboard`](apps/dashboard/README.md) | the Django UI over the two APIs |
| [`frontend`](apps/frontend/README.md) | the public Astro site over the gold API |
| [`tests/e2e`](tests/e2e/README.md) | the end-to-end suite over synthetic sources |

## Development

```sh
uv sync --locked
uv run --directory packages/composer-warehouse pytest   # one member's tests
uv run pyright && uv run ruff check && uv run ruff format --check
```

Each member owns its tests and pytest config, and each README's **Tests**
section says what they cover. [`CLAUDE.md`](CLAUDE.md) has everything CI runs
(the full test matrix, the Postgres and Neo4j suites, the dependency audit, the
frontend checks) and the commit-message rules: Conventional Commits, enforced on
every commit and on the PR title.
