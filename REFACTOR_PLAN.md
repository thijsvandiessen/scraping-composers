# Refactor plan: uniform `Document` + injectable `Scraper`

## Context

We want to add more sources soon, but the current source layer doesn't scale cleanly:

- **Two record shapes** (`SourceRecord` with `kind`/`claims`, and `SourceWorkMention`) flow
  through the pipeline, and `ingest.py` dispatches on Python `isinstance` plus `record.kind`.
  Every new source has to reason about both.
- **No shared scraper.** Each source is a duck-typed module; HTTP retry/backoff/delay logic is
  copy-pasted in every `sources/<name>/fetch.py` (imslp, wikidata, concertgebouw, berlinphil all
  re-implement the same loop).
- **No uniform document identity.** Fetched objects carry no `source_name` and no ingested
  timestamp; that information only exists implicitly in the bucket path `{source}/{run_id}/`.

Goal of this refactor (no new source added in this pass — that becomes trivial afterward):

1. One generic **`Document`** with the same base fields on every object — `id`, `url`,
   `ingested_at`, `source_name` — plus a freeform `body`.
2. A base **`Scraper`** with the per-source special cases *injected* (config + strategy
   functions), so HTTP plumbing lives in one place and a source is just config + parse.
3. Migrate the 5 existing sources onto it with the test suite green at each step.

---

## 1. The `Document` model

New module `src/composer_ingest/sources/document.py` (re-exported from `sources/__init__.py`):

```python
@dataclass(frozen=True)
class Document:
    id: str                 # source-scoped stable id (was external_id)
    source_name: str        # which source produced it
    url: str | None         # link back to the source
    ingested_at: str        # ISO-8601 UTC, stamped at fetch time
    doc_type: str           # discriminator: "entity" | "work_mention"
    content_hash: str       # sha256 of the canonical body; change-detection key
    body: dict[str, Any]    # freeform; shape depends on doc_type
```

- **Base fields** (`id`, `url`, `ingested_at`, `source_name`) are identical for every document,
  exactly as requested. `body` is "anything".
- **`content_hash`** — a `sha256` hex digest of the *canonicalized* `body`
  (`json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`), computed and
  stamped centrally by the `Scraper` (same place as `ingested_at`/`source_name`) so it's
  consistent and parsers can't forget it. It does **not** cover the volatile base fields
  (`ingested_at` changes every fetch), only the content. This lets us tell "seen again,
  unchanged" from "seen again, content changed" on re-ingest (see §3).
- **`doc_type`** is the discriminator the ingest needs to keep entities/claims and work
  resolution working without `isinstance`. Body contracts:
  - `doc_type="entity"` → `body = {"name", "kind", "claims": [ {predicate, object_kind,
    object_label, value}, ... ], "raw": {...}}`
  - `doc_type="work_mention"` → `body = {"title", "composer", "raw": {...}}`
- **`id`** stays whatever the source already used as `external_id` (no change to dedup behavior).
- **`ingested_at` / `source_name` are stamped centrally** by the `Scraper` (see §2) via
  `dataclasses.replace`, so individual parsers can't forget them.
- Keep `SourceClaim` as an ergonomic dataclass for building claims; convert to plain dicts
  (`dataclasses.asdict`) when placing into `body["claims"]`.

**Factory helpers** (in the same module) so parsers stay terse and consistent:

```python
def entity_document(id, name, url, kind="person", claims=(), raw=None) -> Document
def work_mention_document(id, title, composer, raw) -> Document
```

These leave `source_name=""` / `ingested_at=""` / `content_hash=""`; the `Scraper` fills them.
Reuses `normalize`/`entity_uuid` nowhere here — identity is unchanged downstream.

---

## 2. The base `Scraper` (injected config + strategy)

New module `src/composer_ingest/scraper.py`:

```python
@dataclass(frozen=True)
class SourceConfig:
    name: str
    base_url: str

# injected strategies (Protocols)
class Pages(Protocol):
    def __call__(self, http: Http, max_pages: int | None) -> Iterator[Any]: ...
class Parse(Protocol):
    def __call__(self, raw: Any) -> Iterator[Document]: ...

class Scraper:
    def __init__(self, config: SourceConfig, pages: Pages, parse: Parse) -> None: ...
    @property
    def NAME(self) -> str: ...        # back-compat for cli/ingest
    @property
    def BASE_URL(self) -> str: ...
    def fetch_documents(self, max_pages: int | None = None) -> Iterator[Document]:
        for raw in self.pages(self.http, max_pages):
            for doc in self.parse(raw):
                yield dataclasses.replace(
                    doc,
                    source_name=self.config.name,
                    ingested_at=_utc_now_iso(),
                    content_hash=_content_hash(doc.body),
                )
```

- The two special cases per source — **how to page** and **how to parse** — are injected.
- `_content_hash(body)` (a small helper in `document.py`) is the single place the hash is
  computed, so the bucket and the DB always agree on it.
- HTTP concerns are hoisted into a shared **`Http`** helper (new `src/composer_ingest/http.py`)
  wrapping `httpx` with the retry/exponential-backoff/polite-delay logic currently duplicated in
  every `fetch.py` (`get_json`, `get_text`, `post`). This is the single biggest dedup win.
- `REGISTRY` in `sources/__init__.py` becomes `dict[str, Scraper]` (instances, not modules).

---

## 3. Ingest: interpret one generic `Document`

`ingest.py` keeps its preload-caches + batched-commit loop; only the per-item branch changes:

- Replace `isinstance(item, SourceWorkMention)` with `if item.doc_type == "work_mention"`.
- Field access moves into `body`:
  - work_mention: `item.id`, `item.body["composer"]`, `item.body["title"]`, `item.body["raw"]`.
  - entity: `item.id`, `item.body["name"]`, `item.url`, `item.body["kind"]`,
    `item.body["raw"]`, `item.body["claims"]` (list of dicts).
- `_ingest_mention` and the entity branch read from `body` instead of the old attributes.
  All downstream logic (Entity/EntityRecord/Claim creation, `RawWorkMention`/`Work` resolution
  via `extract_features`/`resolve`, idempotency on `(source, id)`) is unchanged.
- `run_ingest(session, scraper, max_pages)` calls `scraper.fetch_documents(...)`;
  `run_ingest_from_bucket` iterates `Document`s. `_run_ingest_records` is typed
  `Iterator[Document]`.

**Change detection via `content_hash`** (review request): persist the hash next to each staged
record so re-ingest can distinguish unchanged from updated content.

- `models.py`: add a `content_hash: Mapped[str]` column to `EntityRecord` and `RawWorkMention`
  (the two staging tables keyed on `(source_id, external_id)`).
- On re-seeing an existing `(source, id)`: today we only bump `last_seen_at`/`last_run_id`. With
  the hash we compare `document.content_hash` to the stored value:
  - **same** → bump `last_seen_at` only (content unchanged), as now.
  - **different** → also overwrite `name`/`url`/`raw` (and re-run claim/work resolution for that
    record), update `content_hash`, and bump the entity's `last_edited_at`.
- This is a behavior *improvement* gated by the new column — preload the existing hashes in the
  same query that already preloads `(external_id, id, entity_id)` so the per-record loop stays
  query-free.

This keeps the "special behavior injected" theme: doc_type → handler mapping is the seam where a
future doc_type plugs in.

---

## 4. Serialization / bucket

`raw_fetch.py` collapses to one type — no more `_type` tagging or claim re-hydration:

```python
def _serialize(doc: Document) -> dict:    return dataclasses.asdict(doc)
def _deserialize(d: dict) -> Document:    return Document(**d)
```

NDJSON layout is unchanged (`{source}/{run_id}/records.ndjson`); each line is now a `Document`.
`bucket.py` only needs a docstring update.

---

## 5. Migrate the 5 existing sources (one pattern)

Each `sources/<name>/` becomes: a `pages(http, max_pages)` generator (paging/fetch, using the
shared `Http`) + a `parse(raw)` generator (emits `Document`s via the factories), wired into a
module-level `Scraper`. Removes the per-source retry loop.

- **imslp** — `pages`: paginate `API.ISCR.php`; `parse`: name → `entity_document(kind="person")`,
  no claims.
- **wikidata** — `pages`: SPARQL POST pagination + metrics; `parse`: reuse existing `parse.py`
  row-folding to emit `entity_document` with claims.
- **concertgebouw** — `pages`: `[search-page text, list-page text]`; `parse`: dropdowns →
  `entity_document`, performances → `work_mention_document` (mixed types in one stream — now fine).
- **nyphil** — `pages`: `[kaggle json]`; `parse`: people → `entity_document`, performances →
  `work_mention_document`.
- **berlinphil** — `pages`: concert list → per-concert detail; `parse`: works →
  `work_mention_document`, artists → `entity_document`.

The existing parser modules (`performances.py`, `dropdowns.py`, `artists.py`, `people.py`,
`parse.py`, `text.py`) are reused almost verbatim — they just return `Document`s instead of
`SourceRecord`/`SourceWorkMention`.

---

## 6. Tests

- `FakeSource` (in `tests/test_ingest.py`) → build via the real `Scraper` with injected fake
  `pages`/`parse`, or a tiny stub exposing `fetch_documents`. Update `conftest` if needed.
- Per-source tests assert emitted `Document`s have the right `doc_type` and `body` keys instead
  of the old dataclasses.
- New tests: `test_document.py` (factories + bucket round-trip + stable `content_hash` for equal
  bodies, different hash for changed bodies), `test_scraper.py` (stamps
  `source_name`/`ingested_at`/`content_hash`, threads `max_pages`), `test_http.py` (retry/backoff).
- Change-detection test: re-ingesting the same `(source, id)` with an unchanged body only bumps
  `last_seen_at`; with a changed body it updates the stored fields, `content_hash`, and
  `last_edited_at`.
- A golden test: a known input produces the same Entity/Claim/Work rows as before the refactor.

---

## 7. Ordered sequence (suite green at each step)

1. Add `Document` + factories, `Http`, `Scraper` (pure additions — nothing breaks).
2. Migrate **imslp** + its tests; add a `doc_type` branch to `ingest.py` alongside the old
   branches; verify.
3. Migrate wikidata → concertgebouw → nyphil → berlinphil, one at a time, verifying after each.
4. Switch `raw_fetch`/bucket serialization to `Document`.
5. Remove `SourceRecord`/`SourceWorkMention` and the old ingest branches; flip `REGISTRY` to
   `Scraper` instances; update `cli.py` and `run_ingest`.
6. `uv run ruff check` + `uv run mypy` (strict) + `uv run pytest` clean.

## Files

- **Create:** `sources/document.py`, `scraper.py`, `http.py`,
  `tests/test_document.py`, `tests/test_scraper.py`, `tests/test_http.py`.
- **Modify:** `sources/__init__.py`, `raw_fetch.py`, `bucket.py`, `ingest.py`, `models.py`
  (add `content_hash` columns), `cli.py`, each `sources/<name>/` package, and the existing
  source/ingest tests.

## Verification

- `uv run pytest` (incl. new round-trip + golden tests), `uv run mypy`, `uv run ruff check`.
- CLI smoke via a fake/offline source: `fetch` → `process` → `stats`. (Live network sources
  need egress; kaggle for nyphil.)

## Payoff (next pass)

Adding a source — e.g. **Hyperion Records** — becomes: write `pages` + `parse`, register one
`Scraper`. (Note: Hyperion's host must be added to this environment's network egress allowlist;
the current 403 is `Host not in allowlist`, not Hyperion blocking us.)
