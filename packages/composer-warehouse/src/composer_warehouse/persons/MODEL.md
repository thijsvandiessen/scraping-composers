# The person linkage model

`model.json` holds the fitted parameters for `composer_warehouse.persons`.
This file records where they came from, what they are worth, and what has
*not* been established. Regenerate both with:

```
composer-ingest person-train --dataset-out packages/composer-warehouse/tests/data/person_eval_pairs.jsonl.gz
composer-ingest person-eval packages/composer-warehouse/tests/data/person_eval_pairs.jsonl.gz
```

Fitted 2026-08-25 against `composers.db` (212,328 person entities, 2,104,981
candidate pairs after surname blocking).

## Results

Held-out test split, 29,003 labelled pairs. "Names only" hides the birth and
death years from the scorer but keeps the term-frequency adjustment.

| scorer | threshold | precision | recall | F1 | false positives |
|---|---|---|---|---|---|
| Fellegi-Sunter | 0.99 | **0.977** | **0.796** | 0.877 | 68 |
| Fellegi-Sunter, names only | 0.99 | 0.899 | 0.298 | 0.448 | 123 |
| pre-#173 scorer (baseline) | 0.90 | 0.781 | 0.779 | 0.780 | 805 |
| pre-#173 scorer, names only | 0.90 | 0.173 | 0.779 | 0.282 | 13,738 |

The model beats the baseline on precision *and* recall at once, cutting false
positives by 92%. The names-only rows are the sharper comparison, because the
labels there are independent of everything the scorer can see: 0.899 against
0.173 is the size of the defect #173 reported.

Run over the whole warehouse, the pass now auto-links **16,670** pairs where
the old scorer linked 364,389 — and none of them on initials alone, which
accounted for 340,650 of the old total. Another 20,488 land in the review
queue.

Operating points on the full model, for choosing a different cut-point:

| threshold | precision | recall |
|---|---|---|
| 0.50 | 0.761 | 0.851 |
| 0.90 | 0.904 | 0.838 |
| 0.95 | 0.925 | 0.831 |
| 0.99 | 0.977 | 0.796 |
| 0.995 | 0.986 | 0.782 |
| 0.999 | 0.993 | 0.717 |
| 0.9999 | 1.000 | 0.681 |

`AUTO_THRESHOLD = 0.99` is the point where the model dominates the old scorer
on both axes; `REVIEW_THRESHOLD = 0.50` catches the rest for `person-review`.
Raise the auto cut-point to 0.999 if 99% precision is wanted and a third of
the true links can wait in the review queue.

## Where the labels come from

No pair was judged by hand. Each label comes from evidence outside the
matcher, and is refused when two rules disagree:

| provenance | label | rule | pairs |
|---|---|---|---|
| `dates_corroborated` | match | birth *and* death year agree, backed by >= 2 distinct sources | 6,303 |
| `alias_identity` | match | one record's name is a curated wikidata alias of the other, and the two names differ by more than word order | 1,163 |
| `distinct_musicbrainz` | non-match | both carry MusicBrainz ids and they differ | ~351,000 |
| `year_conflict` | non-match | lifetimes more than a decade apart | ~521,000 |

The committed set at `tests/data/person_eval_pairs.jsonl.gz` samples the two
large negative strata down and stores the reweighting factor per row, so the
metrics above are the full-population numbers, not the sample's. Negatives
that any scorer rates as a possible link are exempt from sampling and kept
whole — they are the only rows that can become false positives, and
extrapolating them from a sample made the precision estimate lurch by several
points between runs.

## How the parameters were estimated

`u` (P(level | non-match)) and `m` (P(level | match)) are counted directly over
the labelled *training* split. The test split never touches a parameter, and
the split is a hash of the two names, so a pair cannot drift across it between
runs.

The prior — P(match | the pair survived surname blocking) — uses no labels at
all. Blocked pairs share a full given name 1.6% of the time, random pairs
0.08%; since `observed = L*m + (1-L)*u` and `m` cannot be negative, that gap
bounds `L` from below, and the bound taken as an equality gives **L = 0.0199**.

Two rules keep this honest:

- **Date-derived labels inform the name column only.** `dates_corroborated`
  and `year_conflict` are excluded from fitting `birth_year` and `death_year`,
  because setting a feature's weight from a rule *about that feature* measures
  nothing. The year weights come from `alias_identity` and
  `distinct_musicbrainz`, which owe the dates nothing.
- **Term frequency is a redistribution, not a bonus.** The adjustment is
  `log2(reference / p_v)` with `reference` the pair-weighted *geometric* mean
  frequency, so it averages to zero over the pairs actually scored and leaves
  the prior meaning what it says.

## EM was tried and rejected

Splink's unsupervised EM was the issue's leading candidate and is implemented
in neither this model nor the module, because on this corpus it does not
converge to anything usable. Measured behaviour, from several starts:

- From uninformative parameters, EM inverts the mixture — it labels the 2M
  conflicting pairs the matches and returns a prior of 1.0.
- Seeded so that the first M-step reads correct parameters off an exact-name
  split (`m[given][EXACT] = 0.987`, prior 0.0156 — the right answer), the
  likelihood then climbs monotonically away from it, reaching prior 0.43 and
  `m[given][CONFLICT] = 0.95` after 60 iterations.
- Pinning `u` and pinning the prior each slow the drift without stopping it.
  There is no interior fixed point to stop at.

This is the expected failure when matches are under 2% of pairs and the
comparison columns are only approximately independent given match status. With
labels available for free, counting them is both more robust and more direct.

## What this does *not* establish

- **Precision is over labellable pairs, not all pairs.** Roughly 42% of
  candidate pairs carry no external evidence and appear nowhere in the set. The
  labelled population over-represents wikidata-vs-wikidata pairs, which are the
  hard case, so the true corpus precision is probably better than the table
  says — but that is an argument, not a measurement.
- **The year columns' weights are weakly evidenced.** They are fitted from
  `alias_identity` and `distinct_musicbrainz` alone, and the corpus offers no
  date-independent way to check whether "more than a decade apart" is the right
  place to put the line.
- **Recall on `alias_identity` is very poor (0.03).** What remains in that
  stratum after excluding mere word-order differences is the genuinely hard
  residue: stage names, exonyms and transliterations. Most are not reachable by
  surname blocking at all, so the model never sees the pair to score it. That
  is a blocking limitation, not a scoring one, and the number to beat if
  blocking is revisited.
