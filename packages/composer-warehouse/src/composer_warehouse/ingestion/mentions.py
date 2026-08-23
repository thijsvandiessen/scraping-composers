import json
import uuid

from composer_models import RawWorkMention, Work, WorkTitle
from composer_schema import WorkMentionDocument

from ..works import Candidate, MatchResult, WorkFeatures, extract_features, resolve
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
    state: IngestState, composer_id: uuid.UUID | None, title: str
) -> tuple[MatchResult, uuid.UUID | None, WorkFeatures]:
    """Resolve a mention's title to a canonical work (match/review/create)
    against ``state.work_candidates``, creating a new ``Work`` when needed.
    Shared by the create path (:func:`ingest_mention`) and the re-sighted
    path (``_ingest_work_mention`` in ``core.py``).

    On the re-sighted path a corrected title can resolve to a *different* work
    than last time, leaving the previously matched one with no mentions. That
    work is deliberately left in place: it keeps its aliases and stays a match
    candidate, and the work-dedupe pass is what folds such duplicates together.
    Deleting it here would be irreversible and would have to account for
    ``raw_work_mentions.candidate_work_id``, a second foreign key into
    ``works``."""
    features = extract_features(title)
    result = resolve(features, state.work_candidates.get(composer_id, []))

    matched_work_id = result.work_id
    if result.status == "created":
        work = new_work(composer_id, title, features)
        state.session.add(work)
        matched_work_id = work.id
        state.work_candidates.setdefault(composer_id, []).append(Candidate(matched_work_id, features))

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


def ingest_mention(state: IngestState, mention: WorkMentionDocument) -> tuple[int, str]:
    """Resolve one work mention to a canonical work (match/review/create), store
    the mention with the decision, and save its title as an alias. Returns the
    new mention's (id, content hash)."""
    session = state.session
    composer_id: uuid.UUID | None = None
    if mention.composer:
        composer_id = get_or_create_entity(session, state.entities_by_key, "person", mention.composer)
        state.seen_entity_ids.add(composer_id)

    result, matched_work_id, features = resolve_mention(state, composer_id, mention.title)

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

    return mention_row.id, content_hash(mention.title, mention.composer, raw_json)
