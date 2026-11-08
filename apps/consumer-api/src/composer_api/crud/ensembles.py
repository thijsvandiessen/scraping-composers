import uuid
from dataclasses import dataclass, field

from composer_models import Claim, ConcertParticipant, Entity, RecordingParticipant
from composer_models.normalize import wikidata_id
from composer_schema import ensemble_type_from_name
from sqlalchemy import ColumnElement, Subquery, func, not_, or_, select
from sqlalchemy.orm import Session, aliased

from ..deps import EnsembleFilters, Filters, Pagination
from ..errors import NotFoundError
from ..schemas import EnsembleDetail, EnsemblePage, EnsembleSummary, EntityRef
from .common import outgoing_claims
from .people import source_filter

# Predicates read off an ensemble (or, in silver, off the Wikidata ensemble it
# was folded onto) to describe it.
_DESCRIBING = ("ensemble_type", "based_in", "also_known_as", "musicbrainz_id", "part_of")


@dataclass
class _Facts:
    """What an ensemble's claims say about it."""

    types: list[str] = field(default_factory=list)
    cities: list[EntityRef] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    musicbrainz: list[str] = field(default_factory=list)
    part_of: list[EntityRef] = field(default_factory=list)


def _anchored() -> ColumnElement[bool]:
    # Keyed on a QID (see composer_models.normalize.entity_key), or, in silver,
    # folded onto an ensemble that is. Gold resolves the fold away.
    return or_(Entity.dedup_key.like("wikidata:%"), Entity.canonical_entity_id.is_not(None))


def _typed(ensemble_type: str) -> ColumnElement[bool]:
    typed = select(Claim.subject_id).where(Claim.predicate == "ensemble_type", Claim.value == ensemble_type)
    return or_(Entity.id.in_(typed), Entity.canonical_entity_id.in_(typed))


def _facts(db: Session, ids: set[uuid.UUID]) -> dict[uuid.UUID, _Facts]:
    """The describing claims of ``ids``, sorted for a stable response."""
    obj = aliased(Entity)
    facts: dict[uuid.UUID, _Facts] = {entity_id: _Facts() for entity_id in ids}
    rows = db.execute(
        select(Claim.subject_id, Claim.predicate, Claim.value, obj.id, obj.label)
        .outerjoin(obj, obj.id == Claim.object_id)
        .where(Claim.subject_id.in_(ids), Claim.predicate.in_(_DESCRIBING))
        .order_by(Claim.predicate, Claim.value, obj.label)
    ).tuples()
    for subject_id, predicate, value, object_id, object_label in rows:
        fact = facts[subject_id]
        ref = EntityRef(id=object_id, label=object_label) if object_id and object_label else None
        if predicate == "ensemble_type" and value:
            fact.types.append(value)
        elif predicate == "based_in" and ref and ref not in fact.cities:
            fact.cities.append(ref)
        elif predicate == "also_known_as" and value and value not in fact.aliases:
            fact.aliases.append(value)
        elif predicate == "musicbrainz_id" and value:
            fact.musicbrainz.append(value)
        elif predicate == "part_of" and ref and ref not in fact.part_of:
            fact.part_of.append(ref)
    return facts


def _summary(entity: Entity, fact: _Facts, concerts: int, recordings: int) -> EnsembleSummary:
    return EnsembleSummary(
        id=entity.id,
        label=entity.label,
        wikidata_id=wikidata_id(entity.dedup_key),
        ensemble_type=fact.types[0] if fact.types else ensemble_type_from_name(entity.label),
        home_city=fact.cities[0] if fact.cities else None,
        canonical_entity_id=entity.canonical_entity_id,
        concert_count=concerts,
        recording_count=recordings,
    )


def _appearance_counts() -> tuple[Subquery, Subquery]:
    concerts = (
        select(
            ConcertParticipant.entity_id.label("entity_id"),
            func.count(func.distinct(ConcertParticipant.concert_id)).label("n"),
        )
        .group_by(ConcertParticipant.entity_id)
        .subquery()
    )
    recordings = (
        select(
            RecordingParticipant.entity_id.label("entity_id"),
            func.count(func.distinct(RecordingParticipant.recording_id)).label("n"),
        )
        .group_by(RecordingParticipant.entity_id)
        .subquery()
    )
    return concerts, recordings


def list_ensembles(db: Session, filters: Filters, pager: Pagination, ef: EnsembleFilters) -> EnsemblePage:
    """Ensembles with their type, home city and how often they are credited.

    Type and home city are read off the ensemble itself or, for a silver
    spelling folded onto a Wikidata ensemble, off that ensemble.
    """
    base = select(Entity).where(Entity.kind == "ensemble")
    if filters.source:
        base = base.where(source_filter(filters.source))
    if filters.q:
        base = base.where(Entity.label.ilike(f"%{filters.q}%"))
    if ef.anchored is True:
        base = base.where(_anchored())
    elif ef.anchored is False:
        base = base.where(not_(_anchored()))
    if ef.type:
        base = base.where(_typed(ef.type))

    total = db.scalar(select(func.count()).select_from(base.subquery()))

    # uncorrelated aggregates, outer-joined in (see crud/people.py)
    concerts, recordings = _appearance_counts()
    query = (
        base.add_columns(concerts.c.n, recordings.c.n)
        .outerjoin(concerts, concerts.c.entity_id == Entity.id)
        .outerjoin(recordings, recordings.c.entity_id == Entity.id)
    )
    if ef.sort == "appearances":
        appearances = func.coalesce(concerts.c.n, 0) + func.coalesce(recordings.c.n, 0)
        query = query.order_by(appearances.desc(), Entity.label)
    else:
        query = query.order_by(Entity.label)
    rows = db.execute(query.offset(pager.offset).limit(pager.limit)).tuples().all()

    facts = _facts(db, {entity.canonical_entity_id or entity.id for entity, _, _ in rows})
    return EnsemblePage(
        items=[
            _summary(entity, facts[entity.canonical_entity_id or entity.id], c or 0, r or 0)
            for entity, c, r in rows
        ],
        total=total or 0,
        page=pager.page,
        limit=pager.limit,
    )


def get_ensemble(db: Session, ensemble_id: uuid.UUID) -> EnsembleDetail:
    entity = db.get(Entity, ensemble_id)
    if entity is None or entity.kind != "ensemble":
        raise NotFoundError("ensemble not found")

    described = entity.canonical_entity_id or entity.id
    fact = _facts(db, {described})[described]
    concerts = db.scalar(
        select(func.count(func.distinct(ConcertParticipant.concert_id))).where(
            ConcertParticipant.entity_id == entity.id
        )
    )
    recordings = db.scalar(
        select(func.count(func.distinct(RecordingParticipant.recording_id))).where(
            RecordingParticipant.entity_id == entity.id
        )
    )
    folded = db.scalars(select(Entity.label).where(Entity.canonical_entity_id == entity.id)).all()
    aliases = sorted({*fact.aliases, *folded} - {entity.label})
    summary = _summary(entity, fact, concerts or 0, recordings or 0)
    return EnsembleDetail(
        **summary.model_dump(),
        aliases=aliases,
        musicbrainz_id=fact.musicbrainz[0] if fact.musicbrainz else None,
        part_of=fact.part_of,
        claims=outgoing_claims(db, entity.id),
    )
