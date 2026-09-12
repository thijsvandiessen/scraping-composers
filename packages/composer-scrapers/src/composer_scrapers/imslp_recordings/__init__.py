"""IMSLP's commercial recordings — what the catalogue has been recorded on.

IMSLP already gives this project two sources: :mod:`..imslp` (its people index)
and :mod:`..imslp_works` (work pages, scoped to composers gold already knows).
Neither reads the third thing IMSLP holds, which is the one gold is short of.

Gold admits a person on **credits** — a ``recording_participants`` or
``concert_participants`` row that resolved to an entity — and not on appearing
in some source's artist index. Appearing in an index is not evidence anyone
played anything. So a source that lists performers earns nothing, and a source
that says *who played what on which release* earns everything, which is what the
26,933 pages in ``Category:Pages with commercial recordings`` say: per work, the
albums it appears on, the tracks of it on each, and everyone credited.

It says it about repertoire nothing else here reaches. The label catalogues and
orchestra archives cover the centre of the canon; this covers the tail, because
IMSLP's tail *is* IMSLP.

Two documents come out, and the split is the one :mod:`..decca` uses:

* a **work mention per (page, album)**, whose ``raw`` is shaped for
  ``composer_warehouse.recordings.derive`` — that pass keys on ``_kind`` and
  ``record_key`` alone and is otherwise source-agnostic, so nothing downstream
  needs to learn about this source to turn these into recordings;
* an **entity per credited name**, yielded last, because a performer's
  profession is settled by every credit they appear under and the last of those
  is not known until the sweep ends.

:mod:`.fetch` walks the category through ``api.php``, :mod:`.recordings` reads
one page's ``JGCommRec`` payload, and :mod:`.artists` accumulates the people.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime

from .. import EntityDocument, RefreshCadence, SourceAdapter, SourceClaim, WorkMentionDocument
from .artists import Person, Roster
from .fetch import BASE_URL, iter_recording_pages
from .recordings import Album, WorkPage, commercial_recordings, work_page

log = logging.getLogger(__name__)

__all__ = ["BASE_URL", "ImslpRecordingsAdapter"]


class ImslpRecordingsAdapter(SourceAdapter):
    """Every commercial recording IMSLP lists, and everyone credited on one."""

    name = "imslp_recordings"
    base_url = BASE_URL
    cadence = RefreshCadence.YEARLY

    def fetch(self, max_pages: int | None = None) -> Iterator[EntityDocument | WorkMentionDocument]:
        """The category, as work mentions first and the people on them after.

        ``max_pages`` caps the number of work pages read, for smoke runs. A
        capped run's roster is capped with it — unlike :mod:`..decca`, there is
        no separate artist index to read in full, because IMSLP publishes none.

        Mentions are yielded as each page lands rather than collected: a sweep
        this long will sometimes die partway, and what it has already read
        should still be a usable snapshot.
        """
        ingested_at = datetime.now(UTC)
        roster = Roster()
        mentions = 0
        pages = 0
        for pageid, page_title, url, document in iter_recording_pages(max_pages=max_pages):
            albums = commercial_recordings(document)
            if not albums:
                continue
            pages += 1
            page = work_page(pageid, page_title, url)
            for album in albums:
                for credit in album.credits:
                    roster.add(credit)
                mentions += 1
                yield _mention_document(album, page, ingested_at)
        for person in roster:
            yield _person_document(person, ingested_at)
        log.info(
            "imslp_recordings: %d work mentions over %d pages with recordings, %d artists",
            mentions,
            pages,
            len(roster),
        )


def _mention_document(album: Album, page: WorkPage, ingested_at: datetime) -> WorkMentionDocument:
    """One work on one release.

    The ``raw`` payload is what ``recordings/derive`` reads, and it is shaped for
    it: ``record_key`` is the album, so a release listed on ninety work pages
    folds into one recording rather than ninety. ``artists`` carries only the
    performers — a lyricist named in the same credit line wrote the words and did
    not play, so they stay in ``credit_line`` and out of the participants, the
    way :mod:`..decca` keeps composers out of its own.

    ``composer`` is passed on in IMSLP's own "Surname, Given" order, which is
    what :mod:`..imslp_works` and the Concertgebouw sources already feed in and
    what ``persons.extract.parse_name`` reads either way round.
    """
    return WorkMentionDocument(
        id=f"{page.page_id}#{album.album_id}",
        url=page.url,
        source_name=ImslpRecordingsAdapter.name,
        ingested_at=ingested_at,
        title=page.title,
        composer=page.composer,
        raw={
            "_source": "scraper",
            "_kind": "recording",
            "record_key": str(album.album_id),
            "title": album.title,
            "release_date": None,
            "label": None,
            "catalogue_number": album.catalogue_number,
            "format": None,
            "url": page.url,
            "artists": [
                {"name": credit.name, "role": credit.role, "discipline": credit.discipline}
                for credit in album.credits
            ],
            "album_id": album.album_id,
            "cover_url": album.cover_url,
            "credit_line": album.credit_line,
            "page_id": page.page_id,
            "page_title": page.page_title,
            # The tracks of *this work* on the album, not the album's whole
            # listing — which is what makes them the work-to-release link.
            "tracks": [
                {"description": track.description, "duration_s": track.duration_s} for track in album.tracks
            ],
        },
    )


def _person_document(person: Person, ingested_at: datetime) -> EntityDocument:
    """One credited artist, with the claims their credits support and no more."""
    claims: list[SourceClaim] = []
    profession = person.profession
    if profession is not None:
        claims.append(SourceClaim("has_profession", "profession", profession))
    claims += [SourceClaim("performs_as", value=discipline) for discipline in person.performs_as]
    return EntityDocument(
        id=person.external_id,
        url=None,
        source_name=ImslpRecordingsAdapter.name,
        ingested_at=ingested_at,
        name=person.name,
        kind=person.kind,
        raw={
            "credited_as": person.performs_as,
            "conductor": person.conducted,
            "albums": person.albums,
        },
        claims=tuple(claims),
    )
