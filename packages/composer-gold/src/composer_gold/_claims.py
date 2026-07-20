"""Claim collection for the gold build: re-pointed, deduped, and the rule 3 walk."""

from __future__ import annotations

import uuid

from composer_warehouse.models import Claim, Entity
from sqlalchemy import Connection, insert, select

from ._copy import INSERT_BATCH, chunked
from ._selection import GoldBuild


def collect_person_claims(build: GoldBuild) -> set[uuid.UUID]:
    """Claims of kept persons: re-point, dedupe. Returns the non-kept
    entities those claims reference (rule 3's seed)."""
    seen_claims: set[tuple[uuid.UUID, str, uuid.UUID | None, str | None, int]] = set()
    referenced: set[uuid.UUID] = set()
    for chunk in chunked(sorted(build.kept_members, key=str)):
        for c in build.silver.execute(
            select(Claim).where(Claim.subject_id.in_(chunk)).order_by(Claim.id)
        ).scalars():
            subject = build.root(c.subject_id)
            obj = build.root(c.object_id) if c.object_id is not None else None
            key = (subject, c.predicate, obj, c.value, c.source_id)
            if key in seen_claims:
                continue  # collapsing duplicates can align identical claims
            seen_claims.add(key)
            if obj is not None and obj not in build.kept_members:
                referenced.add(obj)
            build.claim_rows.append(
                {
                    "subject_id": subject,
                    "predicate": c.predicate,
                    "object_id": obj,
                    "value": c.value,
                    "source_id": c.source_id,
                    "record_id": c.record_id,
                    "created_at": c.created_at,
                }
            )
    return referenced


def walk_referenced(build: GoldBuild, referenced: set[uuid.UUID]) -> None:
    """Rule 3: keep referenced non-person entities (to a fixpoint),
    collecting the discovery-edge claims along the way."""
    build.all_other = set(build.silver.scalars(select(Entity.id).where(Entity.kind != "person")))
    frontier = {r for r in referenced if r not in build.kept_roots}
    while frontier:
        build.kept_other |= frontier
        next_frontier: set[uuid.UUID] = set()
        for chunk in chunked(sorted(frontier, key=str)):
            for c in build.silver.execute(select(Claim).where(Claim.subject_id.in_(chunk))).scalars():
                obj = build.root(c.object_id) if c.object_id is not None else None
                if obj is None or obj in build.kept_roots or obj in build.kept_other:
                    continue
                next_frontier.add(obj)
                build.claim_rows.append(
                    {
                        "subject_id": c.subject_id,
                        "predicate": c.predicate,
                        "object_id": obj,
                        "value": c.value,
                        "source_id": c.source_id,
                        "record_id": c.record_id,
                        "created_at": c.created_at,
                    }
                )
        frontier = next_frontier
    # With rule 3 off, unreferenced non-person entities are kept as well.
    # Joining after the walk (not seeding it) keeps the referenced part of
    # gold — including discovery-edge claims — identical to a curated run;
    # the literal-claims pass picks up the extra entities' claims.
    if not build.config.prune_unreferenced:
        build.kept_other |= {build.root(e) for e in build.all_other} - build.kept_roots


def collect_other_literal_claims(build: GoldBuild) -> None:
    """Own claims of kept non-person entities (literals, e.g. mentioned_in)."""
    for chunk in chunked(sorted(build.kept_other, key=str)):
        for c in build.silver.execute(
            select(Claim).where(Claim.subject_id.in_(chunk), Claim.object_id.is_(None))
        ).scalars():
            build.claim_rows.append(
                {
                    "subject_id": c.subject_id,
                    "predicate": c.predicate,
                    "object_id": None,
                    "value": c.value,
                    "source_id": c.source_id,
                    "record_id": c.record_id,
                    "created_at": c.created_at,
                }
            )


def insert_claims(build: GoldBuild, gold: Connection) -> None:
    for i in range(0, len(build.claim_rows), INSERT_BATCH):
        gold.execute(insert(Claim), build.claim_rows[i : i + INSERT_BATCH])
