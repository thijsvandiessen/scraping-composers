"""Tests for the post-hoc person dedupe pass."""

import pytest
from composer_models import Entity, PersonMatch
from composer_models.normalize import wikidata_id
from composer_schema import EntityDocument, SourceClaim
from composer_warehouse.persons import apply_clusters, dedupe_persons, match, reset_person_links
from composer_warehouse.testing import FakeSource, ingest_source, person
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def _ingest(session: Session, *people: object) -> None:
    ingest_source(session, FakeSource(records=people))  # pyright: ignore[reportArgumentType]


def _by_label(session: Session) -> dict[str, Entity]:
    return {e.label: e for e in session.scalars(select(Entity).where(Entity.kind == "person"))}


def _counts(session: Session) -> tuple[int, int]:
    """Run the pass and keep only its pair counts.

    Most tests here are about which pairs were decided, not about the partition
    the pass returns alongside them; the constraint tests read that.
    """
    result = dedupe_persons(session)
    return result.auto, result.review


def _linked(session: Session) -> int:
    return session.scalar(select(func.count(Entity.id)).where(Entity.canonical_entity_id.is_not(None))) or 0


def _dates(born: str, died: str) -> tuple[SourceClaim, SourceClaim]:
    return SourceClaim("born_on", value=born), SourceClaim("died_on", value=died)


def _crowd(n: int = 40) -> list[object]:
    """Filler records sharing one very common surname.

    Without them every surname in a two-record test corpus is equally frequent,
    the term-frequency adjustment is identically zero, and the tests cannot show
    the thing that makes a shared rare surname count for more than a shared
    common one. With them, "Bach" is rare and "Smith" is not.
    """
    return [person(f"Smith, Filler{i}") for i in range(n)]


def test_corroborated_dates_auto_link_a_pair(session: Session) -> None:
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685-03-31", "1750-07-28")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    auto, review = _counts(session)

    assert (auto, review) == (1, 0)
    people = _by_label(session)
    dup, canonical = people["Bach, J.S."], people["Bach, Johann Sebastian"]
    assert dup.canonical_entity_id == canonical.id  # fuller name is canonical
    assert canonical.canonical_entity_id is None

    match = session.scalars(select(PersonMatch)).one()
    assert match.status == "auto_linked"
    assert match.method == "given:initials+born:exact+died:exact"
    assert match.score >= 0.99


def test_initials_alone_no_longer_auto_link(session: Session) -> None:
    """The #173 defect, at the level of the pass.

    "J.S." against "Johann Sebastian" is genuinely compatible, but compatible
    initials on a common surname are weak evidence — the old scorer scored this
    exactly at its auto threshold and linked it, and linked "Jordan, Jules" to
    "Jordan, Julius" by the identical rule. Uncorroborated, it now goes to the
    review queue instead.
    """
    _ingest(session, person("Bach, J.S."), person("Bach, Johann Sebastian"), *_crowd())
    auto, review = _counts(session)

    assert (auto, review) == (0, 1)
    assert _linked(session) == 0
    match = session.scalars(select(PersonMatch)).one()
    assert (match.status, match.method) == ("needs_review", "given:initials")


def test_conflicting_given_names_are_never_proposed(session: Session) -> None:
    # The pair the issue leads with. Both reduce to the initial "j", which is
    # why the old scorer auto-linked it. Not even a rare surname rescues it.
    _ingest(session, person("Jordan, Jules"), person("Jordan, Julius"), *_crowd())
    assert _counts(session) == (0, 0)
    assert session.scalar(select(func.count(PersonMatch.id))) == 0


def test_a_corroborated_surname_only_pair_is_queued_for_review(session: Session) -> None:
    _ingest(
        session,
        person("Beethoven", SourceClaim("born_on", value="1770")),
        person("Beethoven, Ludwig van", SourceClaim("born_on", value="1770")),
        *_crowd(),
    )
    auto, review = _counts(session)

    assert (auto, review) == (0, 1)
    assert _linked(session) == 0
    match = session.scalars(select(PersonMatch)).one()
    assert (match.status, match.method) == ("needs_review", "given:one_surname_only+born:exact")
    assert match.entity.label == "Beethoven"  # the sparser name is the duplicate


def test_a_bare_rare_surname_is_queued_never_linked(session: Session) -> None:
    """A bare surname against a full name, with nothing else to go on (#202).

    It used to score at exactly the prior and was never recorded either way.
    On a rare surname it now reaches the review queue — never an auto-link,
    since nothing on the bare side tells one namesake from another.
    """
    _ingest(session, person("Beethoven"), person("Beethoven, Ludwig van"), *_crowd())
    assert _counts(session) == (0, 1)
    match = session.scalars(select(PersonMatch)).one()
    assert (match.status, match.method) == ("needs_review", "given:one_surname_only")
    assert _linked(session) == 0


def test_a_bare_common_surname_is_not_proposed_at_all(session: Session) -> None:
    """On a common surname the same comparison stays below its cut-point: the
    old scorer queued 46,046 such pairs at a flat 0.70, where no reviewer could
    do better than guess between the namesakes."""
    _ingest(session, person("Smith"), *_crowd())
    assert _counts(session) == (0, 0)


def test_birth_year_conflict_keeps_namesakes_separate(session: Session) -> None:
    # Same name, a generation apart: the father, not the son.
    _ingest(
        session,
        person("Strauss, Johann", *_dates("1804", "1849")),
        person("Strauss, Johann", SourceClaim("born_on", value="1825")),
    )
    auto, review = _counts(session)
    assert (auto, review) == (0, 0)
    assert _linked(session) == 0


def test_reingest_pass_is_idempotent(session: Session) -> None:
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    first = _counts(session)
    second = _counts(session)

    assert first == (1, 0)
    assert second == (0, 0)  # the decided pair is skipped on re-run
    assert session.scalar(select(func.count(PersonMatch.id))) == 1


def test_different_surnames_are_not_compared(session: Session) -> None:
    _ingest(session, person("Bach, Johann Sebastian"), person("Handel, George Frideric"))
    assert _counts(session) == (0, 0)


def test_aliases_are_used_for_matching(session: Session) -> None:
    _ingest(
        session,
        person(
            "Beethoven, Ludwig van",
            SourceClaim("also_known_as", value="Beethoven, Louis van"),
            *_dates("1770", "1827"),
        ),
        person("Beethoven, Louis van", *_dates("1770", "1827")),
    )
    auto, review = _counts(session)
    assert (auto, review) == (1, 0)
    people = _by_label(session)
    assert people["Beethoven, Louis van"].canonical_entity_id == people["Beethoven, Ludwig van"].id


def test_reset_discards_machine_links_so_the_pass_can_re_decide(session: Session) -> None:
    """#173's links are already in the database, and a re-run alone would keep
    them — the pass skips any pair that already has a match row."""
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    assert _counts(session) == (1, 0)

    deleted, unlinked = reset_person_links(session)
    assert (deleted, unlinked) == (1, 1)
    assert session.scalar(select(func.count(PersonMatch.id))) == 0
    assert _linked(session) == 0

    assert _counts(session) == (1, 0)  # free to decide the pair again


def test_reset_keeps_reviewed_decisions_and_the_links_they_justify(session: Session) -> None:
    _ingest(
        session,
        person("Beethoven", SourceClaim("born_on", value="1770")),
        person("Beethoven, Ludwig van", SourceClaim("born_on", value="1770")),
        *_crowd(),
    )
    dedupe_persons(session)
    match = session.scalars(select(PersonMatch)).one()
    match.status = "accepted"
    match.entity.canonical_entity_id = match.canonical_entity_id
    session.commit()

    deleted, unlinked = reset_person_links(session)
    assert (deleted, unlinked) == (0, 0)
    assert session.scalars(select(PersonMatch)).one().status == "accepted"
    assert _linked(session) == 1


def test_reset_drops_a_link_a_reviewer_rejected(session: Session) -> None:
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    dedupe_persons(session)
    match = session.scalars(select(PersonMatch)).one()
    match.status = "rejected"
    session.commit()

    deleted, unlinked = reset_person_links(session)
    assert (deleted, unlinked) == (0, 1)  # the row survives, the link does not
    assert session.scalars(select(PersonMatch)).one().status == "rejected"
    assert _linked(session) == 0


def _chain_of_three(session: Session) -> dict[str, Entity]:
    """Three spellings of one person, pairwise-linkable in any order."""
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685", "1750")),
        person("Bach, Johann S.", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    dedupe_persons(session)
    return _by_label(session)


def test_a_group_of_three_all_points_at_one_canonical(session: Session) -> None:
    """The pointer-per-pair defect: ``J.S. -> Johann S.`` and
    ``Johann S. -> Johann Sebastian`` are both defensible pair decisions, and
    together they are a chain nothing asked for. Clustering picks the canonical
    once, from the whole membership."""
    people = _chain_of_three(session)
    canonical = people["Bach, Johann Sebastian"]

    assert canonical.canonical_entity_id is None  # fullest given names, chosen once
    assert people["Bach, J.S."].canonical_entity_id == canonical.id
    assert people["Bach, Johann S."].canonical_entity_id == canonical.id


def test_no_canonical_is_itself_a_duplicate(session: Session) -> None:
    """The invariant gold relies on: ``canonical_entity_id`` is one hop to a
    root, so ``_resolve_roots`` is a dict read and needs no cycle guard."""
    _chain_of_three(session)

    links = {
        entity_id: canonical_id
        for entity_id, canonical_id in session.execute(
            select(Entity.id, Entity.canonical_entity_id).where(Entity.canonical_entity_id.is_not(None))
        )
    }
    assert links  # the fixture linked something, so the assertion below has teeth
    assert not [dup for dup, canonical in links.items() if canonical in links]


def test_an_accepted_decision_joins_the_cluster(session: Session) -> None:
    """A human ``accepted`` row is a link like any other, and the canonical is
    still chosen from the whole group rather than from that pair."""
    _ingest(
        session,
        person("Beethoven", SourceClaim("born_on", value="1770")),
        person("Beethoven, Ludwig van", *_dates("1770", "1827")),
        person("Beethoven, Ludwig", *_dates("1770", "1827")),
        *_crowd(),
    )
    dedupe_persons(session)
    people = _by_label(session)
    assert people["Beethoven"].canonical_entity_id is None  # too sparse to link on its own
    accepted = session.scalars(
        select(PersonMatch).where(
            PersonMatch.entity_id == people["Beethoven"].id,
            PersonMatch.canonical_entity_id == people["Beethoven, Ludwig"].id,
        )
    ).one()
    accepted.status = "accepted"
    session.commit()
    apply_clusters(session)

    canonical = people["Beethoven, Ludwig van"]
    assert canonical.canonical_entity_id is None
    assert people["Beethoven"].canonical_entity_id == canonical.id
    assert people["Beethoven, Ludwig"].canonical_entity_id == canonical.id


def _wikidata(label: str, qid: str, *claims: SourceClaim) -> EntityDocument:
    """A person record keyed by its QID, as ingest keys a wikidata page.

    ``external_id`` has to differ too: two documents with the same source id are
    the same record, and these tests turn on two *items* wearing one name.
    """
    return person(label, *claims, external_id=qid, url=f"https://www.wikidata.org/wiki/{qid}")


def test_the_authority_anchored_member_is_canonical_over_a_fuller_name(session: Session) -> None:
    """#379: catalogue links pulled "Wolfgang Amadeus A. Mozart" into Mozart's
    cluster, and its extra initial made it the fullest name there. Gold shows
    the canonical's label, so the Q254 entity has to win however it is spelled."""
    _ingest(
        session,
        _wikidata("Wolfgang Amadeus Mozart", "Q254", *_dates("1756-01-27", "1791-12-05")),
        person("Wolfgang Amadeus A. Mozart", *_dates("1756", "1791")),
        *_crowd(),
    )
    dedupe_persons(session)

    people = _by_label(session)
    canonical = people["Wolfgang Amadeus Mozart"]
    assert canonical.canonical_entity_id is None
    assert people["Wolfgang Amadeus A. Mozart"].canonical_entity_id == canonical.id


def test_without_an_anchor_the_member_with_more_sources_is_canonical(session: Session) -> None:
    """Below the authorities, corroboration outranks spelling: the name two
    sources agree on beats a fuller one only a single source wrote."""
    corroborated = person("Mozart, Wolfgang Amadeus", *_dates("1756", "1791"))
    for name in ("one", "two"):
        ingest_source(session, FakeSource(records=(corroborated,), name=name))
    _ingest(session, person("Mozart, Wolfgang Amadeus Chrysostomus", *_dates("1756", "1791")), *_crowd())
    dedupe_persons(session)

    people = _by_label(session)
    canonical = people["Mozart, Wolfgang Amadeus"]
    assert canonical.canonical_entity_id is None
    assert people["Mozart, Wolfgang Amadeus Chrysostomus"].canonical_entity_id == canonical.id


def test_distinct_qids_are_not_merged(session: Session) -> None:
    """The #204 defect: 862 clusters spanned two or more QIDs.

    Two wikidata items are two people unless something says otherwise, and
    these two agree on everything the *scorer* can see — same name, same dates
    — which is exactly why the score alone cannot be what decides it. The
    ``PersonMatch`` row is still written: it is the audit trail of what was
    scored, and the refusal is a fact about the clustering, not about the score.
    """
    _ingest(
        session,
        _wikidata("Bach, Johann Sebastian", "Q1339", *_dates("1685", "1750")),
        _wikidata("Bach, Johann Sebastian", "Q76428", *_dates("1685", "1750")),
    )
    result = dedupe_persons(session)

    assert result.auto == 1
    assert _linked(session) == 0
    assert result.partition.clustering.clusters == ()
    assert len(result.partition.clustering.refused) == 1
    assert result.partition.constraints.cannot_link != ()
    assert session.scalars(select(PersonMatch)).one().status == "auto_linked"


def test_no_cluster_holds_two_uncorroborated_qids(session: Session) -> None:
    """The acceptance criterion, over a corpus with something of everything.

    A bare-name record must not carry one wikidata item into the other's
    cluster. Since #202 it does not join either when it fits both: that would
    be a guess between them, so its links are held back for review. One that
    fits a single item still links.
    """
    _ingest(
        session,
        _wikidata("Bach, Johann Sebastian", "Q1339", *_dates("1685", "1750")),
        _wikidata("Bach, Johann Sebastian", "Q76428", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750"), external_id="plain"),
        _wikidata("Bach, Johann Christian", "Q57226", *_dates("1735", "1782")),
        person("Bach, Johann Christian", *_dates("1735", "1782"), external_id="plain-jc"),
        *_crowd(),
    )
    result = dedupe_persons(session)

    qids = {
        entity.id: wikidata_id(entity.dedup_key)
        for entity in session.scalars(select(Entity).where(Entity.kind == "person"))
    }
    clusters = result.partition.clustering.clusters
    assert clusters, "nothing merged at all, so the constraint is untested"
    for cluster in clusters:
        found = {qids[member] for member in cluster if qids[member]}
        assert len(found) <= 1, f"cluster spans {found}"
    # Johann Christian's bare-name record joined the one item it fits; Johann
    # Sebastian's fits two, so both of its links went to review instead, and
    # the cannot-link still refuses the direct Q1339-Q76428 link.
    assert result.partition.clustering.members == 2
    assert result.ambiguous == 2
    assert len(result.partition.clustering.refused) == 1
    plain_js = next(
        entity
        for entity in session.scalars(select(Entity).where(Entity.label == "Bach, Johann Sebastian"))
        if not qids[entity.id]
    )
    assert plain_js.canonical_entity_id is None


def _strauss_family() -> list[EntityDocument]:
    """Johann Strauss I, II and III as wikidata files them: three items, each
    listing the plain "Johann Strauss" among its aliases."""
    alias = SourceClaim("also_known_as", value="Johann Strauss")
    return [
        _wikidata("Johann Strauss I", "Q184178", alias, *_dates("1804", "1849")),
        _wikidata("Johann Strauss II", "Q83309", alias, *_dates("1825", "1899")),
        _wikidata("Johann Strauss III", "Q28999", alias, *_dates("1866", "1939")),
    ]


def test_a_namesake_family_is_never_collapsed_onto_one_member(session: Session) -> None:
    """#202: a record that fits several authority items is a guess, not a link.

    An undated "Johann Strauss" agrees exactly with the alias all three items
    carry, and a bare "Strauss" fits any of them. The cannot-links keep I, II
    and III apart, but would still attach each record to whichever item scored
    highest. Neither may be auto-linked to any of them: they go to review,
    where a human can pick the namesake.
    """
    _ingest(
        session,
        *_strauss_family(),
        person("Johann Strauss", external_id="plain"),
        person("Strauss"),
        *_crowd(),
    )
    result = dedupe_persons(session)

    assert result.auto == 0
    assert _linked(session) == 0
    statuses = {match.status for match in session.scalars(select(PersonMatch))}
    assert statuses == {"needs_review"}


def test_a_namesake_family_is_held_back_however_confident_the_score(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same family with the auto cut-point dropped to the review one, so
    the undated "Johann Strauss" scores as a link against all three items. The
    guard, not the threshold, is what keeps it out of every cluster."""
    monkeypatch.setattr(match, "AUTO_THRESHOLD", match.REVIEW_THRESHOLD)
    _ingest(
        session,
        *_strauss_family(),
        person("Johann Strauss", external_id="plain"),
        person("Strauss"),
        *_crowd(),
    )
    result = dedupe_persons(session)

    assert result.ambiguous == 3
    assert result.auto == 0
    assert _linked(session) == 0
    assert {m.status for m in session.scalars(select(PersonMatch))} == {"needs_review"}


def test_a_record_fitting_one_authority_item_still_links(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard is about rivals, not about authority ids as such: with only
    Johann Strauss II on file, the same "Johann Strauss" links to him."""
    monkeypatch.setattr(match, "AUTO_THRESHOLD", match.REVIEW_THRESHOLD)
    _ingest(session, _strauss_family()[1], person("Johann Strauss", external_id="plain"), *_crowd())
    result = dedupe_persons(session)

    people = _by_label(session)
    assert result.ambiguous == 0
    assert people["Johann Strauss"].canonical_entity_id == people["Johann Strauss II"].id


def test_corroboration_lets_a_wikidata_duplicate_merge(session: Session) -> None:
    """The other half of #204: some of those merges were right.

    Wikidata records "Aaron Aachen" as Norbert Linke's pseudonym and the birth
    years agree, so wikidata has contradicted its own id and the constraint is
    discharged.
    """
    _ingest(
        session,
        _wikidata("Aaron Aachen", "Q139217390", *_dates("1933", "2020")),
        _wikidata(
            "Norbert Linke",
            "Q1796591",
            SourceClaim("also_known_as", value="Aaron Aachen"),
            *_dates("1933-03-05", "2020-11-10"),
        ),
    )
    result = dedupe_persons(session)

    people = _by_label(session)
    assert people["Aaron Aachen"].canonical_entity_id == people["Norbert Linke"].id
    assert result.partition.clustering.refused == ()
    assert len(result.partition.constraints.discharged) == 1


def test_a_rejected_decision_is_not_undone_by_a_transitive_merge(session: Session) -> None:
    """What a pairwise pass could not express. The reviewer said these two are
    different people; the pass must not reunite them through a third record."""
    _ingest(
        session,
        person("Bach, J.S.", *_dates("1685", "1750")),
        person("Bach, Johann S.", *_dates("1685", "1750")),
        person("Bach, Johann Sebastian", *_dates("1685", "1750")),
    )
    dedupe_persons(session)
    people = _by_label(session)
    rejected = session.scalars(
        select(PersonMatch).where(
            PersonMatch.entity_id == people["Bach, J.S."].id,
            PersonMatch.canonical_entity_id == people["Bach, Johann Sebastian"].id,
        )
    ).one()
    rejected.status = "rejected"
    session.commit()
    apply_clusters(session)

    assert people["Bach, J.S."].canonical_entity_id != people["Bach, Johann Sebastian"].id
    assert people["Bach, Johann Sebastian"].canonical_entity_id is None
