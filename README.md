# composer-ingest

Ingests classical composer data from IMSLP (and future sources) into a
database, with full provenance: every record knows which source it came from,
when it was first and last seen, and which ingest run produced it.

## Usage

```sh
uv sync

# fetch everything from IMSLP (~55k people, ~55 pages, a few minutes)
uv run composer-ingest ingest imslp

# quick test run
uv run composer-ingest ingest imslp --max-pages 1

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
```

Data lands in `composers.db` (SQLite) by default. To use Postgres instead:

```sh
uv sync --extra postgres
export DATABASE_URL="postgresql+psycopg://user:pass@host:5432/composers"
```

## Data model

Entities connected by claims (the Wikidata pattern), on top of a raw
provenance layer. This repo is the raw staging layer: it records what sources
say, verbatim; curation and conflict resolution happen downstream when data
moves into the golden research index.

- **`sources`** — where data comes from (`imslp`, `wikidata`, `concertgebouw`,
  `nyphil`, `berlinphil`).
- **`ingest_runs`** — the collection log: one row per ingest, with source,
  timestamps, status, and seen/new counts.
- **`entity_records`** — raw records per source, unique on
  `(source, external_id)`. Stores the original payload as JSON plus
  `first_seen`/`last_seen` timestamps and run ids. Re-ingesting is idempotent.
- **`entities`** — canonical, deduplicated nodes. `kind` says what a node is:
  `person`, `profession`, `period`, `genre`, `place`, `work` (open set).
  Records from different sources link here via `(kind, dedup_key)` — a
  normalized label (see `normalize.py`: name-order flipping, diacritic
  stripping, case/punctuation folding) — so adding a second source later
  attaches to existing entities instead of duplicating them.
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
(`src/composer_ingest/works/`):

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

## Adding a source

Create a package `src/composer_ingest/sources/<name>/` whose `__init__.py`
exposes `NAME`, `BASE_URL`, and `fetch_records(max_pages=None)` yielding
`SourceRecord`s (with optional `SourceClaim`s for what the source asserts about
each entity), then add it to `REGISTRY` in `sources/__init__.py`. Each source is
a package split by responsibility — e.g. `fetch.py`/`query.py` for the HTTP or
API access and one module per view/parser (see `concertgebouw/` and `nyphil/`);
keep the public `fetch_records` orchestration in `__init__.py`.

## Development

```sh
uv run pytest              # tests (mock sources, in-memory SQLite — no network)
uv run mypy                # strict type checking
uv run ruff check          # lint
uv run ruff format --check # formatting
```

CI (`.github/workflows/ci.yml`) runs all four on every pull request to `main`
and again on the merge commit. Commit messages and PR titles must follow
[Conventional Commits](https://www.conventionalcommits.org/) (`feat: ...`,
`fix: ...`); `.github/workflows/conventional-commits.yml` enforces this on
every pull request.

## IMSLP API quirks

The endpoint (`/imslpscripts/API.ISCR.php`) takes its parameters as a single
slash-separated string, returns rows keyed by stringified indices alongside a
`metadata` entry holding the pagination flag, and embeds names in MediaWiki
category titles (`Category:Beethoven, Ludwig van`). `sources/imslp/`
handles all of this, plus retries and a polite request delay.
