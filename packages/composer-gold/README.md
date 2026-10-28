# composer-gold

Promotion of silver into **gold**, the curated copy the product API serves, and
the Kumu export of gold.

```sh
uv run composer-ingest promote        # silver → gold (full rebuild, atomic swap)
```

## Promotion rules

Promotion applies three curation rules:

1. **Evidence** — people and ensembles are dropped unless they are *credited*
   (a participant on at least the configured number of concerts or recordings)
   or composed a work some source mentioned.
2. **Collapse** — duplicate person entities (linked by `dedupe-persons`) and
   ensemble spellings (linked to their Wikidata anchor by `anchor-ensembles`)
   are collapsed into their canonical row, with claims, records, works,
   mentions and concert participants re-pointed.
3. **Prune** — entities left unreferenced are dropped. The walk from kept
   people keeps every claim between the non-person entities it reaches, so a
   genre's `subclass_of` chain survives with it, and a label-only genre linked
   to its QID entity collapses onto that one like a duplicate person. A kept
   ensemble seeds the walk too: its `based_in` city and `part_of` groups are
   kept with it.

Being *listed* by a source is deliberately not evidence. Archives publish full
artist and ensemble indexes, and taking those at face value is what filled gold
with soloists and orchestras that appear on no programme; the concert and
recording tables are the only thing rule 1 believes.

Silver is never modified by promotion, so it is repeatable at any time. Status
and stats land in `gold.db.manifest.json` (on Postgres, a row in
`composer_meta.build_manifest`). Concert and recording tables are derived in
silver ([`composer-warehouse`](../composer-warehouse/README.md#concerts-and-recordings))
and copied across; `composer_programmes` is recomputed in gold instead, after
mentions are re-pointed at canonical persons, so a composer's spellings count
as one.

Every rule can be switched off (CLI `--no-drop-unevidenced-persons`,
`--no-collapse-duplicates`, `--no-prune-unreferenced`; the same toggles appear
in the dashboard's promote form and in the `POST /admin/v1/promote` body), and
`--gold-url` writes the gold database elsewhere. Rule 1's
concert/recording/composer/sitelink thresholds live in
[`rule1_config.json`](rule1_config.json) (CLI `--rule1-config PATH`) rather than
CLI flags, so they can be tuned by editing that file or through the admin API's
`GET`/`PUT /admin/v1/rule1-config` without touching code. In code the knobs
travel as a single `PromoteConfig` passed to `promote()`.

## Where gold lives

`GOLD_DATABASE_URL` (default `sqlite:///gold.db`, a single file). To serve gold
from Postgres instead, give it a server of its own:

```sh
docker compose up -d postgres-gold
export GOLD_DATABASE_URL="postgresql+psycopg://composers:composers@localhost:5434/composers_gold"
```

Like silver, it lives in a dedicated schema (`GOLD_SCHEMA`, default `gold`) that
`promote` swaps in by rename, and the gold API picks up each new build without a
restart. `promote` reads silver through a normal session, so any combination of
backends works. While it reads a Postgres silver it holds off `rebuild-silver`,
and the other way round.

## Mapping gold in Kumu

`export-kumu` writes a [Kumu](https://kumu.io) blueprint — the
`{"elements": [...], "connections": [...]}` JSON its import accepts — that you
drag onto a map's canvas (or host and point Kumu at as a remote link):

```sh
uv run composer-ingest export-kumu                      # → kumu.json
uv run composer-ingest export-kumu --limit 1500         # a wider slice
uv run composer-ingest export-kumu --min-weight 3       # only well-evidenced pairings
uv run composer-ingest export-kumu --no-claims -o performances.json
```

Gold is far too large to hand Kumu whole, so the export is a *slice*: the
`--limit` most-credited performers and ensembles, the composers whose music they
programmed, and the biographical context hanging off both. Two kinds of edge go
in — **performances** (performer → composer, derived by walking a concert's or
recording's participants and its programme back to each work's composer, and
weighted by how many performances back the pairing) and **claims** (the
object-valued rows of `claims`: profession, birthplace, citizenship, genre,
period, teacher). Literal claims like `born_on` and `program_count` are not
edges; they ride along as element fields, which is where Kumu wants them for
styling and filtering. Roles become tags, so one person can be a composer *and*
a conductor without the map having to choose. Elements left with no connection
are dropped.

The defaults produce roughly a thousand elements, which Kumu opens comfortably;
`--limit 0` exports every performer and is a good deal more than it enjoys.

## Tests

```sh
uv run --directory packages/composer-gold pytest --rootdir . --cov=composer_gold
```

Silver is the in-memory SQLite fixture from `composer_warehouse.testing`;
`test_promote_postgres.py` is skipped unless `COMPOSER_TEST_POSTGRES_URL` is set.

| File | Covers |
| --- | --- |
| `test_promote.py` | The three rules together and each one switched off; rule 1's concert, recording, composer and sitelink thresholds; ensembles needing their own credit; an anchored ensemble collapsing into one with its type, home city and its spellings' records; re-pointing claims, mentions and concert participants to canonicals; copying derived concerts and recordings; recomputing a composer's programme counts across their spellings; a work's premiere date, place and source surviving promotion; work identifiers and links copied as they are; keeping work claims and instrumentation reached through a composer; claims between entities rule 3 already kept (a place's `in_country`, a genre's `subclass_of` between two genres one person has); a genre's `subclass_of` chain surviving, and a label-only genre collapsing onto its QID entity; the manifest and re-running. |
| `test_promote_postgres.py` | Postgres only: the gold schema swap and its manifest, readers following the swap, usable sequences after promotion, and refusing to read silver mid-rebuild. |
| `test_rule1_config.py` | Loading and writing `rule1_config.json`: round-trips, defaults for missing fields, and falling back on a missing or malformed file. |
| `test_kumu.py` | The Kumu blueprint: performer → composer connections, claims as connections and literals as fields, `--min-weight`, `--limit`, switching claims or performances off, and a reproducible export. |
