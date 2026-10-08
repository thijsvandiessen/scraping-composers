# harmonia mundi: sitemaps, because the REST API is off limits

`harmoniamundi` replaced a crawl4ai + LLM config over the same site. The crawl
produced 985 recording mentions of which every one had a null release date and
a null catalogue number — both printed on every album page — plus invented roles
(a baritone as `soprano`), `record_key`s pointing at artist pages so no work
ever folded into a release, and album pages read as *concerts*, one dated 1907.

The site is WordPress with a hand-filled template, so every one of those fields
is in a named class: `div.feature.ref` is the catalogue number,
`time.release_date` the release month, `div.feature.cd_number` the *format*
(`"1 CD"`, `"Digital"` — not a disc count), and `div.album_humans` lists each
person with the role they were credited under and a link to their page when they
have one. A person becomes a composer here only because the label filed them
under Composers.

Being WordPress, it also answers `/wp-json/wp/v2/albums` with the whole post
list — a far cheaper enumerator than the sitemaps, and **`Disallow`ed**: the
`User-agent: *` group is `Allow: /` with four exceptions and `/wp-json` is one
of them. So enumeration goes through `sitemap_index.xml`, which is allowed and
just as complete (1752 album, 104 artist and 129 composer pages, matching the
site's own totals), and every field is read from the rendered page. Note also
`Content-Signal: ai-train=no, use=reference` — a catalogue index is reference
use, and this trains nothing.

One field is genuinely free text: the "Contents" tracklist, a rich-text box
whose conventions have drifted across thirty years of releases. It is read from
the two signals that hold across the catalogue — life dates in brackets open a
composer, a leading bullet marks a track — and `<strong>` is deliberately
ignored, because some albums bold every track and others bold the work. The
verbatim text rides along in every mention's `raw`, so a better parse can be
derived later without re-fetching 2000 pages.
