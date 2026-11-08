import json
import uuid
from typing import Any

from composer_models import RawWorkMention, Work, WorkIdentifier, WorkTitle
from composer_models.normalize import MAX_KEY_CHARS
from composer_schema import WorkMentionDocument
from composer_schema.premiere import premiere_of, supersedes
from sqlalchemy import update

from ..works import Candidate, MatchResult, WorkFeatures, mention_features, refresh_candidate, resolve
from .entities import get_or_create_entity
from .state import IngestState, content_hash


def new_work(composer_id: uuid.UUID | None, title: str, features: WorkFeatures) -> Work:
    """A new canonical ``Work`` from a title and its extracted features. The id
    is assigned here (not derived from the title) so the caller can reference it
    before flushing."""
    return Work(
        id=uuid.uuid4(),
        composer_entity_id=composer_id,
        canonical_title=title,
        title_key=features.normalized_title,
        work_type=features.work_type,
        opus_number=features.opus_number,
        catalogue_prefix=features.catalogue_prefix,
        catalogue_number=features.catalogue_number,
        musical_key=features.musical_key,
        number=features.number,
    )


def resolve_mention(
    state: IngestState, composer_id: uuid.UUID | None, mention: WorkMentionDocument
) -> tuple[MatchResult, uuid.UUID | None, WorkFeatures]:
    """Resolve a mention to a canonical work (match/review/create) against
    ``state.work_candidates``, creating a new ``Work`` when needed. The
    mention's title is read for its composer, and the catalogue numbers and
    external ids its source states in ``raw`` join the title's (see
    :mod:`composer_warehouse.works.sources`). Shared by the create path
    (:func:`ingest_mention`) and the re-sighted path
    (``_ingest_work_mention`` in ``core.py``).

    On the re-sighted path a corrected title can resolve to a *different* work
    than last time, leaving the previously matched one with no mentions. That
    work is deliberately left in place: it keeps its aliases and stays a match
    candidate, and the work-dedupe pass is what folds such duplicates together.
    Deleting it here would be irreversible and would have to account for
    ``raw_work_mentions.candidate_work_id``, a second foreign key into
    ``works``."""
    features = mention_features(state.source.name, mention.title, mention.composer, mention.raw)
    result = resolve(features, state.work_candidates.of(composer_id))

    matched_work_id = result.work_id
    if result.status == "created":
        work = new_work(composer_id, mention.title, features)
        state.session.add(work)
        matched_work_id = work.id
        state.work_candidates.of(composer_id).append(Candidate(matched_work_id, features))

    return result, matched_work_id, features


def add_work_title_alias(state: IngestState, work_id: uuid.UUID, title: str, features: WorkFeatures) -> None:
    """Record ``title`` as an alias of ``work_id``, once per (work, title key, source)."""
    key = (work_id, features.normalized_title)
    if key not in state.existing_work_titles:
        state.session.add(
            WorkTitle(
                work_id=work_id,
                title=title,
                title_key=features.normalized_title,
                source_id=state.source.id,
            )
        )
        state.existing_work_titles.add(key)


def add_work_identifiers(
    state: IngestState, composer_id: uuid.UUID | None, work_id: uuid.UUID, features: WorkFeatures
) -> None:
    """Store the mention's identifiers on ``work_id`` (once per work, scheme,
    value and source), and let the work's candidate carry them, so the next
    mention -- in this run or the next, see ``works.load_candidates`` --
    matches on them."""
    for scheme, value in sorted(features.identifiers):
        value = value[:MAX_KEY_CHARS]
        key = (work_id, scheme, value)
        if key not in state.existing_work_identifiers:
            state.session.add(
                WorkIdentifier(work_id=work_id, scheme=scheme, value=value, source_id=state.source.id)
            )
            state.existing_work_identifiers.add(key)

    refresh_candidate(state.work_candidates.of(composer_id), work_id, features.identifiers)


def apply_premiere(state: IngestState, work_id: uuid.UUID, raw: dict[str, Any]) -> None:
    """Carry a mention's world premiere (``raw["premiere"]``) onto its work.

    The first source to report a work is seldom the one that knows its
    premiere, so a work without a date takes the first one offered; a work
    that has one only gives it up for an earlier (or more precise) date -- see
    :func:`composer_schema.premiere.supersedes`. The winning source's place and
    id go with the date, so the three always describe the same claim."""
    premiere = premiere_of(raw)
    if premiere is None:
        return
    date, place = premiere
    if not supersedes(date, state.work_premieres.get(work_id)):
        return
    state.session.execute(
        update(Work)
        .where(Work.id == work_id)
        .values(premiere_date=date, premiere_place=place, premiere_source_id=state.source.id)
    )
    state.work_premieres[work_id] = date


def ingest_mention(state: IngestState, mention: WorkMentionDocument) -> tuple[int, str]:
    """Resolve one work mention to a canonical work (match/review/create), store
    the mention with the decision, and save its title as an alias. Returns the
    new mention's (id, content hash)."""
    session = state.session
    composer_id: uuid.UUID | None = None
    if mention.composer:
        composer_id = get_or_create_entity(session, state.entities_by_key, "person", mention.composer)
        state.seen_entity_ids.add(composer_id)

    result, matched_work_id, features = resolve_mention(state, composer_id, mention)

    raw_json = json.dumps(mention.raw, ensure_ascii=False)
    mention_row = RawWorkMention(
        source_id=state.source.id,
        external_id=mention.id,
        raw_composer=mention.composer,
        raw_title=mention.title,
        raw=raw_json,
        composer_entity_id=composer_id,
        work_id=matched_work_id,
        match_status=result.status,
        match_score=result.score,
        match_method=result.method,
        candidate_work_id=result.candidate_work_id,
        first_run_id=state.run.id,
        last_run_id=state.run.id,
    )
    session.add(mention_row)
    session.flush()

    if matched_work_id is not None:
        add_work_title_alias(state, matched_work_id, mention.title, features)
        add_work_identifiers(state, composer_id, matched_work_id, features)
        apply_premiere(state, matched_work_id, mention.raw)

    return mention_row.id, content_hash(mention.title, mention.composer, raw_json)
