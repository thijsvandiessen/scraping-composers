# IMSLP

Three sources read IMSLP: `imslp` (people), `imslp_works` (the work catalogue) and
`imslp_recordings` (commercial recordings).

## API quirks

The endpoint (`/imslpscripts/API.ISCR.php`) takes its parameters as a single
slash-separated string, returns rows keyed by stringified indices alongside a
`metadata` entry holding the pagination flag, and embeds names in MediaWiki
category titles (`Category:Beethoven, Ludwig van`). `composer_scrapers/imslp/`
handles all of this; retries and the polite request delay come from the
`composer_http.SourceSession` it fetches through.

It serves two lists and IMSLP documents no others: `type=1` is the people, read
by `imslp`, and `type=2` is the ~267,000 works, read by `imslp_works`. Both
arrive in the shape above, so `imslp/fetch.py::worklist_page` is shared between
the two sources rather than written twice.

## Works: the whole catalogue, enriched in a second pass

`imslp_works` used to be the one source here that discovered *who* to scrape
from somewhere other than the source: it read gold's composer list, guessed
each composer's IMSLP category URL, and crawled the work lists under it. That
capped it at whoever gold already knew — 7,256 works.

The `type=2` worklist replaces all of that. Every work IMSLP lists arrives in
~267 requests carrying its composer, title, IMSLP catalogue number and page id,
so there is no category to resolve, no pagination to follow, and no scoping.

The **detail pages are now a second pass** over that spine, and the split is the
thing to understand before changing this source:

- The bulk row alone produces both documents and the `composed_by` /
  `catalogue_number` claims. A work is reported whether or not anyone read its
  page.
- The page adds `has_scoring`, `has_key`, `has_genre` and `composed_in`. At one
  request per work, sweeping all 267k is tens of hours, so `--max-pages` bounds
  it — and bounds *only* it. `raw["enriched"]` records whether the page was
  actually read, which is what distinguishes a work with no stated scoring from
  one nobody has looked at yet.

Detail pages come from `action=parse&prop=text`, not `/wiki/<Title>`: the
infobox is field-for-field identical in both (verified over six random works),
the parser output is 6.2KB against 13.8KB, and `imslp_recordings` requests the
same pages at the same URL — so the two share one `PageCache` mirror and
whichever sweep runs second gets the overlap free. The one thing parser output
lacks is a `<title>` tag, which is why `parse_work` takes the title as an
argument rather than reading it off the page.

`imslp_recordings` uses the *other* IMSLP API, the wiki's own `api.php`, and
that one has quirks of its own. It runs MediaWiki 1.18, so continuation comes
back under `query-continue` rather than the modern `continue`, an unknown page
id answers 200 with an `{"error": ...}` body rather than 404, and `action=parse`
takes one page per call — there is no batching to be had, and the IMSLP
extensions register no API action of their own.

## Commercial recordings

`imslp_recordings` reads `Category:Pages with commercial recordings` — 26,933
work pages naming the albums each work appears on, the tracks of it on each, and
everyone credited. It is the cheapest source of *credits* here, which is what
gold's person selection actually counts: a `recording_participants` row that
resolved to an entity is evidence someone played, where appearing in a source's
artist index is not.

Two things about it are worth knowing before changing it:

- **The data is not in the HTML you can see.** The "Commercial 💿" tab renders
  client-side from a `JGCommRec` JS global — but that global sits inside the
  *parser output*, so `action=parse&prop=text` serves it at 6.2KB against 13.8KB
  for the rendered page. It is not in the wikitext; `prop=revisions` gets you
  nothing.
- **Pages are addressed by id.** `list=categorymembers` returns a `pageid`
  beside every title and `action=parse` takes one, which is how the titles in
  this category (`'E spingole frangese!`, `1.X.1905 (Janáček, Leoš)`) stay out
  of a URL. The response names the page back, so the work title and its
  `(Surname, Given)` composer suffix come from the API rather than the listing.

A full sweep is ~27k calls at one per second — hours, so it goes through
`composer_http.PageCache` and an interrupted run resumes where it stopped.
