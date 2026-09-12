"""Tests for the silver recording-derivation pass."""

from composer_models import (
    Concert,
    Entity,
    Recording,
    RecordingParticipant,
    RecordingWork,
)
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.recordings import derive_recordings
from composer_warehouse.testing import FakeSource, ensemble, ingest_source, perf_mention, person
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _recording_raw(catalogue: str | None = "486 1234") -> dict[str, object]:
    """The normalized recording payload composer_extract writes."""
    return {
        "_source": "llm",
        "_kind": "recording",
        "record_key": f"https://dg.example/album#{catalogue}" if catalogue else "https://dg.example/album",
        "url": "https://dg.example/album",
        "title": "Beethoven: Symphony No. 9",
        "release_date": "2024-03-15",
        "label": "Deutsche Grammophon",
        "catalogue_number": catalogue,
        "format": "CD",
        "artists": [
            {"name": "Simon Rattle", "role": "conductor", "discipline": None},
            {"name": "Janine Jansen", "role": "soloist", "discipline": "violin"},
        ],
    }


def _seed(session: Session, catalogue: str | None = "486 1234") -> None:
    raw = _recording_raw(catalogue)
    dg = FakeSource(
        records=(
            perf_mention(f"{raw['record_key']}#w0", "Symphony No. 9", "Beethoven", raw),
            perf_mention(f"{raw['record_key']}#w1", "Coriolan Overture", "Beethoven", raw),
            person("Simon Rattle", external_id="dg:rattle"),
            person("Janine Jansen", external_id="dg:jansen"),
        ),
        name="deutschegrammophon",
        base_url="https://dg.example",
    )
    ingest_source(session, dg)


def test_derive_recordings_groups_and_resolves(session: Session) -> None:
    _seed(session)
    stats = derive_recordings(session)

    recording = session.scalars(select(Recording)).one()  # both works grouped by record_key
    assert recording.external_key == "https://dg.example/album#486 1234"
    assert recording.title == "Beethoven: Symphony No. 9"
    assert recording.release_date == "2024-03-15"
    assert recording.label == "Deutsche Grammophon"
    assert recording.catalogue_number == "486 1234"
    assert recording.format == "CD"
    assert len(recording.works) == 2

    by_role = {(p.role, p.name): p for p in recording.participants}
    assert ("conductor", "Simon Rattle") in by_role
    assert by_role[("soloist", "Janine Jansen")].discipline == "violin"
    assert all(p.entity_id is not None for p in recording.participants)  # verbatim names resolve

    assert (stats.recordings, stats.participant_links) == (1, 2)
    assert stats.unresolved_participant_names == 0


def test_derive_recordings_key_falls_back_to_url(session: Session) -> None:
    _seed(session, catalogue=None)
    derive_recordings(session)
    recording = session.scalars(select(Recording)).one()
    assert recording.external_key == "https://dg.example/album"


def test_derive_concerts_ignores_recording_payloads(session: Session) -> None:
    """Recording mentions share the ``llm`` marker but must not become concerts."""
    _seed(session)
    derive_concerts(session)
    assert session.scalar(select(func.count(Concert.id))) == 0


def test_derive_recordings_resolves_ensemble_credits(session: Session) -> None:
    """An ensemble credit prefers the ``ensemble`` entity over a same-named person."""
    raw = _recording_raw()
    artists = list(raw["artists"])  # type: ignore[call-overload]
    artists.append({"name": "Berliner Philharmoniker", "role": "ensemble", "discipline": None})
    raw["artists"] = artists
    dg = FakeSource(
        records=(
            perf_mention(f"{raw['record_key']}#w0", "Symphony No. 9", "Beethoven", raw),
            person("Simon Rattle", external_id="dg:rattle"),
            person("Janine Jansen", external_id="dg:jansen"),
            ensemble("Berliner Philharmoniker", external_id="dg:ens"),
        ),
        name="deutschegrammophon",
        base_url="https://dg.example",
    )
    ingest_source(session, dg)

    stats = derive_recordings(session)

    orchestra = session.scalars(select(Entity).where(Entity.label == "Berliner Philharmoniker")).one()
    recording = session.scalars(select(Recording)).one()
    credit = next(p for p in recording.participants if p.role == "ensemble")
    assert credit.entity_id == orchestra.id
    assert stats.participant_links == 3


def _album(
    page: str,
    *,
    title: str = "Mahler – Symphony No. 9",
    artists: tuple[tuple[str, str], ...] = (("Osmo Vänskä", "conductor"),),
    **fields: str | None,
) -> dict[str, object]:
    """One page's view of an album. The same release seen on its review page and
    on a tag page differs only in ``record_key`` and in how much it fills in;
    ``fields`` carries the optional release details a thin listing omits."""
    return {
        "_source": "llm",
        "_kind": "recording",
        "record_key": page,
        "url": page,
        "title": title,
        "release_date": fields.get("release_date"),
        "label": fields.get("label"),
        "catalogue_number": fields.get("catalogue_number"),
        "format": fields.get("format"),
        "artists": [{"name": name, "role": role, "discipline": None} for name, role in artists],
    }


def _ingest_albums(session: Session, *albums: dict[str, object]) -> None:
    ingest_source(
        session,
        FakeSource(
            records=tuple(
                perf_mention(f"{a['record_key']}#w0", "Symphony No. 9", "Mahler", a) for a in albums
            ),
            name="theclassicreview",
            base_url="https://tcr.example",
        ),
    )


def test_derive_recordings_merges_the_same_album_across_pages(session: Session) -> None:
    """A review page and a tag page listing the same release are one recording:
    the fuller payload wins, and the thinner one fills the gaps it left."""
    _ingest_albums(
        session,
        _album(
            "https://tcr.example/review-mahler-9",
            artists=(("Osmo Vänskä", "conductor"), ("Minnesota Orchestra", "ensemble")),
            label="BIS Records",
            catalogue_number="BIS-2476",
            format="CD",
        ),
        _album("https://tcr.example/tag/osmo-vanska/#r0", release_date="2023-04-17"),
    )

    stats = derive_recordings(session)

    recording = session.scalars(select(Recording)).one()
    assert recording.external_key == "https://tcr.example/review-mahler-9"
    assert (recording.label, recording.catalogue_number, recording.format) == (
        "BIS Records",
        "BIS-2476",
        "CD",
    )
    assert recording.release_date == "2023-04-17"  # only the tag page had one
    assert len(recording.works) == 2  # both pages' mentions, no duplicates
    assert {p.name for p in recording.participants} == {"Osmo Vänskä", "Minnesota Orchestra"}
    assert (stats.recordings, stats.merged_duplicates) == (1, 1)


def test_derive_recordings_keeps_same_title_albums_with_different_performers(
    session: Session,
) -> None:
    """A round-up page lists many same-titled releases — different performers
    means different releases, so they must not collapse."""
    _ingest_albums(
        session,
        _album("https://tcr.example/top-10#r0", artists=(("Osmo Vänskä", "conductor"),)),
        _album("https://tcr.example/top-10#r1", artists=(("Ádám Fischer", "conductor"),)),
    )

    stats = derive_recordings(session)

    assert (stats.recordings, stats.merged_duplicates) == (2, 0)


def test_derive_recordings_keeps_conflicting_catalogue_numbers_apart(session: Session) -> None:
    """Two releases by the same performer are distinct when the label gave them
    distinct release ids — a shared performer alone must not merge them."""
    _ingest_albums(
        session,
        _album("https://tcr.example/review-a", catalogue_number="BIS-2476"),
        _album("https://tcr.example/review-b", catalogue_number="BIS-9999"),
    )

    stats = derive_recordings(session)

    assert (stats.recordings, stats.merged_duplicates) == (2, 0)


def test_derive_recordings_collapses_honorific_variants(session: Session) -> None:
    """Sources disagree on honorifics; the merged release keeps one credit."""
    _ingest_albums(
        session,
        _album(
            "https://tcr.example/review-mahler-9",
            artists=(("Sir Simon Rattle", "conductor"),),
            label="BR Klassik",
            catalogue_number="CD 900205",
        ),
        _album("https://tcr.example/tag/mahler/#r3", artists=(("Simon Rattle", "conductor"),)),
    )

    derive_recordings(session)

    recording = session.scalars(select(Recording)).one()
    assert [(p.role, p.name) for p in recording.participants] == [("conductor", "Sir Simon Rattle")]


def test_derive_recordings_is_rerunnable(session: Session) -> None:
    _seed(session)
    first = derive_recordings(session)
    second = derive_recordings(session)  # full rebuild: same result, no leftovers

    assert first == second
    assert session.scalar(select(func.count(Recording.id))) == 1
    assert session.scalar(select(func.count(RecordingWork.id))) == 2
    assert session.scalar(select(func.count(RecordingParticipant.id))) == 2


def _scraped_raw() -> dict[str, object]:
    """The same normalized payload, written by a scraper instead of an LLM.

    ``composer_scrapers.decca`` reads its release context out of a structured
    API rather than a model's reading of a page, but what it writes into
    ``raw_work_mentions.raw`` is the shape this pass already understands.
    """
    return {
        "_source": "scraper",
        "_kind": "recording",
        "record_key": "product:95920",
        "url": "https://www.deccaclassics.com/en/catalogue/products/verdi-ballo-7",
        "title": "VERDI Un ballo in maschera Karajan",
        "release_date": "2006-03-14",
        "label": "Deutsche Grammophon (DG)",
        "catalogue_number": "00028947756415",
        "format": "CD album",
        "artists": [
            {"name": "Herbert von Karajan", "role": "conductor", "discipline": "Conductor"},
            {"name": "Wiener Philharmoniker", "role": "ensemble", "discipline": "Orchestra"},
        ],
    }


def test_derive_recordings_reads_a_scraped_payload(session: Session) -> None:
    """Gating on ``_source: "llm"`` here derived zero recordings from every
    deterministic source — the marker says how the payload was produced, which
    is not this pass's business."""
    raw = _scraped_raw()
    decca = FakeSource(
        records=(
            perf_mention("/products/95920/works/449306", "Un ballo in maschera", "Giuseppe Verdi", raw),
            perf_mention("/products/95920/works/551435", "Requiem", "Giuseppe Verdi", raw),
            person("Herbert von Karajan", external_id="/artists/3"),
            ensemble("Wiener Philharmoniker", external_id="/artists/4"),
        ),
        name="decca",
        base_url="https://www.deccaclassics.com/en",
    )
    ingest_source(session, decca)

    derive_recordings(session)

    recording = session.scalars(select(Recording)).one()  # both works, one release
    assert recording.external_key == "product:95920"
    assert recording.catalogue_number == "00028947756415"
    assert recording.label == "Deutsche Grammophon (DG)"
    assert session.scalar(select(func.count()).select_from(RecordingWork)) == 2
    roles = session.scalars(select(RecordingParticipant.role)).all()
    assert sorted(roles) == ["conductor", "ensemble"]


def _imslp_raw() -> dict[str, object]:
    """One album as ``composer_scrapers.imslp_recordings`` writes it.

    Thinner than the other two — IMSLP publishes no release date, label or
    format, and the catalogue number is read off the cover URL — which is the
    case worth pinning: a payload can be mostly empty and still be a recording.
    """
    return {
        "_source": "scraper",
        "_kind": "recording",
        "record_key": "61682",
        "url": "https://imslp.org/wiki/10_Blake_Songs_(Vaughan_Williams,_Ralph)",
        "title": "VAUGHAN WILLIAMS, R.: 10 Blake Songs (Banfalvi)",
        "release_date": None,
        "label": None,
        "catalogue_number": "C5035",
        "format": None,
        "artists": [
            {"name": "Andreas Weller", "role": "soloist", "discipline": "tenor"},
            {"name": "Bela Banfalvi", "role": "conductor", "discipline": None},
        ],
    }


def test_derive_recordings_reads_an_imslp_payload(session: Session) -> None:
    """One album listed on two IMSLP work pages is one recording, not two."""
    raw = _imslp_raw()
    imslp = FakeSource(
        records=(
            perf_mention("49198#61682", "10 Blake Songs", "Vaughan Williams, Ralph", raw),
            perf_mention("49199#61682", "Oboe Concerto", "Vaughan Williams, Ralph", raw),
            person("Andreas Weller", external_id="/names/Andreas%20Weller"),
            person("Bela Banfalvi", external_id="/names/Bela%20Banfalvi"),
        ),
        name="imslp_recordings",
        base_url="https://imslp.org",
    )
    ingest_source(session, imslp)

    derive_recordings(session)

    recording = session.scalars(select(Recording)).one()
    assert recording.external_key == "61682"
    assert recording.catalogue_number == "C5035"
    assert recording.release_date is None
    assert session.scalar(select(func.count()).select_from(RecordingWork)) == 2
    participants = session.scalars(select(RecordingParticipant)).all()
    assert sorted((p.role, p.name) for p in participants) == [
        ("conductor", "Bela Banfalvi"),
        ("soloist", "Andreas Weller"),
    ]
    # The person documents the adapter emits are what let a credit resolve; an
    # unresolved credit never reaches gold's selection.
    assert all(participant.entity_id is not None for participant in participants)
