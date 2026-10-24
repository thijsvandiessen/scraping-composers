# composer-warehouse

The silver staging database: loading bronze snapshots into it, and the
interpretation passes over what was loaded — work matching, person dedupe,
concert and recording derivation — plus `rebuild-silver`, which replays the
whole bucket through the current code.

## Ingest flow

Every source moves through the same pipeline; only the adapter knows the
source's protocol.

1. **Fetch** — the source's `SourceAdapter`
   ([`composer-scrapers`](../composer-scrapers/README.md)) yields a stream of typed
   documents: `EntityDocument` for named entities (with `SourceClaim`s attached)
   and `WorkMentionDocument` for concert-programme `(composer, title)` entries.
   [`composer-bronze`](../composer-bronze/README.md) writes the stream to the bucket.
2. **Load** — the ingest loop (`ingestion/`) opens an `IngestRun` and consumes a
   snapshot:
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
     runs through the work-matching pipeline (see [Works](#works)).

   Existing keys are preloaded up front and commits happen in 1000-record
   batches, so the per-record loop needs no queries. A mid-run failure commits
   what was ingested so far and marks the run `failed` with partial counts.
3. **Record** — the finished run's status, seen/new counts, and timestamps land
   in `ingest_runs` (shown by `composer-ingest runs` and the admin API).

## Works

Concert programmes name works as free text — "Symphony No. 5 in C minor",
"Sinfonie Nr. 5 c-moll", "Beethoven's Fifth" are all the same piece. Rather than
deduplicating on the exact title (which collides across composers and splits the
same work across spellings), works go through a resolution pipeline (`works/`):
it extracts catalogue/opus numbers, key, type and number from the title, finds
candidate works by the same composer, and scores them. Strong keys come first:
a shared Wikidata item, MusicBrainz work or ISWC, then a shared catalogue
number, then a matching opus; a scheme numbered differently on both sides is a
conflict. Otherwise a `difflib` title comparison sharpened by the extracted
features decides. High scores auto-match, middling scores are flagged for
`composer-ingest review`, low scores create a new work. Work ids are assigned at
creation, not derived from the title. The tables are described in
[`composer-models`](../composer-models/README.md#data-model).

**Catalogue numbers** are normalised per composer by
[`composer_schema.catalogues`](../composer-schema/README.md): `K. 550`, `KV 550`
and `K.550` are all Köchel `KV 550` for Mozart (lettered sixth-edition numbers
are `KV6`), `K` is Kirkpatrick for Domenico Scarlatti, Hoboken separators are
unified (`Hob XVI:52`), and a bare `D`/`S`/`B`/`L`/`H` only counts for the
composer who uses that catalogue. Besides the title, `works/sources.py` reads
what a source states elsewhere in the payload: IMSLP's "Opus/Catalogue Number"
(not its internal `ILB` number), and `wikidata_works`/`wikidata_catalogue`'s
item QID, catalogue codes, MusicBrainz ids and ISWCs. Henle and Bärenreiter
`catalogue_number`s are edition numbers and are not read. Every identifier a
mention brings is stored in `work_identifiers` against its work, and the
matcher's candidates carry them (`works.load_candidates`), so a work found by
its IMSLP catalogue field still matches on it in the next run.

**Parts, arrangements and versions** share their parent's number without being
it. A title cue — `(from …)`/`(aus …)`, `arr.`/`Bearb.`/`transcr.`,
`1. Fassung`/`Urfassung`/`revised version` — marks the mention as derived: it
never matches a work of another kind, and its catalogue numbers are kept as the
parent's. `works/links.py` (`link_works`) then rebuilds `work_links`: a derived
work points at the one non-derived work by the same composer holding one of its
parent numbers (`part_of`, `arrangement_of`, `version_of`; method `catalogue`),
and a `wikidata_catalogue` work *part of* (P361) another item points at the work
with that QID (method `wikidata`). It runs in `rebuild-silver` and before every
promote. `composer-ingest work-identity` reports, per source, the share of
works with a catalogue number or external id and how many are linked to a
parent.

## People deduplication

Exact-key dedup unifies "Beethoven, Ludwig van" ↔ "Ludwig van Beethoven" but
misses surname-only ("Beethoven"), initials ("Bach, J.S." vs "Bach, Johann
Sebastian"), and other variants. The `dedupe-persons` pass (`persons/`) closes
the gap **non-destructively**:

- `extract` parses each name into surname, given names, initials and particles;
- `compare` reduces a pair blocked on surname to comparison levels (given
  names, birth year, death year);
- `fellegi_sunter` turns those levels into a match probability, with a
  term-frequency adjustment so a shared rare surname counts for more than a
  common one. The fitted parameters are in `persons/model.json`;
  [`persons/MODEL.md`](src/composer_warehouse/persons/MODEL.md) records how they
  were fitted and how well they score;
- `constraints` turns distinct Wikidata QIDs or MusicBrainz ids into
  cannot-links, unless dates from several sources corroborate the pair.

High-confidence pairs and human `accepted` reviews are recorded in
**`person_matches`**; ambiguous ones wait there for `person-review`. The links
are then derived from those pairs as *clusters*, not pair by pair:
`persons/cluster.py` merges the pairs strongest-first into disjoint groups,
picks one canonical per group (the fullest given names), and points every member
straight at it. So `Entity.canonical_entity_id` is always one hop to a canonical
— no chains, no cycles — and a `rejected` review becomes a cannot-link the group
honours even when the merge arrives transitively. Nothing is deleted and ids
stay stable, so the pass is re-runnable.

`composer-ingest person-train` refits the model from the warehouse, and
`person-eval` scores it against the committed evaluation set in
`tests/data/person_eval_pairs.jsonl.gz`.

## Places

A place's identity is its Wikidata item. The `wikidata` composer records pin
every `born_in` / `died_in` / `citizen_of` claim to the place's QID, and the
`wikidata_places` source records the places themselves under the same QID:
their `place_type` (`settlement`, `region`, `country`, `historical_state`,
`building`), the places containing them (`located_in`, `in_country`), and
`coordinates`, `geonames_id` and `iso_3166_code`. Every claim about a place
lands on its one pinned entity, and two places sharing a name stay two.

A source without QIDs (classicalmusiconline's countries) still creates a bare
place by label. `places.link_places`, run automatically before every promote and
in `rebuild-silver`, points a bare place at the one pinned place with the same
name through `canonical_entity_id`, the link gold follows for duplicate persons
too. A name two pinned places share ("Georgia") is left unlinked.

## Concerts and recordings

**Concerts are derived in silver** from the mentions' raw performance context
(`composer-ingest derive-concerts`, also run automatically before every
promote): mentions are grouped into concerts per source (berlinphil by its
concert id, nyphil by program + date, concertgebouw by date + city, rco and
wienerphil by their own concert ids, dates normalized to ISO) with season and
event type. Conductors, soloists (with their instrument/voice) _and the
orchestra_ are resolved to entities by normalized name — against the person
**and** ensemble entities whatever the credit's role, because a credit's slot
says how the source filed the name, not what the name is (choirs turn up among
the soloists). Each concert keeps its programme.

**Recordings** are the release counterpart (`recordings/`): mentions whose raw
payload is marked `_kind: "recording"` are grouped per source, the page-scoped
groups are folded into one row per release, artists are resolved to person and
ensemble entities, and each recording is linked to its works. Both passes
rebuild their tables from scratch, so they can be re-run after new loads.

Concert and recording ids are **stable**: each is a uuid5 of the kind, the
source's name and the source's own key (`event_uuid` in `composer-models`), not
a row number, so a new concert doesn't move the others and
`/v1/concerts/{id}` links survive every derive and promote. A merged recording
takes the key of its fullest member, so its id moves only if a fuller page for
the same release turns up later.

## Genres and movements

Genres and movements are vocabulary terms, and Wikidata names nearly all of
them, so an entity of either kind with a QID is keyed on `wikidata:QID` rather
than its label (`entity_key` in `composer-models`): a renamed label stays one
entity. The `wikidata` source tags composers by QID, and `wikidata_genres`
supplies each genre's label, English aliases and `subclass_of` claims to its
P279 parents — the hierarchy that lets "dance music" find a composer tagged
only with "waltz".

Some genre entities still carry a label key: a genre a non-Wikidata source
names, and every one an older `wikidata` run minted before the query asked for
the QID (a rebuild replays those runs too). `link_label_genres` (`genres.py`,
`composer-ingest link-genres`) points each at the QID-keyed entity of the same
kind whose label or alias it carries, through `canonical_entity_id`, so gold
collapses them onto one. It starts from scratch each run, so the result
doesn't depend on ingest order; a label two QIDs share links nothing.

`composer-ingest genres` reports how far the hierarchy reaches (`genre_report`):
QID-keyed vs label-only entities, how many have a parent, the top-level QIDs
(`composer_models.genres`) missing from silver, and the **orphans** — entities
with no `subclass_of` path to a top-level genre — ranked by how many persons
they tag.

## Derived state

`refresh_derived` (`derived.py`) runs every pass that recomputes silver state
from silver itself: place linking, genre linking, then concerts, then recordings. It is what
`rebuild-silver`, `composer-ingest promote` and the admin API's Promote call,
so a pass added there reaches all three.

## Rebuilding silver

Interpretation (entity resolution, work matching) is applied when a record is
first loaded — improving the heuristics doesn't fix rows already in the
database. `rebuild-silver` closes that gap by replaying the bucket into a fresh
database with the current code, then re-running dedupe and the derived passes (place and genre linking, concerts, recordings):

```sh
uv run composer-ingest rebuild-silver   # bucket → composers.db (atomic swap)
```

What gets replayed is **every source the bucket has data for, and every one of
its loadable document snapshots** — the same union `process` loads, not just
the newest run. The source list comes from the bucket rather than the scraper
registry, so a source whose adapter has since been removed is still replayed.
That includes any LLM-extracted snapshots the retired crawl → extract pipeline
wrote: delete their directories from the bucket to keep them out. Raw
crawled-page snapshots are never loaded.

Human review decisions survive the rebuild: accepted/rejected person pairs
carry over directly (entity ids are deterministic), and manual work matches
are re-resolved by the work's composer + title (created again if matching no
longer produces the work). The run log (`ingest_runs`) starts fresh, and
**work ids may change** across rebuilds — entity, concert and recording ids are
stable.

The swap is atomic on both backends (`build.py`, `postgres.py`). On SQLite it
is a file replace, and status lands in `composers.db.manifest.json` beside the
database. On Postgres the rebuild builds into a staging schema and promotes it
with `ALTER SCHEMA … RENAME` inside one transaction, and status lands in a
`composer_meta.build_manifest` table, which sits outside everything the swap
touches. Readers follow the swap without a restart. Budget roughly twice
silver's size on disk: the demoted schema is dropped just after the rename, not
before it.

## Tests

```sh
uv run --directory packages/composer-warehouse pytest --rootdir . --cov=composer_warehouse
```

Sources are in-memory fakes and silver is in-memory SQLite. The tests marked
Postgres below are skipped unless `COMPOSER_TEST_POSTGRES_URL` is set (see
`CLAUDE.md`). Shared fixtures (`session`, `ingest_source`, the document
factories) come from `composer_warehouse.testing`.

**Ingest**

| File | Covers |
| --- | --- |
| `test_ingest.py` | Entities, records and claims from a fake source; a second source attaching to the same entity; claim objects as deduplicated entities; QID-pinned claims; `mentioned_in` URLs; 1000-record batch commits; a failing source marking the run failed; ensemble-named credits landing as ensembles. |
| `test_ingest_reingest.py` | Re-sighted entity records: idempotency, content changes, label updates, and the `last_*` timestamps. |
| `test_ingest_premieres.py` | A mention's world premiere carried onto its work: a new work, a later source backfilling a NULL, an earlier date winning across sources and within one run, a later one not, a full date refining a year, a corrected mention, the winning source recorded, and house premieres ignored. |
| `test_ingest_mentions.py` | Work mentions: creating a work and alias, matching an existing work, the same title under two composers, re-ingesting with a corrected title or composer, and mentions kept when a source fails. |

**Works**

| File | Covers |
| --- | --- |
| `test_work_extract.py` | Extracting catalogue numbers (BWV, K., Op.), key, type and number from titles, including German titles; numbers read for the composer (a bare `K` only for Mozart or Scarlatti), structured catalogue fields, and the part/arrangement/version cues that make a parent's number the parent's. |
| `test_work_matching.py` | Scoring candidates and deciding auto-match, review or create; K and KV as one Köchel number, catalogue and Wikidata conflicts, old and new Köchel kept apart, and derived works never matching their parent. |
| `test_work_identity.py` | Identity end to end (#351): the four Mozart No. 40 titles resolving to one work plus a version linked to it, a Brandenburg movement and arrangement linked to BWV 1048 without merging, an ambiguous parent left unlinked, IMSLP's structured catalogue field and a shared Wikidata item as strong keys, stored identifiers matching in the next run, Wikidata ids normalised, P361 links, and `link_works` rebuilding rather than accumulating. |

**People**

| File | Covers |
| --- | --- |
| `test_person_extract.py` | Parsing name labels: comma and plain forms, initials, particles. |
| `test_person_compare.py` | Reducing a pair to comparison levels; the regression suite for #173 (initials vs spelled-out names). |
| `test_fellegi_sunter.py` | The linkage engine: weights as log2 Bayes factors, term frequency, the prior estimate, supervised fitting, JSON round-trip. |
| `test_person_matching.py` | Scoring and classifying a pair with the shipped model: dates, generations, rare surnames, aliases, cut-points. |
| `test_person_constraints.py` | Cannot-links from distinct QIDs and MusicBrainz ids, and when corroboration discharges them. |
| `test_person_cluster.py` | Turning scored pairs into clusters: chains, cannot-links (also transitive), edge-order independence. |
| `test_dedupe_persons.py` | The `dedupe-persons` pass over a warehouse: auto-link vs review, `--reset`, clusters and canonicals, reviewer decisions, QID twins. |
| `test_person_training.py` | Fitting the model: automatic labels, comparison vectors, non-match sampling, the prior, and `train()` end to end (including an empty warehouse). |
| `test_person_evaluation.py` | The labelling rules, the deterministic split, downsampling weights, metrics, and that the shipped model beats the pre-#173 scorer on the committed dataset. |

**Concerts, recordings and rebuild**

| File | Covers |
| --- | --- |
| `test_link_places.py` | Linking a bare place to the pinned place of the same name, leaving ambiguous and unmatched names alone, and a re-run dropping a link that became ambiguous. |
| `test_derive_concerts.py` | Grouping mentions into concerts per source, resolving conductors, soloists and the orchestra, choirs credited as soloists, re-running, and ids derived from source + key that a new concert sorting first doesn't move. |
| `test_derive_recordings.py` | Grouping release payloads into recordings, merging one album across pages, keeping different performers or catalogue numbers apart, IMSLP and scraper payloads, and ids derived from source + key. |
| `test_build.py` | The SQLite atomic swap: the manifest, and a failed or interrupted build leaving the old database in place. |
| `test_rebuild.py` | `rebuild-silver`: replaying every bucket source and run, preserving human decisions, skipping raw-page snapshots, failure keeping the old database, work links recreated on every rebuild, and the same genre entities and links as a live ingest when old label-only runs are replayed. Two cases also run on Postgres, one checking the rebuilt concert keeps its derived id. |
| `test_genres.py` | Genres and movements keyed on their QID across label changes, the record and claim paths meeting on one entity, label keys without a QID, linking label-only genres by label or alias (and not when ambiguous), relinking from scratch, the same genre set for any ingest order, and the orphan report's counts and ranking. |
| `test_postgres_target.py` | Postgres only: the schema swap, readers across it, the manifest table, and the locks that keep a rebuild and a reader apart. |

`ingestion/runner.py`'s `create_run` has no test here; the admin API, its only
caller, tests it through its load endpoint.
