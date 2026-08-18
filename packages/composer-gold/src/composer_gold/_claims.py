"""Claim collection for the gold build: re-pointed, deduped, and the rule 3 walk."""

from __future__ import annotations

import uuid

from composer_models import Claim, Entity
from sqlalchemy import Connection, insert, select

from ._copy import INSERT_BATCH, chunked
from ._selection import GoldBuild


def collect_person_claims(build: GoldBuild) -> set[uuid.UUID]:
    """Claims of kept persons: re-point, dedupe. Returns rule 3's seed — the
    non-kept entities those claims reference by at least ``min_referrers``
    distinct kept persons (all of them when the rule is off)."""
    seen_claims: set[tuple[uuid.UUID, str, uuid.UUID | None, str | None, int]] = set()
    referrers: dict[uuid.UUID, set[uuid.UUID]] = {}
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
                referrers.setdefault(obj, set()).add(subject)
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
    # With the rule off the walk keeps everything anyway; a threshold of 1 then
    # seeds it exactly as an unfiltered run so the referenced part is identical.
    threshold = build.config.min_referrers if build.config.prune_unreferenced else 1
    return {obj for obj, subjects in referrers.items() if len(subjects) >= threshold}


def walk_referenced(build: GoldBuild, referenced: set[uuid.UUID]) -> None:
    """Rule 3: keep referenced non-person entities (to a fixpoint),
    collecting the discovery-edge claims along the way."""
    build.all_other = set(build.silver.scalars(select(Entity.id).where(Entity.kind != "person")))
    # Ensembles are judged by rule 1 (credits), not by being referenced: an
    # ensemble a kept person points at but that never played a concert or
    # recording stays out, and the walk does not expand through it.
    frontier = {r for r in referenced if r not in build.kept_roots and r not in build.unevidenced_ensembles}
    while frontier:
        build.kept_other |= frontier
        next_frontier: set[uuid.UUID] = set()
        for chunk in chunked(sorted(frontier, key=str)):
            for c in build.silver.execute(select(Claim).where(Claim.subject_id.in_(chunk))).scalars():
                obj = build.root(c.object_id) if c.object_id is not None else None
                if (
                    obj is None
                    or obj in build.kept_roots
                    or obj in build.kept_other
                    or obj in build.unevidenced_ensembles
                ):
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
        build.kept_other |= (
            {build.root(e) for e in build.all_other} - build.kept_roots - build.unevidenced_ensembles
        )
    # Credited ensembles stand on their own evidence, so they are kept whether or
    # not a claim points at them (rule 3 never sees the concert tables).
    build.kept_other |= build.kept_ensembles - build.kept_roots


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


def drop_pruned_object_claims(build: GoldBuild) -> None:
    """Drop claim rows whose object entity did not survive rule 3.

    A kept person can reference an entity that the ``min_referrers`` threshold
    then prunes (its only other referrers fell short). Those claims would point
    at a row gold never copies, so remove them. With the threshold at its
    default of 1 every referenced object is kept, so this is a no-op."""
    kept = build.kept_roots | build.kept_other
    build.claim_rows = [
        row for row in build.claim_rows if row["object_id"] is None or row["object_id"] in kept
    ]


def insert_claims(build: GoldBuild, gold: Connection) -> None:
    for i in range(0, len(build.claim_rows), INSERT_BATCH):
        gold.execute(insert(Claim), build.claim_rows[i : i + INSERT_BATCH])
