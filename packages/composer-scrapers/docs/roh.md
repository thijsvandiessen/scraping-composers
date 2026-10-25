# Royal Opera House: the composer is three levels above the performance

`roh` reads the Covent Garden performance database — every opera, ballet and
concert given at the Royal Opera House since the 1730s. It is the deepest
performance archive here and the only one reaching back to the eighteenth
century.

**The site's own search is the wrong way in.**
`SearchResults.aspx?searchtype=performance&genre=Opera` paginates 5,690 opera
performances twenty to a page (9,757 more for ballet), and neither the listing
nor the performance page behind it names a composer. Carmen, 14 January 1947,
records the company, the venue, the conductor, thirty-three cast members, the
translator and the programme it was catalogued from; it does not record Bizet.
Some 15,700 requests would yield no composers at all.

The composer is on the **work** page, in a labelled row, three levels up:

```
work.aspx?work=551          Composer: Giuseppe Verdi      <- the only level with one
  production.aspx?...=3775  Director: Elijah Moshinsky
    performance.aspx?...    Conductor, cast, venue, date
```

So enumeration goes through `performanceindex.aspx` instead — 27 letter pages,
no pagination, every work in the database with its genre — and the sweep
descends from there. That is **27 requests where the search needs 773** (285
pages of opera results plus 488 of ballet), and it
reaches every composer the archive holds. The index has 995 rows; 47 are
cross-references, leaving **948 works — 577 ballets, 287 operas, 62 concert
works** and a scatter of plays, poems and musicals. 926 of them name a composer,
340 distinct.

**A ballet's title is not a work by its composer.** "Adagio Hammerklavier" is
Hans van Manen's; Beethoven wrote the *Piano Sonata No. 29 in B-flat, Op. 106*
that the page names on the next row. So where a work page distinguishes a
`Music title:` it — not the staged title — is what pairs with the composer;
that is **661 of the 948**, so it is the common case, not an edge one. The index compounds the trap by printing one bracketed name after every
title: the composer for an opera, the *choreographer* for a ballet, with nothing
to say which. It is recorded and never read as a composer.

Two more things the markup hides:

- **The hierarchy is not strict.** Some performances hang off a work with no
  production between them — Aida's two 1988 concert excerpts — under a separate
  "Performances not linked to a production" table. A walk that only descends
  through productions drops them and looks complete doing it.
- **A production page never says which work it stages.** There is no link
  upward and no work id in the markup, so a production is only ever read as a
  child of the work that listed it.

Some index rows are cross-reference stubs rather than works ("The Barber of
Seville: see 'Il barbiere di Siviglia'"). Their wording varies too much to
filter on — "see", "See also", ": see", "- See" — so they are recognised by what
their page carries: nothing at all.

A person becomes a composer here only because a work page filed them under
`Composer`. Cast members get no profession at all — the cast column holds the
*part*, not the discipline ("Don José", "Tavern Dancer", "Harpsichord
continuo"), and a 1947 Carmen credits opera singers and ballet dancers in the
same table.

**Which rows name a person is an allowlist per level**, read off the pages
rather than guessed, because the inverse rule ("anything unrecognised is a
credit") files `Opera in four acts` as a human being the first time a new label
appears. The staging tier proves the point twice over: a hundred sampled
productions produced a dozen labels used exactly once (`Cloth designer`,
`Incidental dances`, `Artistic Collaborator`), and two co-production partners
are filed under labels that are the partner's *own name* — `Theater an der
Wien, Vienna` as the `<th>`. Nothing is lost to an unrecognised label, since
every non-credit row is kept as a field regardless; the adapter just logs what
it did not recognise, which is how the lists grow. `Additional music: Ian Page
(recitatives)` is the find that justifies reading the staging tier at all: a
production can commission a composer the work page never names.

`robots.txt` disallows only `/search/autocomplete*`, which this source does not
touch, and asks `User-agent: *` for `Crawl-delay: 180` — three minutes, which
over ~18,000 pages is three weeks for one sweep. The adapter uses **five
seconds**, a deliberate departure recorded in `roh/fetch.py`; it is still ten
times slower than the other HTML sources here. (~18,000 = 995 works, ~1,140
productions and ~15,700 nights, measured over a 300-work sample and agreeing
with the site's own search totals of 5,690 opera and 9,757 ballet performances
— about 25 hours.) Every page goes through
`composer_http.PageCache`, which is what makes a run of that length survivable:
the tiers are drained in order, so every composer is known at ~1,000 pages of
the ~18,000, and a run that dies at hour twenty resumes where it stopped.
