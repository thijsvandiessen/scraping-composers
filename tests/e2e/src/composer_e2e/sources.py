"""Synthetic stand-ins for real sources: same names, same document shapes, no network.

Each adapter takes the name of the real scraper it replaces and yields documents
shaped exactly as that scraper's do, because the silver passes key on both:
``derive_concerts`` picks its payload reader by source name (``berlinphil``),
``derive_recordings`` reads the ``_kind: "recording"`` scraper payload (``decca``),
and ingest folds a ``wikidata.org/wiki/Q…`` URL into the entity's dedup key
(``wikidata``). Nothing here goes through the LLM extract path.

The dataset is small and every name in it is there to exercise one rule:

- Beethoven composed three mentioned works (composer rule, and the API's
  three-work bar for listing a composer) and arrives twice —
  once from wikidata with a QID, once bare from the programmes — so the
  dedupe pass has a real pair to link and promote has a duplicate to collapse.
- Mahler conducts two concerts; Mutter solos on two concerts and two recordings;
  the Synthetic Philharmonic plays on all of them (performer/ensemble credits).
- Guest, One-Off conducts once — below ``min_concert_appearances`` — and is
  dropped, until edition 2 brings a second concert and promotes them.
- Nobody, Obscure and the Listed-Only Consort are listed but credited nowhere.

``COMPOSER_E2E_EDITION`` (default 1) selects which fetch this is: edition 2 is a
later run of the same sources, with one concert added and one fact changed.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, ClassVar

from composer_schema import EntityDocument, SourceAdapter, SourceClaim, WorkMentionDocument

__all__ = ["EDITION_ENV", "SYNTHETIC_SOURCES", "edition"]

EDITION_ENV = "COMPOSER_E2E_EDITION"

_INGESTED_AT = datetime(2026, 1, 1, tzinfo=UTC)

_ORCHESTRA = "Synthetic Philharmonic"


def edition() -> int:
    return int(os.environ.get(EDITION_ENV, "1"))


def _composer(qid: str, name: str, *claims: SourceClaim) -> EntityDocument:
    return EntityDocument(
        id=qid,
        url=f"https://www.wikidata.org/wiki/{qid}",
        source_name="wikidata",
        ingested_at=_INGESTED_AT,
        name=name,
        raw={"item": qid, "itemLabel": name},
        claims=(SourceClaim("has_profession", "profession", "composer"), *claims),
    )


class SyntheticWikidata(SourceAdapter):
    """Composers with a QID, as the wikidata scraper yields them."""

    name: ClassVar[str] = "wikidata"
    base_url: ClassVar[str] = "https://www.wikidata.org"

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument]:
        # edition 2 changes the birthplace: the new value must reach silver and gold
        birthplace = "Bonn" if edition() >= 2 else "Cologne"
        yield _composer(
            "Q255",
            "Ludwig van Beethoven",
            SourceClaim("born_on", value="1770-12-17"),
            SourceClaim("died_on", value="1827-03-26"),
            SourceClaim("born_in", "place", birthplace),
        )
        yield _composer(
            "Q7304",
            "Gustav Mahler",
            SourceClaim("born_on", value="1860-07-07"),
            SourceClaim("died_on", value="1911-05-18"),
            SourceClaim("born_in", "place", "Kaliště"),
        )
        yield _composer("Q999001", "Nobody, Obscure", SourceClaim("born_in", "place", "Atlantis"))


#: (concert id, date, conductor, soloist or None, [(composer, title), ...])
_Concert = tuple[str, str, str, str | None, list[tuple[str, str]]]

_CONCERTS: list[_Concert] = [
    (
        "c1",
        "2024-01-10",
        "Gustav Mahler",
        "Anne-Sophie Mutter",
        [
            ("Ludwig van Beethoven", "Violin Concerto in D major, Op. 61"),
            ("Ludwig van Beethoven", "Symphony No. 5 in C minor, Op. 67"),
        ],
    ),
    (
        "c2",
        "2024-02-14",
        "Gustav Mahler",
        "Anne-Sophie Mutter",
        [
            ("Ludwig van Beethoven", "Violin Concerto in D major, Op. 61"),
            ("Gustav Mahler", "Symphony No. 1 in D major"),
        ],
    ),
    (
        "c3",
        "2024-03-20",
        "Guest, One-Off",
        None,
        [("Ludwig van Beethoven", "Symphony No. 5 in C minor, Op. 67")],
    ),
]

_EDITION_2_CONCERTS: list[_Concert] = [
    (
        "c4",
        "2024-09-05",
        "Guest, One-Off",
        None,
        [("Gustav Mahler", "Symphony No. 1 in D major")],
    ),
]


def _performance(concert: _Concert, index: int, composer: str, title: str) -> WorkMentionDocument:
    concert_id, date, conductor, soloist, _ = concert
    url = f"https://www.digitalconcerthall.com/en/concert/{concert_id}"
    raw: dict[str, Any] = {
        "concert_id": concert_id,
        "work_id": f"{concert_id}-w{index}",
        "title": title,
        "url": url,
        "date": date,
        "season": f"{date[:4]}",
        "composers": [composer],
        "conductors": [conductor],
        "soloists": [{"name": soloist, "discipline": "violin"}] if soloist else [],
        "arrangers": [],
        "ensembles": [_ORCHESTRA],
        "periods": [],
        "is_encore": False,
    }
    return WorkMentionDocument(
        id=f"perf:{concert_id}-w{index}",
        url=url,
        source_name="berlinphil",
        ingested_at=_INGESTED_AT,
        title=title,
        composer=composer,
        raw=raw,
    )


def _artist(artist_id: str, name: str, kind: str, *claims: SourceClaim) -> EntityDocument:
    return EntityDocument(
        id=f"artist:{artist_id}",
        url=f"https://www.digitalconcerthall.com/en/artist/{artist_id}",
        source_name="berlinphil",
        ingested_at=_INGESTED_AT,
        name=name,
        kind=kind,
        raw={"id": artist_id, "name": name},
        claims=claims,
    )


class SyntheticBerlinPhil(SourceAdapter):
    """A concert archive: one work mention per performed work, then its artists."""

    name: ClassVar[str] = "berlinphil"
    base_url: ClassVar[str] = "https://www.digitalconcerthall.com"

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        concerts = _CONCERTS + (_EDITION_2_CONCERTS if edition() >= 2 else [])
        for concert in concerts:
            for index, (composer, title) in enumerate(concert[4]):
                yield _performance(concert, index, composer, title)
        yield _artist(
            "a1", "Gustav Mahler", "person", SourceClaim("has_profession", "profession", "conductor")
        )
        yield _artist(
            "a2",
            "Anne-Sophie Mutter",
            "person",
            SourceClaim("has_profession", "profession", "soloist"),
            SourceClaim("performs_as", value="violin"),
        )
        yield _artist(
            "a3", "Guest, One-Off", "person", SourceClaim("has_profession", "profession", "conductor")
        )
        yield _artist("a4", _ORCHESTRA, "ensemble")
        yield _artist("a5", "Listed-Only Consort", "ensemble")


#: (product id, title, [(composer, work title), ...])
_RELEASES: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "p1",
        "Beethoven: Violin Concerto",
        [
            ("Ludwig van Beethoven", "Violin Concerto in D major, Op. 61"),
            ("Ludwig van Beethoven", "Coriolan Overture, Op. 62"),
        ],
    ),
    (
        "p2",
        "Beethoven & Mahler",
        [
            ("Ludwig van Beethoven", "Symphony No. 5 in C minor, Op. 67"),
            ("Gustav Mahler", "Symphony No. 1 in D major"),
        ],
    ),
]


class SyntheticDecca(SourceAdapter):
    """A label catalogue: one work mention per work on a release, in the
    normalized ``_kind: "recording"`` scraper payload."""

    name: ClassVar[str] = "decca"
    base_url: ClassVar[str] = "https://www.deccaclassics.com"

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        for product_id, album, works in _RELEASES:
            url = f"{self.base_url}/products/{product_id}"
            for index, (composer, title) in enumerate(works):
                yield WorkMentionDocument(
                    id=f"/products/{product_id}/works/{index}",
                    url=url,
                    source_name=self.name,
                    ingested_at=_INGESTED_AT,
                    title=title,
                    composer=composer,
                    raw={
                        "_source": "scraper",
                        "_kind": "recording",
                        "record_key": product_id,
                        "title": album,
                        "release_date": "2020-05-01",
                        "label": "Decca",
                        "catalogue_number": f"483 {product_id}",
                        "format": "CD",
                        "url": url,
                        "artists": [
                            {"name": "Anne-Sophie Mutter", "role": "soloist", "discipline": "violin"},
                            {"name": _ORCHESTRA, "role": "ensemble", "discipline": None},
                        ],
                        "composers": [composer],
                    },
                )
        # The label's artist pages carry life dates — the corroboration the
        # dedupe pass needs to link these to the wikidata QID twins.
        yield self._artist("beethoven", "Ludwig van Beethoven", "composer", "1770-12-17", "1827-03-26")
        yield self._artist("mahler", "Gustav Mahler", "composer", "1860-07-07", "1911-05-18")
        yield self._artist("mutter", "Anne-Sophie Mutter", "soloist", "1963-06-29", None)

    def _artist(self, slug: str, name: str, profession: str, born: str, died: str | None) -> EntityDocument:
        claims = [SourceClaim("has_profession", "profession", profession), SourceClaim("born_on", value=born)]
        if died:
            claims.append(SourceClaim("died_on", value=died))
        return EntityDocument(
            id=f"/artists/{slug}",
            url=f"{self.base_url}/artists/{slug}",
            source_name=self.name,
            ingested_at=_INGESTED_AT,
            name=name,
            raw={"slug": slug, "functions": [profession]},
            claims=tuple(claims),
        )


SYNTHETIC_SOURCES: tuple[SourceAdapter, ...] = (SyntheticWikidata(), SyntheticBerlinPhil(), SyntheticDecca())
