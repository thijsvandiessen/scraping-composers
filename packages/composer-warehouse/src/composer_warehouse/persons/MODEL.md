# The person linkage model

`model.json` holds the fitted parameters for `composer_warehouse.persons`.
This file records where they came from, what they are worth, and what has
*not* been established. Regenerate both with:

```
composer-ingest person-train --dataset-out packages/composer-warehouse/tests/data/person_eval_pairs.jsonl.gz
composer-ingest person-eval packages/composer-warehouse/tests/data/person_eval_pairs.jsonl.gz
```

Fitted 2026-11-08 against `composers.db` (290,608 person entities, 3,416,675
candidate pairs after surname blocking), after #202 split the surname-only
given-name level in two.

## Results

Held-out test split, 31,986 labelled pairs. "Names only" hides the birth and
death years from the scorer but keeps the term-frequency adjustment.

| scorer | threshold | precision | recall | F1 | false positives |
|---|---|---|---|---|---|
| Fellegi-Sunter | 0.99 | **0.991** | **0.796** | 0.883 | 51 |
| Fellegi-Sunter, names only | 0.99 | 0.932 | 0.282 | 0.433 | 145 |
| pre-#173 scorer (baseline) | 0.90 | 0.841 | 0.793 | 0.816 | 1,048 |
| pre-#173 scorer, names only | 0.90 | 0.244 | 0.793 | 0.373 | 17,232 |

The model beats the baseline on precision *and* recall at once, cutting false
positives by 95%. The names-only rows are the sharper comparison, because the
labels there are independent of everything the scorer can see: 0.932 against
0.244 is the size of the defect #173 reported. Splitting the surname-only level
changed none of the overall figures: the same code with the old single null
level, refitted on the same corpus, scores 0.9909 / 0.7960 at 0.99 and
0.880 / 0.876 at 0.50, identical to three places.

On the 2026-08-25 database the pass auto-linked **15,826** pairs where the
old scorer linked 364,389 — and none of them on initials alone, which
accounted for 340,650 of the old total. Another 13,405 landed in the review
queue. For the 2026-11-08 figures see "Surname-only pairs" below.

Operating points on the full model, for choosing a different cut-point:

| threshold | precision | recall |
|---|---|---|
| 0.50 | 0.880 | 0.876 |
| 0.90 | 0.953 | 0.850 |
| 0.95 | 0.965 | 0.822 |
| 0.99 | 0.991 | 0.796 |
| 0.995 | 0.993 | 0.772 |
| 0.999 | 0.997 | 0.705 |
| 0.9999 | 1.000 | 0.646 |

`AUTO_THRESHOLD = 0.99` is the point where the model dominates the old scorer
on both axes; `REVIEW_THRESHOLD = 0.50` catches the rest for `person-review`.
Neither applies to a surname-only pair, which has its own policy below.

## Surname-only pairs (#202)

A record that is nothing but a surname ("Mozart", "Brahms") used to land on a
single `ABSENT` given-name level that was the column's null level: zero bits,
so with no years either the pair scored the prior plus the surname's rarity,
and 1,617 of the 1,749 bare surnames sharing a block with a full name had no
`person_matches` row at all. The level is now two, both fitted like any other:

| level | meaning | weight |
|---|---|---|
| `ONE_SURNAME_ONLY` | one side bare, the other has given names | -0.13 bits |
| `BOTH_SURNAME_ONLY` | neither side has given names | +0.21 bits |

The fit says what intuition does: a bare surname, on its own, is no evidence
for identity. What still moves such a pair is the surname's rarity
(`tf_bits`) and any years.

They are kept out of the prior's moment bound (`training.PRIOR_LEVELS`):
whether a record has given names at all is not distributed alike over random
and blocked pairs, matches or not, so the prior is recovered from the four
levels that compare two given-name sequences, exactly as before.

### Precision and recall, by surname rarity

`ONE_SURNAME_ONLY` pairs in the held-out split (1,725 rows standing for 374
matches and 22,166 non-matches after reweighting), cut by the posterior.
Rarity is the size of the surname's block.

| cut | all P / R | block <= 5 | 6-20 | 21-100 | > 100 |
|---|---|---|---|---|---|
| 0.03 | 0.396 / 0.532 | 0.257 / 0.506 | 0.701 / 0.848 | 0.269 / 0.355 | 0 / 0 (37 fp) |
| **0.04** | **0.425 / 0.508** | 0.257 / 0.506 | 0.701 / 0.848 | 0.289 / 0.258 | 0 / 0 (21 fp) |
| 0.05 | 0.472 / 0.487 | 0.283 / 0.506 | 0.701 / 0.848 | 0.432 / 0.172 | 0 / 0 (21 fp) |
| 0.10 | 0.408 / 0.353 | 0.283 / 0.506 | 0.651 / 0.600 | 0.118 / 0.022 | 0 / 0 (21 fp) |
| 0.50 | 0.163 / 0.056 | 0.200 / 0.165 | 0.238 / 0.034 | 0.118 / 0.022 | 0 / 0 (21 fp) |
| 0.99 | 1.000 / 0.016 | 1.000 / 0.047 | 1.000 / 0.014 | — | — |

The labelled stratum flatters these numbers. Its matches are `alias_identity`
rows (wikidata listing the bare name as an alias of one item), and the
namesakes the same bare record faces are mostly unlabelled. A label-free bound
over *every* candidate pair says more: a bare record whose candidates above the
cut carry `k` distinct QIDs is right about at most one of those `k` people.

| cut | bare records | pairs | facing >= 2 QIDs | precision at most |
|---|---|---|---|---|
| 0.04 | 1,731 | 8,701 | 794 | 0.632 |
| 0.10 | 1,441 | 4,208 | 523 | 0.712 |
| 0.50 | 49 | 52 | 1 | 0.981 |
| 0.99 | 8 | 9 | 0 | 1.000 |

### Policy

- **Never auto-linked.** Nowhere is precision clearly high except at 0.99,
  where the measurement rests on six held-out pairs and recall is 1.6%. Every
  namesake in a block scores alike against a bare surname, so a link would be
  a guess between them. `classify` sends a surname-only pair to review
  whatever its score.
- **Queued from `SURNAME_ONLY_REVIEW_THRESHOLD = 0.04`**, measured precision
  0.425 at recall 0.508 on the held-out split. Precision is flat to within
  five points between 0.03 and 0.05 (it peaks at 0.05, 0.472), and 0.04 is
  the highest cut at which `Mozart` -> `Mozart, Wolfgang Amadeus` (0.0486)
  reaches a decision. Above 100 namesakes nothing is right at any cut.
- **A bare alias does not count for a record that has given names.** A bare
  surname cannot conflict, so "Leopold Mozart" with the alias "Mozart" would
  otherwise beat his own given-name conflict against Wolfgang Amadeus.
- **No record joins two namesakes' authority items.** Separately from the
  score, a record whose machine links reach two distinct, uncorroborated QIDs
  or MusicBrainz ids it holds none of (an undated "Johann Strauss" against
  Johann Strauss I, II and III) has every machine link sent back to review
  (`dedupe._demote_ambiguous_links`), rather than joining whichever scored
  highest. The cannot-links still keep the items apart as before.

Run on a copy of the 2026-11-08 database (`dedupe-persons --reset`):

| | before | after |
|---|---|---|
| bare surnames sharing a block with a full name | 1,749 | 1,749 |
| ... with no `person_matches` row | 1,617 | **206** |
| ... linked | 34 | 17 |
| auto_linked / needs_review | 25,781 / 23,096 | 26,835 / 30,422 |
| `given:one_surname_only` in review | — | 9,053 |
| links held back as ambiguous between namesakes | — | 476 |
| surname-only auto-links | — | 0 |

"Before" is the live database, from the pass the previous model ran; "after"
is the refit model and this policy on a copy of it. Fewer bare surnames are
linked because none is auto-linked on its surname any more; the review queue
now holds them instead.

## What the authority ids refuse

Scores decide pairs; `constraints.py` gets a veto (#204). Two records carrying
distinct Wikidata QIDs or distinct MusicBrainz ids are two people unless the
two items agree on a birth year *and* one names the other in `also_known_as`,
and the constraint is checked against the whole cluster — so the merge is
refused even when it arrives transitively through a bare-name record, which is
how most of them arrived.

Measured over the 15,826 recorded links on the 2026-08-25 database:

| | before | after |
|---|---|---|
| clusters | 14,409 | 13,802 |
| members | 29,866 | 28,480 |
| largest cluster | 7 | 6 |
| clusters spanning two QIDs | 862 | 102, all corroborated |
| clusters spanning conflicting MusicBrainz ids | 77 | 4, all corroborated |

897 authority conflicts were found (892 wikidata, 86 MusicBrainz; some pairs
trip both). 104 were discharged as corroborated — Wikidata's own duplicate
items, 99 of which the `alias_identity` rule independently labels a match — and
793 became hard cannot-links, which refused 864 merges. More merges than
cannot-links, because 115 of the refusals are a *bare-name* record that would
otherwise have joined two wikidata items into one cluster: it now attaches to
whichever of them scored highest and the weaker edge is dropped.

Judged by the label rules above, the 864 refusals split 67 non-matches to 71
matches (53 `alias_identity`, 18 `dates_corroborated`), with 726 unlabellable.
So the refusals are slightly net-positive on the pairs there is evidence about,
and the ~71 lost links are the price of the rule being conservative:

- **A curated alias with no agreeing birth year does not discharge.** 37 of the
  refused alias-identity pairs have no birth year on one side and 33 disagree
  on it. Wikidata lists exonyms and near-namesakes as aliases too, so the
  alias alone is not enough — but "neither side contradicts" would be a
  defensible weaker rule, and it is where the 71 would mostly come back.
- **An identical name and one agreeing birth year does not discharge.** 312 of
  the refusals look like that (`aarne mannik|Q139027755` against
  `aarne mannik|Q12358097`, both born 1947, neither with an alias). Some are
  duplicate items and some are two people born the same year, and nothing in
  the corpus separates them.

Refusals are counted in the pass's output, not dropped: a cannot-link firing on
a pair the model scored 1.00 — 526 of the 864 did — is a report about the
model, not just about the pair.

## Where the labels come from

No pair was judged by hand. Each label comes from evidence outside the
matcher, and is refused when two rules disagree:

| provenance | label | rule | pairs |
|---|---|---|---|
| `dates_corroborated` | match | birth *and* death year agree, backed by >= 2 distinct sources | 6,303 |
| `alias_identity` | match | one record's name is a curated wikidata alias of the other, and the two names differ by more than word order | 1,162 |
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
bounds `L` from below, and the bound taken as an equality gives **L = 0.0209**.

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
- **The year columns' weights rest on very little.** Excluding the date-derived
  labels leaves only `alias_identity` to supply matches, and few of those pairs
  carry dates on both sides: `m` for `birth_year` is fitted on 90 informative
  observations and `death_year` on 30, against 3,461 for `given`. Jeffreys
  smoothing keeps the resulting weights bounded and they land where intuition
  says they should (about -5.7 bits for a conflict, +6.2 for an exact match),
  but they would move on a modest amount of new evidence. The corpus also
  offers no date-independent way to check whether "more than a decade apart" is
  the right place to put the line.
- **Precision on the pairs no rule can label is lower than the table.** Spot-
  checking the auto-link set by hand turns up roughly one wrong pair in seven,
  against the one in fifty the labelled population reports. Both are a long way
  from the four in five the old scorer produced, but the measured figure is the
  optimistic end of the range.
- **Recall on `alias_identity` is very poor (0.03).** What remains in that
  stratum after excluding mere word-order differences is the genuinely hard
  residue: stage names, exonyms and transliterations. Most are not reachable by
  surname blocking at all, so the model never sees the pair to score it. That
  is a blocking limitation, not a scoring one, and the number to beat if
  blocking is revisited.
