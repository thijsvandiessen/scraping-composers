# composer-schema

The source contracts: the document types every scraper produces and the
warehouse ingests, and the `SourceAdapter` interface. It has no heavy
dependencies, so every tier can import it without pulling in httpx, SQLAlchemy
or the scraper stack.

- `__init__.py` — `ScrapedDocument`, `EntityDocument`, `WorkMentionDocument`,
  `SourceClaim`, `SourceAdapter` and `RefreshCadence` (when a source is due
  again), plus document serialization for the bucket.
- `kinds.py` — the label-based ensemble guard. Every source that credits
  concert or recording participants files each credited *name* as a person,
  whether it names a violinist or an orchestra. A label carrying an ensemble
  word ("Symphony Orchestra", "Knabenchor", "String Quartet") is stored as an
  `ensemble` instead. The word list is deliberately narrow: re-kinding a real
  person is the worse error, so an ensemble whose name says nothing about being
  one stays a person until the list earns another word.
- `instrumentation.py` — folds stated scoring ("Klavier zu vier Händen", "for
  string orchestra") onto canonical scoring categories, so "every work for
  piano" is a query rather than a `LIKE` across spellings. It refuses to guess.
- `shorthand.py` — reads publishers' orchestral shorthand in both dialects
  (Chester/Novello `3223 / 2230 / timp.perc / str[8]`, Boosey
  `3.2.2.3 - 2.2.3.0 - timp - strings[6]`) into the instruments a work
  includes.
- `testing.py` — `person()`, `genre()`, `mention()`, `FakeSource` and friends, which every
  other member's tests use (the warehouse re-exports them).

## Tests

```sh
uv run --directory packages/composer-schema pytest --rootdir . --cov=composer_schema
```

| File | Covers |
| --- | --- |
| `test_schema.py` | Refresh-cadence staleness (static never due, never-run always due, naive timestamps as UTC), the document factories and `FakeSource`, and serialization round-trips. |
| `test_premiere.py` | The `raw["premiere"]` convention: ISO dates at year, month or day precision (BCE too), anything else ignored rather than guessed, and the precedence rule (earliest date wins, a full date refines a bare year). |
| `test_kinds.py` | The ensemble guard (#174): ensemble labels recognised, musicians left alone, and only `person` second-guessed. |
| `test_instrumentation.py` | Scoring categories: combinations yield their parts, ensembles list their members apart, unrecognised scoring yields nothing, and the category tables are consistent. |
| `test_shorthand.py` | Real catalogue entries in both dialects: desks, doublings, percussion, string parts, score order, German abbreviations, and refusing what is not shorthand. |
