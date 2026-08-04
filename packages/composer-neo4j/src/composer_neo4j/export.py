"""Export the gold database into Neo4j, with a manifest like promote's."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import chain
from pathlib import Path

from composer_warehouse.build import BuildManifest, read_build_manifest, run_tracked
from composer_warehouse.models import (
    Claim,
    ConcertParticipant,
    ConcertWork,
    RawWorkMention,
    RecordingParticipant,
    RecordingWork,
    Work,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import (
    AURA_FREE_MAX_NODES,
    AURA_FREE_MAX_RELATIONSHIPS,
    ExportConfig,
    ExportTooLargeError,
)
from .mapping import (
    GoldIndex,
    NodeBatch,
    RelBatch,
    build_index,
    iter_concert_nodes,
    iter_entity_nodes,
    iter_recording_nodes,
    iter_work_nodes,
)
from .relationships import (
    iter_claim_relationships,
    iter_composer_relationships,
    iter_participant_relationships,
    iter_programme_relationships,
)
from .writer import (
    GraphWriter,
    connect,
    create_constraints,
    graph_size,
    wipe,
    write_nodes,
    write_relationships,
)

log = logging.getLogger(__name__)

DEFAULT_MANIFEST_KEY = "./neo4j"


@dataclass(frozen=True)
class ExportStats:
    """What the target holds after the export — read back from it, not counted
    on the way out (see ``graph_size``)."""

    nodes: int = 0
    relationships: int = 0
    entities: int = 0
    works: int = 0
    concerts: int = 0
    recordings: int = 0
    works_skipped_unperformed: int = 0
    duplicate_edges_merged: int = 0
    rows_sent: int = 0


def read_export_manifest(key: str | Path = DEFAULT_MANIFEST_KEY) -> BuildManifest | None:
    return read_build_manifest(key)


def _nodes(session: Session, index: GoldIndex, config: ExportConfig) -> Iterator[NodeBatch]:
    return chain(
        iter_entity_nodes(session, index, config),
        iter_work_nodes(session, index, config),
        iter_concert_nodes(session, index, config),
        iter_recording_nodes(session, index, config),
    )


def _relationships(session: Session, index: GoldIndex, config: ExportConfig) -> Iterator[RelBatch]:
    return chain(
        iter_claim_relationships(session, index, config),
        iter_composer_relationships(session, index, config),
        iter_participant_relationships(session, index, config),
        iter_programme_relationships(session, index, config),
    )


def count_nodes(index: GoldIndex) -> int:
    """Exact node count — the index already holds every id that will be written."""
    return (
        len(index.entity_labels) + len(index.work_ids) + len(index.concert_keys) + len(index.recording_keys)
    )


def count_relationships(session: Session, index: GoldIndex) -> int:
    """Exact relationship count, so the capacity check runs before the wipe."""
    distinct_claims = select(
        func.count()
    ).select_from(
        select(Claim.subject_id, Claim.predicate, Claim.object_id)
        .where(Claim.object_id.is_not(None))
        .distinct()
        .subquery()
    )
    total = session.scalar(distinct_claims) or 0
    total += sum(
        1
        for work_id, composer_id in session.execute(
            select(Work.id, Work.composer_entity_id).where(Work.composer_entity_id.is_not(None))
        ).tuples()
        if work_id in index.work_ids and composer_id in index.entity_labels
    )
    for model in (ConcertParticipant, RecordingParticipant):
        total += session.scalar(
            select(func.count()).select_from(model).where(model.entity_id.is_not(None))
        ) or 0
    for model in (ConcertWork, RecordingWork):
        total += sum(
            1
            for (work_id,) in session.execute(
                select(RawWorkMention.work_id)
                .join(model, model.mention_id == RawWorkMention.id)
                .where(RawWorkMention.work_id.is_not(None))
            ).tuples()
            if work_id in index.work_ids
        )
    return total


def check_capacity(nodes: int, relationships: int) -> None:
    """Fail before writing when the graph cannot fit an Aura Free instance.

    A partial write is worse than a refusal: the wipe has already happened by
    the time the cap is hit, so the instance would be left holding half a graph
    with no way to tell that from a finished one.
    """
    if nodes > AURA_FREE_MAX_NODES or relationships > AURA_FREE_MAX_RELATIONSHIPS:
        raise ExportTooLargeError(
            f"the mapped graph ({nodes:,} nodes, {relationships:,} relationships) exceeds "
            f"Aura Free's limits ({AURA_FREE_MAX_NODES:,} / {AURA_FREE_MAX_RELATIONSHIPS:,}). "
            "Re-run without 'include unperformed works', or use a paid instance."
        )


def export_to_neo4j(
    gold: Session,
    config: ExportConfig,
    manifest_key: str | Path = DEFAULT_MANIFEST_KEY,
    writer: GraphWriter | None = None,
) -> ExportStats:
    """Rebuild the Neo4j graph from the gold session.

    Progress and outcome land in ``{manifest_key}.manifest.json``, the same
    contract the gold promote uses, so the dashboard can poll one shape for both.

    ``writer`` is an injection point for tests; left unset, the export opens its
    own connection from ``config`` and closes it when done.
    """
    stats = run_tracked(manifest_key, lambda: _export(gold, config, writer))
    log.info("exported gold to %s: %s", config.uri, stats)
    return stats


def _export(gold: Session, config: ExportConfig, writer: GraphWriter | None) -> ExportStats:
    index = build_index(gold, config)
    check_capacity(count_nodes(index), count_relationships(gold, index))

    owned = writer is None
    target = writer if writer is not None else connect(config)
    try:
        create_constraints(target)
        if config.wipe_first:
            wipe(target)
        sent_nodes = write_nodes(target, _nodes(gold, index, config))
        sent_relationships = write_relationships(target, _relationships(gold, index, config))
        nodes, relationships = graph_size(target)
    finally:
        if owned:
            target.close()

    total_works = gold.scalar(select(func.count()).select_from(Work)) or 0
    return ExportStats(
        nodes=nodes,
        relationships=relationships,
        duplicate_edges_merged=sent_relationships - relationships,
        rows_sent=sent_nodes + sent_relationships,
        entities=len(index.entity_labels),
        works=len(index.work_ids),
        concerts=len(index.concert_keys),
        recordings=len(index.recording_keys),
        works_skipped_unperformed=total_works - len(index.work_ids),
    )
