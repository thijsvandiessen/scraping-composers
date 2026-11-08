# classicapedia

[classicapedia.com](https://classicapedia.com/en/) is a free bilingual encyclopedia: about 22,400 biographies,
282 opera houses and concert halls, and 156 orchestras. This source reads the English pages for facts: years,
birthplace, professions, teacher and pupil links, works, and each venue's Wikipedia article.

## How far to trust it

It is a **lower-trust source**. It is one author's personal project, written from reference works it does not
cite. No teacher link carries a citation and no person carries an identifier. Its value is reach: on the composers
compared, it often names teachers Wikidata lacks.

Provenance is per claim, so a link only this source states stays identifiable. Treat such a link as a lead rather
than a fact until a second source backs it.

The site's biographies are its own writing and may only be quoted with credit, so no prose is stored: `raw` holds
structured facts and the page URL.

## Crawl

The English sitemap drives it: `b/` biographies, `t/` opera houses, `s/` concert halls (`t/` and `s/` together are
the `auditorios.html` listing), `o/` orchestras. Stories, instruments and forms are not read. `assets/indice.js`,
the search box's data, maps each slug to a canonical name without fetching that person's page.

robots.txt sets no crawl-delay. At one request a second a full crawl takes about six and a half hours, then a few
hundred Wikipedia and Wikidata lookups and searches. These use the MediaWiki APIs, not WDQS. `--max-pages` caps each
sitemap section separately, so a small run still reaches every kind of page.

The documents are only produced after the crawl, because a teacher's identity is not known until their own page
has been read.

## Teacher lists are capped

A biography lists at most 40 pupils and the `maestros-*` pages at most 60, while Nadia Boulanger's stated count is
208. Pupils past the cap still name their teacher on their own page. So the complete picture is the union of
`student_of` and `teacher_of` over all pages, and the `maestros` pages are not read at all. Each claim is stored as
its own page asserts it, so a link seen from both ends is two claims.

## Identity

- **Names.** A linked person is named as the search index names them, not as the link text does, so a teacher lands
  on the entity their own record creates. A trailing `(i)` and a stray `and`, left from the lists' origins, are removed.
- **Same-name people.** About 400 people share a name with someone on the site, three Johann Strausses among them,
  and a person without a QID is keyed on the label alone. Such a person is dropped, with every link naming them,
  unless a QID is confirmed. They are searched for on Wikidata by name, and pinned if the page states both years and
  exactly one human item fits both within one. An item two pages reach, or one a page already cites, pins nobody. The
  numbers are in the run's log.
- **QIDs for people.** Some pages cite a Wikipedia article, as the source of their curiosities section rather than as
  a statement of identity. It only pins the person if the page cites exactly one article, whose item is a human with
  years fitting within one. A wrong QID would become a hard cannot-link in person dedupe.
- **Pinned people take Wikidata's English label,** so that they reach the entity the `wikidata` source creates.
- **Unlinked names** are never claimed, and remain in `raw`. About a third of the relations are unlinked, Liszt's
  pupils among them since he has no page. Matching them to pages by name was tried on the full crawl: of 388 matches,
  191 were the page's own subject and 47 had a teacher dead before the pupil's birth (Luise Walker's guitar teacher
  Heinrich Albert became the baroque composer), against none of 11,301 for linked names.
- **Dates that cannot be a life** state no years: "1904–1904" (Dallapiccola), "384–322" (BC), "1846–1996" (a typo). The
  page's string stays in `raw["dates"]`.
- **Works and shared names.** A work mention finds its composer by label alone, so a composer on a shared name has no
  mentions even when pinned.

The cost is still real: a same-name person with no dates, or dated only by a birth year, stays dropped, and those
recovered have no work mentions.

## Works

`#catalogo` is used when the page has one, otherwise `#obras`. Summary lines such as "Approximately 78 original
works for guitar" are skipped.

**Keys are unreliable.** The trailing key letter loses accidentals: the Eroica reads `Symphony no.3 Eroica, E`,
though it is in E flat. The letter is cut from the title the matcher sees and kept verbatim in
`raw["listed_as"]`.

## Venues and orchestras

The `p.mas-info` link is the page's Wikipedia article, resolved to a QID. A pinned record's `url` is its Wikidata
page, with the site's in `raw["page_url"]`. All 19 pages sampled during development resolved.

The site files opera companies under opera houses (Welsh National Opera, Scottish Opera), so Wikidata's direct P31
decides: a building stays a `venue`, and a company with no building class becomes an `ensemble`. Anything else stays
a `venue` and is counted in the log (Mozarteum University is one), with its classes in `raw["instance_of"]`.

Facts are free-form and their labels vary, so all of them stay in `raw["facts"]`. Only `opened_on`, `founded_on`,
`based_in` and `wikipedia_article` become claims. City names are as the page spells them, which is not always English
("Roma", "Río de Janeiro").
