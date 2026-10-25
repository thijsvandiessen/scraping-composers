# Henle catalogue: difficulty per work

`henle` reads every edition G. Henle sells: the 2,241 products in henle.de's
English sitemap. It is shaped like `baerenreiter` (a work mention plus a
composer-qualified work entity) and adds what no other source here states: Henle's
**level of difficulty**. Henle grades every piano, violin, flute, cello and
clarinet work from 1 (easy) to 9 (difficult), and grades it *per work*, so a
volume of nine Mozart sonatas says which one is the hardest.

The shop is Shopware, rendered on the server; its `/api/` is disallowed in
`robots.txt`, so the product page itself is the record. One request per product,
mirrored through the page cache (≈40 minutes the first time, free after; ~55KB
per page gzipped).

```bash
uv run composer-ingest fetch henle --max-pages 20
uv run composer-ingest process henle
uv run composer-ingest claims "Impromptu c minor op. 90,1 D 899" --kind work --source henle
```

`henle/products.py` reads the page by its class names:

| Page | Markup | Lands as |
| --- | --- | --- |
| Composer | `h2.product-detail-subtitle` | `composed_by` (an anthology's "Piano Music (Collection)" is not a person; its rows name their own composers) |
| Scoring ("Piano solo", "String Quartets") | `.product-detail-properties-container` | `written_for` / `includes_instrument` edges, `has_scoring` literal |
| Edition type ("Urtext Edition, paperbound") | `.product-detail-properties-container` | `edition_type` |
| Contributors ("… (Editor)", "… (Fingering Piano)") | `.product-persons-contributors-row` | `edited_by`, `fingering_by`; other roles stay in `raw` |
| HN number, ISMN, pages | `itemprop="sku"`, `itemprop="ISMN"`, `.product-detail-weight` | `catalogue_number`, `ismn`, `page_count` |
| Contents: title, grade 1–9, band, ABRSM grade | `.mws-henle-work` rows | one work each, `difficulty_level`; band and ABRSM in `raw` |

What a product becomes depends on its contents table:

- **Several rows** (a volume): each row is a work (`HN-1#3`) with its own grade
  and a `part_of` edge to the volume, which is only an entity (`HN-1`) carrying the
  edition facts. A bold row is a set ("4 Impromptus op. 90 D 899") whose pieces
  follow as rows of their own; nothing in the markup says where the set ends, so
  rows are not nested, and `raw["heading"]` marks the set.
- **One row or none**: the edition is the work (`HN-659`), one mention and one
  entity carrying every claim.

Parts, study score and complete-edition volume are separate products (HN 741,
HN 9741, …) and arrive as separate mentions of the same composer-qualified title;
the matcher and entity dedup fold them. Henle's scoring names shelves as well as
forces: "Violin Concertos" is read as violin and orchestra, while "Chamber music
with winds" names no scoring and is counted in the fetch log's `scorings
unrecognised`.
