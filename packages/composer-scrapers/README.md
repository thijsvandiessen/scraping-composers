# composer-scrapers

The per-source adapters and `REGISTRY`, the name → adapter map that
`composer-ingest fetch <name>` and the admin API look scrapers up in. An adapter
only knows its source's protocol: it fetches and yields typed documents, and
everything after that (bucket, ingest, matching) is shared.

## Sources

| Source | What it reads | Cadence | Notes |
| --- | --- | --- | --- |
| `imslp` | IMSLP people API | yearly | [imslp](docs/imslp.md) |
| `imslp_works` | IMSLP work catalogue — all of it | yearly | [imslp](docs/imslp.md) |
| `imslp_recordings` | IMSLP's commercial recordings of the catalogue | yearly | [imslp](docs/imslp.md) |
| `wikidata` | every Wikidata item with occupation composer (Wikidata SPARQL) | yearly | genres (P136) and movements (P135) are claimed with their QID, which their entities are keyed on |
| `wikidata_places` | every place a Wikidata composer was born, died or held citizenship in, then the places containing those | yearly | place records with `place_type`, `located_in`, `in_country`, coordinates, GeoNames id and ISO 3166 code; see [`composer-warehouse`](../composer-warehouse/README.md#places) |
| `wikidata_works` | every Wikidata work with a date of first performance (P1191) and a composer, with its premiere place (P4647) | yearly | work mentions carrying `raw["premiere"]`, which fills `Work.premiere_date` |
| `wikidata_genres` | every genre and movement the composers are tagged with, and their `subclass of` (P279) ancestors | yearly | `subclass_of` claims make the hierarchy; parents outside music genre / art or cultural movement are dropped, and the walk stops after 15 levels |
| `wikidata_ensembles` | every Wikidata orchestra (Q42998) and choir (Q131186), subclasses included, with their names in the sources' languages, headquarters city, MusicBrainz id and the groups they are part of | yearly | the anchors `anchor-ensembles` folds name-keyed ensembles onto; see below |
| `wikidata_catalogue` | every Wikidata work with a composer and a catalogue code (P528 qualified by P972), with its MusicBrainz work ids (P435), ISWCs (P1827) and the works it is part of (P361) | yearly | work mentions the warehouse matches on their QID and catalogue numbers; P361 becomes a `part_of` work link |
| `openopus` | Open Opus work catalogue | yearly | |
| `classicalmusiconline` | classical-music-online.net composer catalogue and works | yearly | two levels: 26 index pages, then ~11.6k composer pages (a couple of hours); `--max-pages` caps the composer pages |
| `classicfm` | composer and artist name lists from classicfm.com | yearly | |
| `concertgebouw_archive` | Concertgebouworkest concert archive | static | |
| `rco` | Royal Concertgebouw Orchestra live calendar | yearly | |
| `nyphil` | New York Philharmonic performance history (Kaggle dataset) | static | |
| `berlinphil` | Berliner Philharmoniker Digital Concert Hall archive | static | |
| `wienerphil` | Vienna Philharmonic concert archive | static | |
| `laphil` | Los Angeles Philharmonic concert programmes | yearly | |
| `roh` | Royal Opera House Collections performance database | static | [roh](docs/roh.md) |
| `decca` | Decca Classics recorded catalogue | yearly | |
| `harmoniamundi` | harmonia mundi label catalogue | yearly | [harmoniamundi](docs/harmoniamundi.md) |
| `boosey` | Boosey & Hawkes work catalogue | yearly | [boosey](docs/boosey.md) |
| `baerenreiter` | Bärenreiter editions and instrumentation | yearly | [baerenreiter](docs/baerenreiter.md) |
| `henle` | G. Henle Urtext editions and difficulty | yearly | [henle](docs/henle.md) |

The cadence decides when a source is *due* again in the admin API and how long
its page mirror serves a page (see below). `static` is for archives of things
already given: scraped once, re-run on demand.

`wikidata_ensembles` is paged like `wikidata`: one live id query, then a
mirrored detail query and a mirrored multi-valued query per 500 items, the
detail page checked for coverage. Each ensemble record carries an
`ensemble_type` (from its class: choir over orchestra; else from name cues such
as `Quartet` or `Chor`; else `other`), a `based_in` city (the headquarters if it
is a settlement, else the headquarters' P131) pinned to the city's QID, an `also_known_as` per label and alias in
English, `mul`, German, French, Italian, Dutch, Spanish, Czech, Swedish, Danish,
Polish and Hungarian, and `part_of` only towards another ensemble in the
population. Chamber ensembles and opera companies are not population classes
yet: their QIDs still need pinning against live Wikidata.

## Adding a source

Every source has the same shape. Create a package
`src/composer_scrapers/<name>/` with:

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
bypasses it for a run; deleting the file is the hard reset. The mirror itself
lives in [`composer-http`](../composer-http/README.md).

Every document inherits the `ScrapedDocument` base: `id` (source-local identifier),
`url`, `source_name`, and `ingested_at` (UTC timestamp set at fetch time). Use
`EntityDocument` for named entities (people, places, …) and `WorkMentionDocument`
for concert-programme entries (a `(composer, title)` pair) — a source yielding both
subclasses `HttpSourceAdapter[EntityDocument | WorkMentionDocument]`. Attach typed
assertions to an entity as `SourceClaim`s in `EntityDocument.claims`.

Then add an instance to `REGISTRY` in `composer_scrapers/__init__.py`, and give
the source the three test files described below. Test against
`composer_http.testing.mock_session(handler)` rather than the network.

## Tests

```sh
uv run --directory packages/composer-scrapers pytest --rootdir . --cov=composer_scrapers
```

No network: every request goes to a `mock_session` handler or a fixture page.

Most sources are tested in three layers, one file each:

- **parse** (`test_<source>.py`) — the pure functions from a fetched body to
  records: names, dates, claims, what is skipped.
- **adapter** (`test_<source>_adapter.py`) — the documents `scrape()` yields
  over a fake source: which records become entities or work mentions, their
  claims and ids, `max_pages`, and registration in `REGISTRY`.
- **fetch** (`test_<source>_fetch.py`) — the HTTP layer: URLs, pagination,
  retries, and which requests are mirrored.

| Source | parse | adapter | fetch |
| --- | --- | --- | --- |
| `baerenreiter` | `test_baerenreiter.py`, `test_baerenreiter_instrumentation.py` | ✓ | ✓ |
| `berlinphil` | ✓ | — | ✓ |
| `boosey` | ✓ | ✓ | ✓ |
| `classicalmusiconline` | `test_classicalmusiconline.py` covers all three | | |
| `classicfm` | ✓ | ✓ | ✓ |
| `concertgebouw_archive` | `test_concertgebouw.py` | — | `test_concertgebouw_fetch.py` |
| `decca` | ✓ | ✓ | ✓ |
| `harmoniamundi` | ✓ | ✓ | ✓ |
| `henle` | ✓ | ✓ | ✓ |
| `imslp` | `test_imslp.py` covers the paging fetch and records | | |
| `imslp_recordings` | ✓ | ✓ | ✓ |
| `imslp_works` | ✓ | ✓ | ✓ |
| `laphil` | ✓ | ✓ | ✓ |
| `nyphil` | `test_nyphil.py` (aggregation into records and mentions) | — | — |
| `openopus` | `test_openopus.py` covers all three | | |
| `rco` | ✓ | — | `test_rco_fetch.py` (calendar slugs only) |
| `roh` | ✓ | ✓ | ✓ |
| `wienerphil` | `test_wienerphil.py`, `test_wienerphil_details.py` | ✓ | ✓ |
| `wikidata` | `test_wikidata.py` covers all three, including the SPARQL mirror and QID-pinned place claims | | |
| `wikidata_places` | `test_wikidata_places.py` covers all three: type precedence, parent rounds, coordinates | | |
| `wikidata_works` | `test_wikidata_works.py` covers all three | | |
| `wikidata_genres` | `test_wikidata_genres.py` covers all three: seeds, paging, cycles, the class filter, the depth cap, `max_pages`, the mirror | | |
| `wikidata_ensembles` | `test_wikidata_ensembles.py` covers all three, including the mirror | | |
| `wikidata_catalogue` | `test_wikidata_catalogue.py` covers all three | | |

`test_base.py` covers what every source shares: the session carries the
adapter's manners, the mirror's lifetime follows the cadence, and every
`REGISTRY` entry has the same shape.

A — marks a layer with no test file. For `berlinphil`, `nyphil` and `rco` that
means nothing exercises `scrape()` end to end, nor `rco`'s concert-detail
paging; `concertgebouw_archive`'s adapter is covered only through its fetch
tests.
