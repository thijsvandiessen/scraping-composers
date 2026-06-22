# composer-ingest

Ingests classical composer data from IMSLP, Wikidata, Concertgebouw,
NY Phil, and Berlin Phil into a database, with full provenance: every record
knows which source it came from, when it was first and last seen, and which
ingest run produced it.

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

# link near-duplicate person entities ("Beethoven" vs "Beethoven, Ludwig van")
uv run composer-ingest dedupe-persons
uv run composer-ingest person-review                    # pairs the matcher wasn't sure about
uv run composer-ingest person-review --accept 7         # confirm a duplicate link
uv run composer-ingest person-review --reject 7         # reject a proposed link
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
(`src/composer_ingest/etl/works/`):

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
(`src/composer_ingest/etl/persons/`) closes the gap **non-destructively**: it parses
each person name (surname / given / initials / particles), groups by surname,
and scores pairs with a few heuristics — given-name compatibility plus
birth-year corroboration (a conflicting `born_on` year rules a pair out; a
matching one confirms it). High-confidence pairs set the duplicate's
`Entity.canonical_entity_id` to the fuller name; ambiguous ones land in
**`person_matches`** for `person-review`. Nothing is deleted and ids stay
stable, so the pass is re-runnable as the heuristics grow (phonetic matching,
nickname maps, external ids, …).

## Adding a source

Create a package `src/composer_ingest/scraper/sources/<name>/` and subclass
`SourceAdapter` from `composer_ingest.scraper.sources`:

```python
from datetime import UTC, datetime
from collections.abc import Iterator
from composer_ingest.scraper.sources import (
    EntityDocument, SourceAdapter, SourceClaim, WorkMentionDocument,
)

class MyAdapter(SourceAdapter):
    name = "mysource"
    base_url = "https://example.com"

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        ingested_at = datetime.now(UTC)
        for row in _fetch_data(max_pages):
            yield EntityDocument(
                id=row["id"],
                url=row.get("url"),
                source_name=self.name,
                ingested_at=ingested_at,
                name=row["name"],
                claims=(SourceClaim("has_profession", "profession", row["role"]),),
            )
```

Every document inherits the `ScrapedDocument` base: `id` (source-local identifier),
`url`, `source_name`, and `ingested_at` (UTC timestamp set at fetch time). Use
`EntityDocument` for named entities (people, places, …) and `WorkMentionDocument`
for concert-programme entries (a `(composer, title)` pair). Attach typed assertions
to an entity as `SourceClaim`s in `EntityDocument.claims`.

Keep HTTP/API access in `fetch.py` and parsing in one module per view; put the
public `fetch()` orchestration in `__init__.py`. Then add an instance to `REGISTRY`
in `scraper/sources/__init__.py`.

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
