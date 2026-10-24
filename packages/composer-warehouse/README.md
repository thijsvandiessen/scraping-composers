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
candidate works by the same composer, and scores them. A matching `BWV`/`Op.`
number is near-certain identity; otherwise a `difflib` title comparison
sharpened by the extracted features decides. High scores auto-match, middling
scores are flagged for `composer-ingest review`, low scores create a new work.
Work ids are assigned at creation, not derived from the title. The tables are
described in [`composer-models`](../composer-models/README.md#data-model).

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

## Ensembles

Every concert and recording source mints one ensemble per printed name, so one
orchestra sits in silver under each spelling ("Vienna Philharmonic", "The
Vienna Philharmonic", "Wiener Philharmoniker"). The `wikidata_ensembles` source
supplies **anchors**: ensembles keyed on their QID alone (`wikidata:Q…`, see
`entity_key` in `composer-models`), each with a type (`ensemble_type`), a home
city (`based_in`, pinned to the city's QID like every Wikidata place, so it is
the same place entity composers are born in) and every label and
alias Wikidata holds in the sources' languages.

`anchor_ensembles` (`ensembles/`, `composer-ingest anchor-ensembles`, also run
before every derive in promote and rebuild) points each name-keyed ensemble at
the anchor it names through `canonical_entity_id`, the same non-destructive
link the person dedupe writes:

- The match key is `dedup_key` minus a leading article, with a few ensemble
  words mapped to one spelling (`Philharmoniker`/`Philharmonic Orchestra` →
  `philharmonic`, `Orchester`/`Orchestre`/`Orkest` → `orchestra`). Nothing
  fuzzy: a name is one of the anchor's names or it is left alone, which keeps
  "Wiener Philharmoniker Streichquartett" and "Junge Philharmonie Wien" apart.
- A name two anchors carry is linked only when exactly one anchor's home city
  is spelled out in it; otherwise it is counted as ambiguous.
- "Members of X" / "Mitglieder der X" stay their own ensemble.
- No human decides ensemble links, so the pass clears the column for the kind
  first: re-runs are idempotent and a link goes when its evidence does.
- It reports, without moving anything, how many person entities carry an
  anchor's name and how many name-keyed ensembles carry a Wikidata person's.

The derive passes fall back to the anchors' names (`alias_index`) for a credit
no source minted an entity for. Gold collapses each anchor's spellings into
one ensemble.

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

## Rebuilding silver

Interpretation (entity resolution, work matching) is applied when a record is
first loaded — improving the heuristics doesn't fix rows already in the
database. `rebuild-silver` closes that gap by replaying the bucket into a fresh
database with the current code, then re-running dedupe, place linking and concert derivation:

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
| `test_work_extract.py` | Extracting catalogue numbers (BWV, K., Op.), key, type and number from titles, including German titles. |
| `test_work_matching.py` | Scoring candidates and deciding auto-match, review or create. |

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

**Ensembles**

| File | Covers |
| --- | --- |
| `test_anchor_ensembles.py` | The four Vienna Philharmonic spellings folded onto the QID-keyed anchor; the quartet, chamber orchestra and "Members of" kept apart; a shared name split by home city or left ambiguous; idempotent re-runs and stale links cleared; the person/ensemble boundary counts; unanchored ensembles on a programme; the home city landing on the pinned place composers point at; match keys. |

**Concerts, recordings and rebuild**

| File | Covers |
| --- | --- |
| `test_link_places.py` | Linking a bare place to the pinned place of the same name, leaving ambiguous and unmatched names alone, and a re-run dropping a link that became ambiguous. |
| `test_derive_concerts.py` | Grouping mentions into concerts per source, resolving conductors, soloists and the orchestra, choirs credited as soloists, a credit resolved through an ensemble anchor's alias, re-running, and ids derived from source + key that a new concert sorting first doesn't move. |
| `test_derive_recordings.py` | Grouping release payloads into recordings, merging one album across pages, keeping different performers or catalogue numbers apart, IMSLP and scraper payloads, and ids derived from source + key. |
| `test_build.py` | The SQLite atomic swap: the manifest, and a failed or interrupted build leaving the old database in place. |
| `test_rebuild.py` | `rebuild-silver`: replaying every bucket source and run, preserving human decisions, skipping raw-page snapshots, failure keeping the old database. Two cases also run on Postgres, one checking the rebuilt concert keeps its derived id. |
| `test_postgres_target.py` | Postgres only: the schema swap, readers across it, the manifest table, and the locks that keep a rebuild and a reader apart. |

`ingestion/runner.py`'s `create_run` has no test here; the admin API, its only
caller, tests it through its load endpoint.
