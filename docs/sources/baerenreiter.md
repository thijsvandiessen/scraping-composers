# Bärenreiter catalogue: instrumentation in detail

`baerenreiter` reads every edition Bärenreiter sells or hires: about 17,000
products, of which ~13,000 are reached (see below for what is left out). It is
shaped like `boosey` — a work mention plus a composer-qualified work entity per
edition — and adds what boosey's pages do not state: the instrumentation **part
by part**, with counts and doublings.

The site is a client-rendered single-page app, so its HTML is an empty shell for
every product (the reason an earlier generic crawl of it stored 34,719 empty
pages). The data comes from the JSON API the page itself calls, which needs no
authentication:

| | |
| --- | --- |
| `/api/sitemap/products_en.xml` | every product id — the inventory |
| `/api/bv/product/{id}?lang=en` | one product, every field its page shows |

The shop's search API is not an inventory: it answers HTTP 500 past the 10,000th
hit and omits products hidden from search, so the sweep is one request per
sitemap id, mirrored through the page cache (≈3 hours the first time, free after).

```bash
uv run composer-ingest fetch baerenreiter --max-pages 50
uv run composer-ingest process baerenreiter
```

The API's field names are the publisher's German internals: `art` (*Artikel*, a
shop product) plus an abbreviation — `artAuCompos` composer, `artTiMain` title,
and the `artBstz…` (*Besetzung*, scoring) family. `baerenreiter/products.py` is
the one place that maps them to the labels the page shows:

| API field | Page label | Lands as |
| --- | --- | --- |
| `artBstzMainDispl` | Scoring | `written_for` edges, `has_scoring` literal |
| `artBstzDispl` | Instrumentation in detail | `includes_instrument` edges, `raw["instrumentation"]["parts"]` |
| `artBstzOrchNum` | Instrumentation | shorthand; read when there is no detail list |
| `artAuCompos` / `artAuText` / `artAuArr` | Composer / Librettist / Arranger | `composed_by` / `text_by` / `arranged_by` |
| `orderId`, `artPrTyDispl`, `artISMN` | Edition number, Product format, ISMN | `catalogue_number`, `edition_type`, `ismn` |
| `artTiDatOr`, `artLength` | Date of composition, Duration | `composed_in`, `has_duration` (minutes) |
| `state` | Availability | `raw["availability"]` |

The detail list is read entry by entry (`baerenreiter/instrumentation.py`); a
parenthetical is a count, a choir's voicing, or the instruments a player doubles
on (or that realise a continuo), told apart by what it contains:

```
Flute (2) (Piccolo flute)  ->  {"instrument": "flute", "count": 2, "also": ["piccolo"]}
Mixed choir (SATB) (2)     ->  {"instrument": "mixed choir", "count": 2, "voicing": "SATB"}
Basso continuo (Violoncello, Organ)
                           ->  {"instrument": "basso continuo", "also": ["cello", "organ"]}
```

Names resolve through the same `composer_schema.instrumentation` table the
`claims` extractor uses, so "Violoncello" here and "Cello" elsewhere are one
entity; ~96% of entries resolve, and what does not is kept verbatim under
`raw["instrumentation"]["unmatched"]` rather than guessed. Counts stay in `raw`,
as the shorthand's do. About a sixth of the catalogue states *only* the
shorthand, which is read the same way as a publisher shorthand (including
Bärenreiter's German abbreviations and a sized string section, `str (4.4.3.2.1)`).

Left out on purpose:

- **Digital twins.** `BA05163D` is `BA05163` as a PDF — same edition, same
  contents — so it is not fetched when the print product is listed; its id is
  recorded on the print edition as `digital_edition_id`. Digital-only editions
  are kept.
- **Books, magazines and media** (`NON_MUSIC_TYPES`): their contents are chapters.

An anthology's items are works of their own: each item with a composer becomes a
work mention (`<product>#<n>`), and the anthology itself is only an entity.
