# Boosey & Hawkes work metadata

`boosey` is the first source carrying *work* metadata — scoring, duration, year
of composition — rather than concert programmes or people. It walks the
publisher's catalogue in three hops (composer index → each composer's work list
→ each work's detail page) and emits **two documents per work**, sharing the
work's Boosey id:

- a `WorkMentionDocument`, which the work matcher resolves to a canonical `works`
  row, with everything the page stated kept in `raw`;
- an `EntityDocument` of kind `work`, whose claims (`has_scoring`,
  `has_duration`, `composed_in`, `composed_by`, `published_by`) make that
  metadata queryable next to every other claim.

Both are needed because the two land in different tables — `Entity(kind="work")`
is not the same row as a `Work`, and nothing links them.

```bash
uv run composer-ingest fetch boosey --max-pages 5
uv run composer-ingest process boosey
uv run composer-ingest claims "Kerori" --kind work --source boosey
```

Two things to know before running it wide:

- **Work entity labels are composer-qualified** ("Kerori (Walter Steffens)").
  Entity dedup keys on the normalised label alone, so a bare title would merge
  two composers' identically-named works into one entity and pool their claims.
- **Parsing is label-driven, not markup-driven.** `boosey/works.py` flattens a
  page to text lines and reads "Scoring", "Duration", "Year Composed" … off by
  label, so it survives a redesign and skips labels it does not recognise. Add
  aliases to `_LABELS` rather than writing new regexes. Discovery
  (`boosey/catalogue.py`) keys only on the stable `/cr/music/<slug>/<id>` URL
  shape; the id is the trailing integer, and the slug is decorative.
