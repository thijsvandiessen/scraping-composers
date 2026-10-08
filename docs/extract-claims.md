# Publisher catalogues via the crawler

[`boosey`](sources/boosey.md), [`baerenreiter`](sources/baerenreiter.md) and [`henle`](sources/henle.md)
are hand-written adapters for one publisher each.
Every other publisher's catalogue is reachable with the generic crawler and the `claims` extract kind,
with no code at all — which is what `claims` is for: it records whatever a page
states, so a site nobody wrote a parser for still contributes.

A sheet-music page states two things at once, and both land on the work:

- facts about the piece — `written_for`, `includes_instrument`, `in_key`,
  `catalogue_number`, `composed_in`, `duration_minutes`;
- facts about the printed edition of it — `published_by`, `edited_by`,
  `fingering_by`, `edition_type`, `ismn`, `page_count`, `difficulty_level`.

Edition facts are claims on the *work*, not on an "edition" entity of their own,
so two editions of one piece merge. That is a deliberate trade: edition dedup is
a problem in its own right and nothing downstream needs it solved yet.

## Scoring is stored twice

"Which works are for piano" is the question a publisher's catalogue is built to
answer — Bärenreiter's own navigation offers *works for string orchestra* as a
facet — and free text cannot answer it: the same scoring is written `for piano
solo`, `Klavier zu vier Händen`, `Piano, 4 hands`. So the stated scoring is kept
twice over:

- verbatim, as an `orchestration` literal — what the page actually said;
- folded onto canonical *scoring categories*, as one `written_for` claim each,
  pointing at an `instrumentation` entity.

The category, not the instrument, is the unit: `piano`, `string orchestra` and
`violin and piano` are each one entity, because that is how the catalogues
themselves are organised and because a string orchestra is not a list of
instruments. A category that names instruments rather than an ensemble also emits
them (`instrumentation.py`'s `CONTAINS`), so a violin sonata still answers "works
for piano":

```
Beethoven: Violin Sonata no. 5  --orchestration--> "Violine und Klavier"   (literal)
                                --written_for----> violin and piano        (instrumentation)
                                --written_for----> violin                  (instrumentation)
                                --written_for----> piano                   (instrumentation)
```

Nothing is guessed. A phrase no category is recognised in keeps its literal and is
*counted*, and the extract run's log names the commonest misses:

```
claims: 412 pages, 480 chunks, 0 retried, 0 failed, 3106 claims
  (new predicates: plate_number(88); unrecognised scoring: 12 solo voices(31), …)
```

Those two lists are the review queue: fold a recurring predicate into
`vocabulary.py`'s `ALIASES` and a recurring scoring phrase into
`instrumentation.py`'s `CATEGORIES`, and the next run curates it.

## Orchestral shorthand

An orchestral catalogue does not print prose. It prints a positional notation, in
two dialects that say the same thing (`shorthand.py`):

| | Beethoven's Fifth |
| --- | --- |
| Chester/Novello | `3223 / 2230 / timp.perc / str[8]` |
| Boosey & Hawkes | `3.2.2.3 - 2.2.3.0 - timp - strings[6]` |
| Bärenreiter | `2,2,2,2 – 2,2,3,0 – Pk,Schlg – Str` (German abbreviations) |

The first two sections are *positional*: four counts standing for the four standing
woodwind desks (flute, oboe, clarinet, bassoon) and the four brass ones (horn,
trumpet, trombone, tuba), in score order. A parenthetical says how many of a desk's
players double on something else — `3(pic)`, `4(2pic)`, `3(III=picc)`,
`4(III,IV=picc)` and `Dcl(=Ebcl)` all occur — and `[N]` counts the string parts.

A symphony is not "a work for flute" the way a sonata is a work for piano, so the
two are different predicates:

```
Beethoven: Symphony No. 5  --orchestration-------> "3.2.2.3 - 2.2.3.0 - timp - strings[6]"
                           --written_for---------> orchestra
                           --includes_instrument-> flute, oboe, clarinet, bassoon,
                                                   horn, trumpet, trombone,
                                                   timpani, strings
```

Both point at the same `instrumentation` entities, so `piano` is one node whether a
sonata is for it or a symphony contains it, and "everything involving a piano" is
the union of the two predicates. The same split applies to a named ensemble:
`string quartet` is what the work is `written_for`, and violin/viola/cello are what
it `includes_instrument` (`instrumentation.py`'s `MEMBERS`).

Player counts and the string-part number are structure rather than facts to
compare, so they travel in the record's `raw` payload under `"scoring"` instead of
becoming a claim each:

```json
{"instruments": ["flute", "oboe", "…"],
 "counts": {"flute": 3, "oboe": 2, "clarinet": 2, "bassoon": 3, "…": 1},
 "string_parts": 6}
```

Detection is deliberately strict, because a false positive files a work under an
ensemble it was never written for: a shorthand must name strings **and** carry at
least one four-count section. Prose never does — it always names an instrument
after a count — so `flute, 2 oboes, …, strings` stays on the prose path and is
recognised as nothing rather than as a work for flute.

## Crawl recipes

A recipe goes in via the dashboard's **New crawl** form (or `PUT
/admin/v1/crawls/<name>`) rather than `CRAWL_REGISTRY` — a code-registered config
wins over the stored one and is read-only in the dashboard, so tuning an allow
pattern would mean a commit each time.

Henle and Bärenreiter have no recipe: a crawl of either stored next to nothing
(Bärenreiter's pages render client-side; Henle's facts sit in shop markup the
`claims` prompt did not read), so each has an adapter of its own (see [Henle](sources/henle.md) and
[Bärenreiter](sources/baerenreiter.md)).

Two things to check before a wide run:

- **Facet URLs are often not crawlable.** Search and filter paths are usually
  absent from `sitemap.xml` and frequently `Disallow`ed in `robots.txt`;
  `respect_robots` defaults to on, so those pages will simply be skipped. Read
  the site's `robots.txt` first, and fall back to sitemap-driven detail pages if
  the facets are excluded — the detail pages state the scoring themselves, so the
  facet listings are a convenience, not a requirement.
- **These are commercial catalogues.** Keep the request delay polite and check
  the site's terms of use before running anything at scale.

## Enabling this on an existing crawl costs one re-extract

The `claims` system prompt is part of both the answer-cache key and the
extraction ledger's fingerprint (see [Not analysing the same page
twice](../README.md#not-analysing-the-same-page-twice)), so widening it invalidates every
cached `claims` answer: the next `extract` run on a crawl with `claims` enabled
sends every page back through the model once. `concerts` and `recordings` are
unaffected — their prompts did not change, so their caches stay warm.
