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
  one stays a person until the list earns another word. The same module types
  an ensemble from its name (`ensemble_type_from_name`) and holds
  `CLASSICAL_NAME_CUES`, the words `wikidata_ensembles` admits a genre-less
  musical group for, each with the type the typer reads it as.
- `instrumentation.py` — folds stated scoring ("Klavier zu vier Händen", "for
  string orchestra") onto canonical scoring categories, so "every work for
  piano" is a query rather than a `LIKE` across spellings. It refuses to guess.
- `shorthand.py` — reads publishers' orchestral shorthand in both dialects
  (Chester/Novello `3223 / 2230 / timp.perc / str[8]`, Boosey
  `3.2.2.3 - 2.2.3.0 - timp - strings[6]`) into the instruments a work
  includes.
- `catalogues.py` — catalogue numbers normalised per composer into
  `(scheme, value)` pairs: `K`/`KV` both Köchel for Mozart (lettered numbers as
  `KV6`), `K` as Kirkpatrick for Domenico Scarlatti, Hoboken separators unified,
  `BWV`/`HWV`/`TWV`/`BuxWV`/`SWV`/`RV` for anyone, `WoO` and the appendices
  (`Anh.`, `BWV Anh.`, `KV Anh.`) as schemes of their own, and the bare letters
  (`D`, `S`, `LW`, `B`, `L`, `H`) only for the composer who uses them. The
  composer-scoped catalogues (#361) also cover C.P.E. Bach (`Wq`, Helm `H`,
  `BR-CPEB`), J.C. Bach (`Warb`, `Ty`), W.F. Bach (`F`/`Fk`, `BR-WFB`), Purcell
  `Z`, Graupner `GWV`, Lassus `LV` (and `LV Anh.`), Chopin (`B`, `KK`), R.
  Strauss (`TrV`, `AV`), Wagner `WWV`, Bartók (`BB`, `Sz`), Bruckner `WAB`,
  Nielsen `CNW`, Zelenka `ZWV`, Michael Haydn `MH`, Poulenc `FP` and Janáček
  `JW`; a composer entry can require a given name, or its initials in full
  (`C.P.E. Bach`, `Strauss, R.`, but not `J.S.` or `J.`), which tells the Bach
  sons apart. Only Helm's numbers keep a dotted part (`H 542.5`); Holst's,
  Honegger's and Martinů's `H. 103.2` is `H 103`, as Hoboken's `I 104.2` is
  `I:104`. `CATALOGUE_SCHEMES` is the set of every scheme the parser produces. A
  range or list (`BWV 1046-1051`, `TWV 40:2-13`, `BWV 1046, 1047`) is one pair
  whose value is the set in canonical form (`1046-1051`), never its first
  number; `catalogue_members` expands it (refusing a range wider than
  `MAX_RANGE`, #378). `normalize_wikidata_code`
  reads a Wikidata P528 code by its catalogue item: `WIKIDATA_CATALOGUES` maps
  checked QIDs to the very scheme objects above (the Köchel editions to
  `KV`/`KV6`, Terry's `238/3` to `Ty`); in a mapped item a bare `Anh.` is that
  catalogue's appendix (`KV Anh`, `BWV Anh`, the sixth edition's lettered `Anh.
  C 27.06` as `KV6 Anh`), an edition marker is no number (`KV6 173dB`), a
  sibling catalogue's prefix is read as that catalogue (`Wq 117` in the
  Bach-Repertorium), and a code written as an opus keeps it. A code in any
  other catalogue gets the item as its scheme, `wd:<QID>`, except in
  `NON_WORK_CATALOGUES` (rights societies, record labels, libraries), whose
  codes number registrations, releases or holdings rather than works and are
  not read. The warehouse's work matcher keys on these.
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
| `test_catalogues.py` | Catalogue normalisation: K/KV as one Köchel number, sixth-edition KV6 numbers (both from `K. 183/173dB`), Scarlatti's Kirkpatrick and Longo, relatives excluded, Hoboken separators, bare letters with and without the matching composer, Liszt's S vs LW, universal catalogues, appendices, WoO, structured fields with an opus, and ranges and lists (#358): `-`/`–`/`—`/`to` alike, the same form across BWV, HWV, TWV and Hob, never the first piece, list and range agreeing, what only looks like one (a movement `BWV 825-4`, a year, `BWV 57, 8`) keeping its first number, and `catalogue_members` (oversized, backwards and hyphenated external ids refused, #378); the composer-scoped catalogues of #361 with each ambiguous letter (`H`, `B`, `F`, `Z`, `LV`, `MH`, `KK`) refused for anyone else, the Bach sons told apart, Purcell's `Z.802-811` as a set, C.P.E. Bach's numbers within a collection (`Wq 117:39`, `H 542.5`, `BR-CPEB C 48.1`), a letter that is no reference not hiding the one after it, and Wikidata codes read by their catalogue item: mapped QIDs (the Köchel editions, Helm, Terry, a code in another catalogue than its item) and the `wd:<QID>` fallback (a range kept a set, a hyphenated number not, Rinaldi's `R. op.` and `op.` no opus, `deest` nothing). The #373 review cases: the Wikidata map reusing the parser's scheme objects, a sibling catalogue's prefix in a mapped item (`Wq 117` in BR-CPEB, `Ty 238` in Warburton's) while a section that looks like one stays a section (`Fp 12`), the Köchel edition marker (`KV6`/`KV⁶`/`K6 173dB`), a bare `Anh.` as the item's appendix (`KV Anh`, `BWV Anh`, `KK Anh`, lettered `KV6 Anh`, also in a title, each in `CATALOGUE_SCHEMES`), an opus-spelled code keeping its opus, rights-society, record-label and library codes read as nothing, Hoboken dropping a work's part (`I 104.2`), the dotted part kept only for Helm, and initials standing for the given names (`C.P.E.`, `J.C.`, `W.F.`, `R.`) but never the wrong ones (`J.S.`, `J.C.F.`, `J.`). |
| `test_kinds.py` | The ensemble guard (#174): ensemble labels recognised, musicians left alone, and only `person` second-guessed; the name typer reads every classical name cue the way `wikidata_ensembles` admits it, without widening the guard. |
| `test_instrumentation.py` | Scoring categories: combinations yield their parts, ensembles list their members apart, unrecognised scoring yields nothing, and the category tables are consistent. |
| `test_shorthand.py` | Real catalogue entries in both dialects: desks, doublings, percussion, string parts, score order, German abbreviations, and refusing what is not shorthand. |
