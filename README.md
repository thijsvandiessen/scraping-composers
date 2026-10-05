# composer-ingest

Ingests classical composer, work, concert and recording data from 19 sources (see
[Sources](#sources)) plus any site the generic crawler is pointed at, into a
database, with full provenance: every record knows which source it came from, when
it was first and last seen, and which ingest run produced it.

## Sources

Each source is an adapter in `packages/composer-scrapers/src/composer_scrapers/`,
registered in `REGISTRY` under the name `composer-ingest fetch` takes.

| Source | What it reads | Notes |
| --- | --- | --- |
| `imslp` | IMSLP people API | [imslp](docs/sources/imslp.md) |
| `imslp_works` | IMSLP work catalogue — all of it | [imslp](docs/sources/imslp.md) |
| `imslp_recordings` | IMSLP's commercial recordings of the catalogue | [imslp](docs/sources/imslp.md) |
| `wikidata` | every Wikidata item with occupation composer (Wikidata SPARQL) | |
| `openopus` | Open Opus work catalogue | |
| `classicalmusiconline` | classical-music-online.net composer catalogue and works | |
| `classicfm` | composer and artist name lists from classicfm.com | |
| `concertgebouw_archive` | Concertgebouworkest concert archive | |
| `rco` | Royal Concertgebouw Orchestra live calendar | |
| `nyphil` | New York Philharmonic performance history (Kaggle dataset) | |
| `berlinphil` | Berliner Philharmoniker Digital Concert Hall archive | |
| `wienerphil` | Vienna Philharmonic concert archive | |
| `laphil` | Los Angeles Philharmonic concert programmes | |
| `roh` | Royal Opera House Collections performance database | [roh](docs/sources/roh.md) |
| `decca` | Decca Classics recorded catalogue | |
| `harmoniamundi` | harmonia mundi label catalogue | [harmoniamundi](docs/sources/harmoniamundi.md) |
| `boosey` | Boosey & Hawkes work catalogue | [boosey](docs/sources/boosey.md) |
| `baerenreiter` | Bärenreiter editions and instrumentation | [baerenreiter](docs/sources/baerenreiter.md) |
| `henle` | G. Henle Urtext editions and difficulty | [henle](docs/sources/henle.md) |

Sites without an adapter go through the generic crawler (`composer-ingest crawl`)
and LLM extraction; how its `claims` kind turns publisher catalogues, scoring and
orchestral shorthand into claims is in [docs/extract-claims.md](docs/extract-claims.md).

## Usage

```sh
uv sync

# the generic crawler (composer-ingest crawl) renders pages with crawl4ai in a
# headless browser; install its Chromium once after syncing
uv run crawl4ai-setup

# scrapers identify themselves (User-Agent) to the sites they crawl; a
# reachable contact email is required before fetching
export SCRAPER_CONTACT_EMAIL="you@example.com"

# fetch everything from IMSLP (~55k people, ~55 pages, a few minutes) to the
# bucket, then load the snapshot into the database (no network)
uv run composer-ingest fetch imslp
uv run composer-ingest process imslp

# quick test run
uv run composer-ingest fetch imslp --max-pages 1

# classical-music-online.net is a two-level crawl: 26 alphabet index pages give
# ~11.6k composers (name, life years, country), then every composer's own page
# is fetched for its works. That is ~11.6k requests — a couple of hours,
# unattended; --max-pages caps the composer pages for a smoke run. Processing
# the resulting snapshot needs no network.
uv run composer-ingest fetch classicalmusiconline --max-pages 5
uv run composer-ingest process classicalmusiconline

# extract concerts + performers from crawled pages with a local Ollama model:
# crawl a site, run the model over each page's markdown (stored at crawl time),
# then process the extracted docs like any other snapshot. Needs Ollama running
# (e.g. `ollama pull qwen2.5`); set OLLAMA_MODEL/OLLAMA_BASE_URL to override.
# The same crawl → extract steps are buttons on the dashboard's Crawls page.
uv run composer-ingest crawl lso
uv run composer-ingest extract lso                       # → work-mention + person docs; prints run_id
uv run composer-ingest process lso --run-id <run_id>     # load the extract snapshot
uv run composer-ingest derive-concerts                   # group the mentions into concerts

# re-extracting a crawl only pays for pages whose text actually changed: model
# answers are cached (see "Not analysing the same page twice" below)
uv run composer-ingest extract lso --no-cache            # force every page back through the model

# extract-all: LLM-extract every loadable crawl snapshot of every crawl-config
# source in one call — every past crawl run, not just the latest — instead of
# looping `extract <config> --crawl-run-id <id>` by hand. Best-effort: one run
# failing doesn't stop the rest of the batch, and it prints which (source, run)
# pairs failed. Accepts the same --provider/--model/--no-cache/--no-ledger flags
# as `extract`, applied to every run.
uv run composer-ingest extract-all

# crawl and extract are slow and unattended, so they narrate themselves on stderr:
# discovery, a periodic page count, and what each run dropped. DEBUG adds a line
# per crawled page, per markdown chunk and per model call (with its latency and
# token counts). Global flags come before the subcommand.
uv run composer-ingest -v crawl lso              # DEBUG, crawl4ai and ollama included
uv run composer-ingest --log-level warning run lso
# LOG_LEVEL in .env sets the default for the CLI and the admin API alike

# inspect the dataset and the collection log
uv run composer-ingest stats
uv run composer-ingest runs

# inspect one entity's claims and which source asserts each — every claim
# keeps its source and the raw record it came from, so you can compare
# disagreeing facts and decide which to trust
uv run composer-ingest claims "Beethoven, Ludwig van" --kind person
uv run composer-ingest claims "Beethoven, Ludwig van" --predicate born_on

# inspect resolved works (by composer or title) and the titles they were seen under
uv run composer-ingest works "Beethoven"

# work mentions the matcher wasn't confident about, and how to resolve them
uv run composer-ingest review
uv run composer-ingest review --accept 42 <work-uuid>   # match a mention to a work
uv run composer-ingest review --new 42                  # create a new work from a mention
uv run composer-ingest rematch                          # re-run matching after tuning

# link near-duplicate person entities ("Beethoven" vs "Beethoven, Ludwig van")
uv run composer-ingest dedupe-persons
uv run composer-ingest person-review                    # pairs the matcher wasn't sure about
uv run composer-ingest person-review --accept 7         # confirm a duplicate link
uv run composer-ingest person-review --reject 7         # reject a proposed link

# group performance mentions into concerts (also runs before every promote)
uv run composer-ingest derive-concerts
```

Data lands in `composers.db` (SQLite) by default. **Postgres is fully
supported for silver** — including `rebuild-silver`, whose atomic swap has a
real Postgres equivalent (see [Schema and migrations](#schema-and-migrations)):

```sh
uv sync --extra postgres
export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/composers"
uv run alembic upgrade head          # creates the schema on a fresh database
```

A local one for development comes with the repo:

```sh
docker compose up -d postgres
export DATABASE_URL="postgresql+psycopg://composers:composers@localhost:5433/composers"
```

Silver lives in its own schema (`SILVER_SCHEMA`, default `silver`), never in
`public`, because `rebuild-silver` replaces that schema wholesale. The role
needs `CREATE` on the database — `GRANT CREATE ON DATABASE composers TO app` —
and nothing more; no superuser, no ownership of `public`.

Gold is configured separately: `GOLD_DATABASE_URL` (default
`sqlite:///gold.db`, a single file). To serve gold from Postgres instead, give it
a server of its own:

```sh
docker compose up -d postgres-gold
export GOLD_DATABASE_URL="postgresql+psycopg://composers:composers@localhost:5434/composers_gold"
```

Like silver, it lives in a dedicated schema (`GOLD_SCHEMA`, default `gold`) that
`promote` swaps in by rename, and the gold API picks up each new build without a
restart. `promote` reads silver through a normal session, so any combination of
backends works. While it reads a Postgres silver it holds off `rebuild-silver`,
and the other way round.

## Bronze, silver & gold

Data lives in three tiers. **Bronze** is the raw NDJSON bucket (`raw-data/`,
written by `fetch`): everything every source said, verbatim, as complete
documents — the only tier holding the full fetch output, and the only way data
enters the pipeline. **Silver** (`composers.db`) is the staging database built
from bronze by `process`: raw records with provenance plus the interpretation
passes over them — entity resolution, claims, work matching, person dedupe,
and human review decisions. **Gold** (`gold.db`) is the curated copy rebuilt
from silver by the promote step:

```sh
uv run composer-ingest promote        # silver → gold (full rebuild, atomic swap)
```

Promotion applies the curation rules: people and ensembles are dropped unless
they are *credited* — a participant on at least the configured number of
concerts or recordings — or composed a work some source mentioned; duplicate person entities
(linked by `dedupe-persons`) are collapsed into their canonical row with claims,
works, and mentions re-pointed; entities left unreferenced are pruned. Silver is
never modified by promotion, so it is repeatable at any time; status and
stats land in `gold.db.manifest.json` (on Postgres, a row in
`composer_meta.build_manifest`).

Being *listed* by a source is deliberately not evidence. Archives publish full
artist and ensemble indexes, and taking those at face value is what filled gold
with soloists and orchestras that appear on no programme; the concert and
recording tables are the only thing rule 1 believes.

Each run is configurable: every rule can be switched off (CLI
`--no-drop-unevidenced-persons`, `--no-collapse-duplicates`,
`--no-prune-unreferenced`; the same toggles appear in the dashboard's promote
form and in the `POST /admin/v1/promote` body), and `--gold-url` writes the
gold database elsewhere. Rule 1's concert/recording/composer/sitelink
thresholds live in `rule1_config.json` (CLI `--rule1-config PATH`, default the
repo's `packages/composer-gold/rule1_config.json`) rather than CLI flags, so
they can be tuned by editing that file or through the admin API's
`GET`/`PUT /admin/v1/rule1-config` without touching code. In code the knobs
travel as a single `PromoteConfig` passed to `promote()`.

### Mapping gold in Kumu

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

### Not analysing the same page twice

A crawl writes a whole new snapshot every run, but most of a site is the same text
as last time. Re-extracting it used to re-ask the model about every page — hours of
GPU time to recompute answers it had already given. Model answers are therefore
cached in `extract-cache.db` (`EXTRACT_CACHE_PATH`), so an `extract` only pays for
pages whose text actually changed.

The key is a SHA-256 of the **whole request**, not of the page: the model, the
system prompt, the user prompt (which folds in the page markdown *and* its
title/description metadata), the JSON schema demanded of the answer, and the
generation options. Anything that could change the answer changes the key — so
editing the prompts in `composer_extract/prompt.py`, or pointing `OLLAMA_MODEL` at
a different model, re-asks every page by itself. There is no version constant to
remember to bump, which is the failure mode that makes a prompt improvement look
like it did nothing.

Only answers that validate are stored, so the truncated JSON that
`composer_extract/resilience.py` exists to survive is never cached; empty answers
*are* cached, since "this page has no concert on it" is the common case and would
otherwise be recomputed forever. The cache is an optimization and never a reason to
fail — an unreachable or damaged database degrades to "not cached" and is logged.

```sh
uv run composer-ingest extract lso            # prints e.g. "412 cached, 38 asked (92% of calls saved)"
uv run composer-ingest extract lso --no-cache # bypass it for one run
rm extract-cache.db                           # the hard reset
sqlite3 extract-cache.db "select model, schema_name, count(*) from extraction_cache group by 1, 2"
```

Crawling cannot skip the fetch itself: the markdown only exists once crawl4ai has
rendered the page in its headless browser, and crawl4ai's request headers are
browser-global, so there is no seam for a per-URL `If-None-Match`. (ETags are
captured in each record's `headers` where a site sends them, but coverage across
the configured sources is too sparse to build on.) What the crawl does instead is
stamp every page with `content_sha256` and compare it against the previous
snapshot, so the closing tally reports how much of a re-crawl was worth doing:

```
crawl 'lso' finished in 812s: 450 pages, 0 skipped, 2 without markdown, 412 unchanged
```

### Rebuilding silver

Interpretation (entity resolution, work matching) is applied when a record is
first loaded — improving the heuristics doesn't fix rows already in the
database. `rebuild-silver` closes that gap by replaying the bucket into a fresh
database with the current code, then re-running dedupe and concert derivation:

```sh
uv run composer-ingest rebuild-silver   # bucket → composers.db (atomic swap)
```

What gets replayed is **every source the bucket has data for, and every one of
its loadable document snapshots** — the same union `process` loads, not just
the newest run. The source list comes from the bucket rather than the scraper
registry, so the crawl-config sources count too: they have no adapter, but the
`extract` step writes their LLM-derived documents in the same format a scraper
writes, and they are the only source of recordings. A source that has been
crawled but not yet extracted has no document snapshot and is skipped.

Human review decisions survive the rebuild: accepted/rejected person pairs
carry over directly (entity ids are deterministic), and manual work matches
are re-resolved by the work's composer + title (created again if matching no
longer produces the work). The run log (`ingest_runs`) starts fresh, and
**work ids may change** across rebuilds — only entity ids are stable.

The swap is atomic on both backends. On SQLite it is a file replace, and
status lands in `composers.db.manifest.json` beside the database. On Postgres
the rebuild builds into a staging schema and promotes it with `ALTER SCHEMA …
RENAME` inside one transaction — Postgres DDL is transactional, so this is
exactly as indivisible — and status lands in a `composer_meta.build_manifest`
table, which sits outside everything the swap touches. Readers follow the swap
without a restart. Budget roughly twice silver's size on disk: the demoted
schema is dropped just after the rename, not before it.

**Concerts are derived in silver** from the mentions' raw performance
context (`composer-ingest derive-concerts`, also run automatically before
every promote): mentions are grouped into concerts per source (berlinphil by
its concert id, nyphil by program + date, concertgebouw by date + city, rco
and wienerphil by their own concert ids, dates normalized to ISO) with season
and event type; conductors, soloists (with their instrument/voice) _and the
orchestra_ are resolved to entities by normalized name — against the person
**and** ensemble entities whatever the credit's role, because a credit's slot
says how the source filed the name, not what the name is (choirs turn up among
the soloists); and each concert keeps its programme. Promotion copies the concert
tables into gold, collapsing participant links to canonical entities. That
powers the concert browser, per-person concert lists, and concert-count
sorting in both APIs.

## Schema and migrations

The two backends answer "how does an existing database get a schema change?"
differently, and both answers are deliberate.

**SQLite has no migrations, and doesn't need any.** The schema comes from
`Base.metadata.create_all`, and a schema change means running `rebuild-silver`:
bronze holds the full documents, so nothing in silver is irreplaceable. That is
cheaper than writing migrations for a tier that is rebuilt anyway — and SQLite
has no real `ALTER`, so every non-trivial migration would be a table-copy
recipe.

**Postgres uses Alembic**, because a hosted database is not disposable the same
way. `uv run alembic upgrade head` is the whole bootstrap for an empty
database — it creates the schema itself, not just the tables in it.
`rebuild-silver` still builds its staging schema from the models directly and
stamps it at head, so a rebuilt database looks migrated rather than hand-made.

What keeps the two from drifting apart is a test, not discipline:
`test_head_matches_the_models` asserts that `alembic upgrade head` produces
exactly what `create_all` would. Add a column to a model without writing a
revision and it fails.

**Always autogenerate against Postgres.** Types are rendered from the models,
so one revision serves both dialects (`sa.Uuid()` becomes `UUID` on Postgres
and `CHAR(32)` on SQLite) — but reflecting a *SQLite* database compares
`CHAR(32)` against the model's `Uuid` and proposes a bogus `alter_column`.

```sh
docker compose up -d postgres
DATABASE_URL=postgresql+psycopg://composers:composers@localhost:5433/composers \
  uv run alembic revision --autogenerate -m "add work.canonical_work_id"
uv run ruff format packages/composer-models/src/composer_models/migrations/versions
```

Moving an existing SQLite silver to Postgres is a **rebuild from bronze**, not
a dump and restore: `DATABASE_URL=postgresql+psycopg://… uv run composer-ingest
rebuild-silver`. Entity ids are deterministic, so they survive; work ids and
the run log do not, exactly as with any other rebuild.

## Consumer API (read the dataset)

Two read-only FastAPI apps with identical routes — gold is the product API,
silver serves the staging data for inspection:

```sh
uv sync
uv run uvicorn composer_api:gold_app --port 8000     # curated (default)
uv run uvicorn composer_api:silver_app --port 8003   # staging
# open http://localhost:8000/docs
```

- `GET /v1/composers` / `/v1/soloists` / `/v1/conductors` (+ `/{id}`) — searchable, paginated people;
  `?sort=concerts` orders by concert count (each item carries `concert_count`)
- `GET /v1/people/{id}/concerts` — the concerts a person took part in, with dates, venues, and works
- `GET /v1/concerts?q=&source=` / `GET /v1/concerts/{id}` — browse concerts (search by venue or
  participant); the detail carries all musicians (role, discipline, entity link) and the programme
- `GET /v1/stats` — dataset counts: entities per kind, records per source, works, mention statuses
- `GET /v1/entities?q=&kind=` / `GET /v1/entities/{id}` — search every entity kind; the detail shows
  each claim with its source plus the (capped) list of claims pointing at the entity
- `GET /v1/works?q=` — resolved works by title or composer, with aliases and mention counts
- `GET /v1/mentions?status=` — work mentions with the matcher's decision;
  `status=needs_review` is the review queue, each entry with its best candidate work

Every claim in a detail response carries its provenance: `source` (the scraper
name), `source_url` (the exact page the fact came from, e.g.
`https://www.wikidata.org/wiki/Q255`, falling back to the source homepage), and
`source_external_id` (the source's own id for the person, e.g. the Wikidata QID).

### Frontend (Astro)

A public-facing web UI (`apps/frontend/`) over the gold consumer API: a
searchable composer list plus a detail page where every fact shows the source
it came from — Wikidata-backed facts link to the exact item page (and show the
QID). It is a pure HTTP client of the gold API, like the dashboard, and renders
server-side so no CORS setup is needed. Data appears once `composer-ingest
promote` has produced `gold.db`.

```sh
cd apps/frontend
npm install
GOLD_API_URL=http://localhost:8000 npm run dev   # http://localhost:4321
npm run build && npm start                        # production build
```

## Admin API (manage & run scrapers)

A small FastAPI app for triggering scrapes from a browser instead of the CLI.
It is **separate from the read-only consumer API** so it can be deployed in its
own, locked-down environment.

```sh
uv sync
export ADMIN_API_KEY=dev-key
uv run uvicorn composer_admin:admin_app --port 8001
# open http://localhost:8001/docs and click "Try it out"
```

Each scraper carries a **refresh cadence** declared on its `SourceAdapter`:
`static` for the archives (wienerphil, roh, concertgebouw_archive, berlinphil,
nyphil — scraped once, re-run on demand to pick up what was added) and `yearly`
for everything else. The cadence also sets how long the scraper's page mirror
serves a page before refetching it (see [Adding a source](#adding-a-source)).
The API surfaces which scrapers are _due_ so you can refresh by staleness rather
than by data type:

The two ingest phases are separate endpoints, mirroring the CLI's `fetch` and
`process`:

- `GET  /admin/v1/scrapers` — every scraper with its cadence, last snapshot, and `due` flag
- `POST /admin/v1/scrapers/{name}/fetch` — fetch one source to the bucket (background, returns the `snapshot_id`)
- `POST /admin/v1/scrapers/fetch-due` — fetch every scraper whose raw data is stale
- `GET  /admin/v1/snapshots` — every raw snapshot in the bucket with its status, record count, and size
- `POST /admin/v1/snapshots/{source}/{snapshot_id}/process` — load a snapshot into the database (background, returns a `run_id`)
- `POST /admin/v1/snapshots/{source}/{snapshot_id}/abandon` — give up on a snapshot stuck on `running`
- `GET  /admin/v1/runs` / `GET /admin/v1/runs/{run_id}` — load history and status

A fetch or crawl that is killed outright never finalizes its manifest, so it
stays `running` for good: the dashboard keeps showing it as live and no new run
for that source can start. **Abandon** is the way out — it marks the snapshot
failed and corrects its record count to what is on disk, deleting nothing (a
crawl streams its pages to the bucket as it goes, so an interrupted one keeps
everything it had fetched). The Crawls page grows an **Abandon** button on any
row whose last snapshot is `running`.

Fetch status lives in the snapshot's manifest on disk; loads are recorded in
`ingest_runs` (the same log the CLI `runs` command shows). `ADMIN_API_KEY` is
**required**: every admin request must carry a matching `X-Admin-Key` header,
and while the variable is unset the API fails closed and refuses all requests
with a 503 — so set it for local use too (any value, e.g. `dev-key`). The
fetch endpoints run the scrapers, so the admin API process also needs
`SCRAPER_CONTACT_EMAIL` set (see Usage).

### Dashboard (Django + Unfold)

A web UI (`apps/dashboard/`) on top of the admin API, styled with
[Unfold](https://unfoldadmin.com/) and living behind the Django admin login,
with one page per ingest phase:

- **Scrapers** — every scraper with its cadence, due/fresh state, and last
  snapshot; a **Fetch** button each and a **Fetch all due** button. Fetching
  only writes raw data to the bucket.
- **Load** — every raw snapshot in the bucket (status, record count, size)
  with a **Load into DB** button on complete ones, plus the recent-loads log.
- **Promote** — gold status (last rebuild, curation stats) and the button to
  rebuild gold from silver.
- **Data (silver)** (Overview / Entities / Works / Review) — inspect the
  staging data: dataset counts, a searchable entity browser (per-kind pages,
  random sampling, claims with per-source provenance, cross-linked entities),
  a searchable works browser, and the work-mention review queue (resolving
  stays on the CLI: `review --accept` / `--new`).
- **Musicians (gold)** (Composers / Soloists / Conductors / Concerts) —
  role-based people pages over the curated gold database with per-person
  concert counts (sortable), and a concert browser whose detail pages show
  each concert's musicians and programme.

Scrapers/Load/Promote pages auto-refresh every 5 seconds while work is in
progress.

```sh
uv sync
export ADMIN_API_KEY=dev-key  # the admin API requires it; the dashboard forwards it
export DASHBOARD_DEBUG=1      # DEBUG defaults off; local dev needs it on (see below)
uv run python apps/dashboard/manage.py migrate            # once: Django's own tables
uv run python apps/dashboard/manage.py createsuperuser    # once: your login
uv run uvicorn composer_api:gold_app --port 8000      # gold API (Musicians pages)
uv run uvicorn composer_admin:admin_app --port 8001   # admin API (scrape/load/promote)
uv run uvicorn composer_api:silver_app --port 8003    # silver API (Data pages)
uv run python apps/dashboard/manage.py runserver 8002             # the dashboard
# open http://localhost:8002 and log in
```

`DEBUG` is off unless `DASHBOARD_DEBUG=1` is set. With `DEBUG` off,
`runserver` does not serve static files, so the Unfold admin CSS breaks —
always set it for local development.

The dashboard never touches any composer database — scrape/load/promote
actions go through the admin API (`ADMIN_API_URL`, default
`http://localhost:8001`; `ADMIN_API_KEY` forwarded as `X-Admin-Key`, so it
must be set in the dashboard's environment too)
and data inspection goes through the consumer APIs (`GOLD_API_URL`, default
`http://localhost:8000`; `SILVER_API_URL`, default `http://localhost:8003`

## Ingest flow

Every source moves through the same pipeline; only the adapter knows the
source's protocol.

1. **Fetch** — the source's `SourceAdapter`
   (`packages/composer-scrapers/src/composer_scrapers/<name>/`) talks to the source's API or
   pages and yields a stream of typed documents: `EntityDocument` for named
   entities (with `SourceClaim`s attached) and `WorkMentionDocument` for
   concert-programme `(composer, title)` entries.
2. **Load** — the ingest loop (`packages/composer-warehouse/src/composer_warehouse/ingestion/`) opens an
   `IngestRun` and consumes the stream:
   - A document already known for this source — matched on
     `(source, external_id)` — always gets its `last_seen` timestamp and run id
     touched; if its content (title/composer/raw, or name/url/raw plus claims)
     differs from what's stored, that's updated too. A changed work mention is
     also re-run through the matching pipeline, so a corrected title doesn't
     keep the decision made for the old one (a mention that re-matches
     elsewhere leaves its previous work behind for the dedupe pass to fold in).
     An unchanged re-sighting costs no extra write beyond the timestamp bump,
     which is what makes re-ingesting idempotent.
   - A new `EntityDocument` is attached to a canonical `Entity` via its
     normalized `(kind, dedup_key)` (created if the label is new) and stored
     verbatim as an `entity_records` row; its `SourceClaim`s, plus a
     `mentioned_in` claim recording where it was found, become `claims` rows.
   - A new `WorkMentionDocument` lands in `raw_work_mentions` and immediately
     runs through the work-matching pipeline described under
     [Works](#works) (auto-match / flag for review / create a new work).

   Existing keys are preloaded up front and commits happen in 1000-record
   batches, so the per-record loop needs no queries. A mid-run failure commits
   what was ingested so far and marks the run `failed` with partial counts.

3. **Record** — the finished run's status, seen/new counts, and timestamps land
   in `ingest_runs` (shown by `composer-ingest runs` and the admin API).

The two phases are deliberately separate commands — source requests are slow,
so scraping never blocks on (or fails with) the database load:

```sh
uv run composer-ingest fetch imslp    # network → ./raw-data/imslp/<run_id>/records.ndjson
uv run composer-ingest process imslp  # disk → DB; latest run by default, --run-id to pick one
```

The bucket (`composer_bronze/bucket.py`; NDJSON per run under `BUCKET_PATH`, default
`./raw-data`) is the only way data enters the database: after an ETL change you
can re-process a snapshot without hitting the source again, and `LocalBucket`
can be swapped for an S3 implementation without touching callers.

Each snapshot carries a `manifest.json` recording the fetch's status
(`running`/`completed`/`failed`), record count, and timestamps, so a crashed
fetch can never be mistaken for a complete snapshot: `process` (and the admin
API) only load complete snapshots when picking "latest" — pass `--run-id`
explicitly to override.

## Data model

Entities connected by claims (the Wikidata pattern), on top of a raw
provenance layer. This is the silver staging schema: it records what sources
say, verbatim, plus the matching passes over it; curation and conflict
resolution happen downstream when data is promoted into gold.

- **`sources`** — where data comes from: one row per [source](#sources) or crawl
  config.
- **`ingest_runs`** — the collection log: one row per ingest, with source,
  timestamps, status, and seen/new counts.
- **`entity_records`** — raw records per source, unique on
  `(source, external_id)`. Stores the original payload as JSON plus
  `first_seen_at`/`last_seen_at` timestamps and run ids. Re-ingesting is idempotent.
- **`entities`** — canonical, deduplicated nodes. `kind` says what a node is:
  `person`, `profession`, `period`, `genre`, `place`, `work`, `ensemble`,
  `publisher`, `instrumentation` (an open set — `kind` is a plain string column,
  and a claim naming a new one creates it).
  Records from different sources link here via `(kind, dedup_key)` — a
  normalized label (see `normalize.py`: name-order flipping, diacritic
  stripping, case/punctuation folding) — so adding a second source later
  attaches to existing entities instead of duplicating them.
  `person` is the one kind ingest does not take on trust: every source that
  credits concert or recording participants mints one entity per credited
  *name* and files it as a person, whether it names a violinist or an
  orchestra. So the label decides at write time — a name carrying an ensemble
  word ("Symphony Orchestra", "Knabenchor", "String Quartet"; see
  `composer_schema.kinds`) is stored as an `ensemble`, which keeps it out of
  the person dedupe pass and out of every `kind='person'` read. The word list
  is deliberately narrow: re-kinding a real person is the worse error, so an
  ensemble whose name says nothing about being one ("Academy of St Martin in
  the Fields") stays a person until the list earns another word.
  Existing rows predating the guard are corrected by `rebuild-silver`, which
  replays the bucket through the current heuristics — entity ids are
  `uuid5(kind + dedup_key)`, so a re-kinded entity gets a new id and no
  in-place update would do.
- **`claims`** — typed edges between entities, e.g.
  `person --has_profession--> profession`, `person --born_in--> place`,
  `person --composed--> work`. The object is either another entity
  (`object_id`) or a literal (`value`, e.g. a date string). Every claim
  carries the source and the raw record it was extracted from, so two sources
  asserting conflicting facts coexist as two claims. IMSLP's people list
  reports nothing beyond names (it mixes composers, performers, editors, and
  ensembles), so it produces no claims; richer sources will.

### Works

Concert programmes name works as free text — "Symphony No. 5 in C minor",
"Sinfonie Nr. 5 c-moll", "Beethoven's Fifth" are all the same piece. Rather than
deduplicating on the exact title (which collides across composers and splits the
same work across spellings), works go through a resolution pipeline
(`packages/composer-warehouse/src/composer_warehouse/works/`):

- **`raw_work_mentions`** — one row per `(composer, title)` a source reported,
  with the full performance context kept in `raw`, idempotent on
  `(source, external_id)`. Each carries the matcher's decision: `match_status`
  (`auto_matched`/`needs_review`/`created`/`manual_matched`), score and method.
- **`works`** — canonical compositions. The pipeline extracts catalogue/opus
  numbers, key, type and number from the title, finds candidate works by the
  same composer, and scores them (a matching `BWV`/`Op.` number is near-certain
  identity; otherwise a `difflib` title comparison sharpened by the extracted
  features). High scores auto-match, middling scores are flagged for `review`,
  low scores create a new work. Work ids are assigned at creation, not derived
  from the title.
- **`work_titles`** — every title a work was seen under (its aliases).

Composers stay `person` entities (deduplicated as above); works reference them
by id. Performance events, richer work metadata and external ids build on this
layer next.

### People deduplication

Exact-key dedup unifies "Beethoven, Ludwig van" ↔ "Ludwig van Beethoven" but
misses surname-only ("Beethoven"), initials ("Bach, J.S." vs "Bach, Johann
Sebastian"), and other variants. The `dedupe-persons` pass
(`packages/composer-warehouse/src/composer_warehouse/persons/`) closes the gap **non-destructively**: it parses
each person name (surname / given / initials / particles), groups by surname,
and scores pairs with a few heuristics — given-name compatibility plus
birth-year corroboration (a conflicting `born_on` year rules a pair out; a
matching one confirms it). High-confidence pairs and human `accepted` reviews
are recorded in **`person_matches`**; ambiguous ones wait there for
`person-review`.

The links themselves are then derived from those pairs as *clusters*, not pair
by pair: `persons/cluster.py` merges the pairs strongest-first into disjoint
groups, picks one canonical per group (the fullest given names, chosen from the
whole membership), and points every member straight at it. So
`Entity.canonical_entity_id` is always one hop to a canonical — no chains, no
cycles — and a `rejected` review becomes a cannot-link the group honours even
when the merge arrives transitively through a third record. Nothing is deleted
and ids stay stable, so the pass is re-runnable as the heuristics grow
(phonetic matching, nickname maps, external ids, …).

## Adding a source

Every source has the same shape. Create a package
`packages/composer-scrapers/src/composer_scrapers/<name>/` with:

- `fetch.py` — URLs and the requests themselves, as functions of a
  `composer_http.SourceSession`. No client construction, no `time.sleep`, no
  cache handling: the session owns all three.
- one parse module per view — pure functions from a fetched body to records.
- `__init__.py` — a subclass of `HttpSourceAdapter` declaring identity, cadence
  and manners, with `scrape(session, max_pages)` gluing the two together.

```python
from collections.abc import Iterator
from datetime import UTC, datetime

from composer_http import SourceSession
from composer_scrapers import EntityDocument, HttpSourceAdapter, RefreshCadence, SourceClaim

from .fetch import fetch_index, fetch_person


class MyAdapter(HttpSourceAdapter[EntityDocument]):
    name = "mysource"
    base_url = "https://example.com"
    cadence = RefreshCadence.YEARLY  # STATIC for an archive of things already given
    request_delay_s = 0.5  # between network requests; mirror hits are free
    follow_redirects = True  # also: timeout_s, headers

    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[EntityDocument]:
        ingested_at = datetime.now(UTC)
        for row in fetch_index(session)[:max_pages]:  # live: how a re-run sees growth
            page = fetch_person(session, row.url)  # mirrored, tolerant of a 404
            ...
            yield EntityDocument(
                id=row.id,
                url=row.url,
                source_name=self.name,
                ingested_at=ingested_at,
                name=row.name,
                claims=(SourceClaim("has_profession", "profession", "composer"),),
            )
```

with `fetch.py` along the lines of:

```python
def fetch_index(session: SourceSession) -> str:
    return session.get_text(INDEX_URL, label="index")  # never mirrored


def fetch_person(session: SourceSession, url: str) -> str | None:
    return session.try_get_text(url, label=url, mirror=True)
```

**The page mirror.** `mirror=True` keeps a fetched page in `PAGE_CACHE_PATH`
(SQLite, gzipped), so a sweep of thousands of detail pages is paid once and an
interrupted run resumes. Two things decide whether a mirrored page is served:

- **its age, against the adapter's cadence** — a `yearly` source refetches once a
  year has passed, which is exactly when it becomes due; a `static` source keeps
  its mirror forever;
- **the request that produced it** — a POSTed query (GraphQL, SPARQL) is mirrored
  through `session.mirrored(...)`/`session.store(...)` with a
  `request_fingerprint` of the query, so editing the query in code is a miss.

Mirror detail pages; never mirror what *enumerates* the source (an index, a
listing, a sitemap) or a re-run would never see new records. Pass `valid=` to
keep error pages served with a 200 out of the mirror. `PAGE_CACHE_ENABLED=false`
bypasses it for a run; deleting the file is the hard reset.

Every document inherits the `ScrapedDocument` base: `id` (source-local identifier),
`url`, `source_name`, and `ingested_at` (UTC timestamp set at fetch time). Use
`EntityDocument` for named entities (people, places, …) and `WorkMentionDocument`
for concert-programme entries (a `(composer, title)` pair) — a source yielding both
subclasses `HttpSourceAdapter[EntityDocument | WorkMentionDocument]`. Attach typed
assertions to an entity as `SourceClaim`s in `EntityDocument.claims`.

Then add an instance to `REGISTRY` in `composer_scrapers/__init__.py`. Test
against `composer_http.testing.mock_session(handler)` rather than the network.

## Development

This repo is a [uv workspace](https://docs.astral.sh/uv/concepts/projects/workspaces/)
split by data tier, sharing one lockfile. `uv sync` installs the whole workspace.

Libraries under `packages/` (each depends only on the tiers below it):

- `composer-config` — the one pydantic-settings `Settings` object every other member reads
- `composer-schema` — source contracts (document types + the `SourceAdapter` interface), zero heavy deps
- `composer-models` — the ORM schema shared by the silver and gold DBs, engine helpers, and the
  dedup keys / seeded entity UUIDs that define entity identity
- `composer-http` — the polite User-Agent (contact identity) and retrying HTTP helpers, shared by
  `composer-scrapers` and `composer-crawler`, plus the `SourceSession` and page mirror the
  scrapers fetch through
- `composer-bronze` — the raw NDJSON bucket and fetch orchestration
- `composer-scrapers` — the per-source adapters and `REGISTRY`
- `composer-crawler` — the generic config-driven crawl4ai crawler, into the same bucket
- `composer-extract` — LLM extraction (local Ollama or hosted Gemini) of concerts, recordings and
  claims from crawled pages
- `composer-warehouse` — the silver staging DB: ingestion and person/work matching
- `composer-gold` — promotion of the staging DB into a curated copy

Apps under `apps/`:

- `consumer-api` — the read-only product API (depends on models + gold only)
- `admin-api` — the scrape/ingest/promote orchestration API (depends on every tier)
- `cli` — the command-line pipeline
- `dashboard` — the Django UI (a pure HTTP client of the two APIs)

```sh
# tests run per member (each owns its pytest config; the Django settings stay
# scoped to the dashboard) — mock sources, in-memory SQLite, no network:
uv run --directory packages/composer-config pytest
uv run --directory packages/composer-schema pytest
uv run --directory packages/composer-models pytest
uv run --directory packages/composer-http pytest
uv run --directory packages/composer-bronze pytest
uv run --directory packages/composer-scrapers pytest
uv run --directory packages/composer-crawler pytest
uv run --directory packages/composer-extract pytest
uv run --directory packages/composer-warehouse pytest
uv run --directory packages/composer-gold pytest
uv run --directory apps/consumer-api pytest
uv run --directory apps/admin-api pytest
uv run --directory apps/cli pytest
uv run --directory apps/dashboard pytest

uv run pyright             # strict type checking (whole workspace)
uv run ruff check          # lint
uv run ruff format --check # formatting
```

Document/adapter test factories live in `composer_schema.testing`; the warehouse
re-exports them alongside its DB fixtures in `composer_warehouse.testing`, which
each member's `tests/conftest.py` loads via `pytest_plugins`.

CI (`.github/workflows/ci.yml`) runs the per-member test matrix alongside the
type, lint and dependency-audit jobs on every pull request to `main`, and again
on the merge commit. Commit messages and PR titles must follow
[Conventional Commits](https://www.conventionalcommits.org/) (`feat: ...`,
`fix: ...`); `.github/workflows/conventional-commits.yml` enforces this on
every pull request.
