"""Genres and movements with their ``subclass_of`` hierarchy (#349).

Everything here rolls up: a genre stands for itself and every genre below it,
so "dance music" finds a composer tagged only with "waltz". The subtree is a
recursive CTE over two kinds of edge — ``subclass_of`` claims (parent to
child) and ``canonical_entity_id`` pointers (a label-only genre silver linked
to its QID one) — joined with UNION, not UNION ALL: P279 has cycles, and
UNION's de-duplication is what makes the recursion stop on both SQLite and
Postgres.
"""

import uuid

from composer_models import Claim, Entity
from composer_models.genres import GENRE_KINDS, MEMBERSHIP_PREDICATES, TOP_LEVEL
from composer_models.normalize import qid_key, wikidata_id
from sqlalchemy import CTE, ColumnElement, func, select, union_all
from sqlalchemy.orm import Session

from ..deps import Pagination
from ..errors import NotFoundError
from ..schemas import EntitySummary, GenreDetail, GenrePeoplePage, GenreRef, GenreSummary

_MEMBERSHIP = tuple(MEMBERSHIP_PREDICATES.values())


def descendants(root_id: uuid.UUID) -> CTE:
    """``root_id`` and every genre below it, as a one-column (``id``) CTE."""
    edges = union_all(
        select(Claim.object_id.label("parent"), Claim.subject_id.label("child")).where(
            Claim.predicate == "subclass_of", Claim.object_id.is_not(None)
        ),
        select(Entity.canonical_entity_id.label("parent"), Entity.id.label("child")).where(
            Entity.canonical_entity_id.is_not(None)
        ),
    ).subquery()
    tree = select(Entity.id.label("id")).where(Entity.id == root_id).cte("genre_tree", recursive=True)
    return tree.union(select(edges.c.child).join(tree, edges.c.parent == tree.c.id))


def genre_filter(root_id: uuid.UUID) -> ColumnElement[bool]:
    """Persons tagged with ``root_id`` or anything below it. An uncorrelated IN,
    like the other person filters (see ``people.profession_filter``)."""
    tree = descendants(root_id)
    return Entity.id.in_(
        select(Claim.subject_id).where(
            Claim.predicate.in_(_MEMBERSHIP), Claim.object_id.in_(select(tree.c.id))
        )
    )


def _person_count(db: Session, root_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).where(Entity.kind == "person", genre_filter(root_id))) or 0


def _summary(db: Session, entity: Entity) -> GenreSummary:
    return GenreSummary(
        id=entity.id,
        label=entity.label,
        kind=entity.kind,
        wikidata_id=wikidata_id(entity.dedup_key),
        person_count=_person_count(db, entity.id),
    )


def list_genre_facets(db: Session, kind: str) -> list[GenreSummary]:
    """The top-level genres (or movements) present, each with its subtree's
    person count, most persons first."""
    keys = [qid_key(qid) for qid in TOP_LEVEL[kind]]
    tops = db.scalars(select(Entity).where(Entity.kind == kind, Entity.dedup_key.in_(keys))).all()
    return sorted((_summary(db, e) for e in tops), key=lambda s: (-s.person_count, s.label))


def _genre(db: Session, genre_id: uuid.UUID) -> Entity:
    entity = db.get(Entity, genre_id)
    if entity is None or entity.kind not in GENRE_KINDS:
        raise NotFoundError("genre not found")
    return entity


def get_genre(db: Session, genre_id: uuid.UUID) -> GenreDetail:
    entity = _genre(db, genre_id)
    parent = select(Entity.id, Entity.label).join(Claim, Claim.object_id == Entity.id)
    child = select(Entity.id, Entity.label).join(Claim, Claim.subject_id == Entity.id)
    parents = db.execute(
        parent.where(Claim.subject_id == genre_id, Claim.predicate == "subclass_of")
        .distinct()
        .order_by(Entity.label)
    ).all()
    children = db.execute(
        child.where(Claim.object_id == genre_id, Claim.predicate == "subclass_of")
        .distinct()
        .order_by(Entity.label)
    ).all()
    return GenreDetail(
        **_summary(db, entity).model_dump(),
        parents=[GenreRef(id=i, label=label) for i, label in parents],
        children=[GenreRef(id=i, label=label) for i, label in children],
    )


def genre_people(db: Session, genre_id: uuid.UUID, pager: Pagination) -> GenrePeoplePage:
    """Persons tagged with the genre or any genre below it, by label."""
    _genre(db, genre_id)
    base = select(Entity).where(Entity.kind == "person", genre_filter(genre_id))
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = db.scalars(base.order_by(Entity.label, Entity.id).offset(pager.offset).limit(pager.limit)).all()
    return GenrePeoplePage(
        items=[EntitySummary.model_validate(e) for e in rows],
        total=total or 0,
        page=pager.page,
        limit=pager.limit,
    )
