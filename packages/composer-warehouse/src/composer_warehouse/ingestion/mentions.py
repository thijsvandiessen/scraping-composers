import json
import uuid

from composer_schema import WorkMentionDocument
from sqlalchemy.orm import Session

from ..models import RawWorkMention, Work, WorkTitle
from ..works import Candidate, WorkFeatures, extract_features, resolve
from .entities import get_or_create_entity


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


def ingest_mention(  # noqa: PLR0913
    session: Session,
    mention: WorkMentionDocument,
    source_id: int,
    run_id: int,
    entities_by_key: dict[tuple[str, str], uuid.UUID],
    seen_entity_ids: set[uuid.UUID],
    work_candidates: dict[uuid.UUID | None, list[Candidate]],
    existing_work_titles: set[tuple[uuid.UUID, str]],
) -> int:
    """Resolve one work mention to a canonical work (match/review/create), store
    the mention with the decision, and save its title as an alias. Returns the
    new mention's id."""
    composer_id: uuid.UUID | None = None
    if mention.composer:
        composer_id = get_or_create_entity(session, entities_by_key, "person", mention.composer)
        seen_entity_ids.add(composer_id)

    features = extract_features(mention.title)
    result = resolve(features, work_candidates.get(composer_id, []))

    matched_work_id = result.work_id
    if result.status == "created":
        work = new_work(composer_id, mention.title, features)
        session.add(work)
        matched_work_id = work.id
        work_candidates.setdefault(composer_id, []).append(Candidate(matched_work_id, features))

    mention_row = RawWorkMention(
        source_id=source_id,
        external_id=mention.id,
        raw_composer=mention.composer,
        raw_title=mention.title,
        raw=json.dumps(mention.raw, ensure_ascii=False),
        composer_entity_id=composer_id,
        work_id=matched_work_id,
        match_status=result.status,
        match_score=result.score,
        match_method=result.method,
        candidate_work_id=result.candidate_work_id,
        first_run_id=run_id,
        last_run_id=run_id,
    )
    session.add(mention_row)
    session.flush()

    # save the raw title as an alias of the matched/created work
    if matched_work_id is not None:
        key = (matched_work_id, features.normalized_title)
        if key not in existing_work_titles:
            session.add(
                WorkTitle(
                    work_id=matched_work_id,
                    title=mention.title,
                    title_key=features.normalized_title,
                    source_id=source_id,
                )
            )
            existing_work_titles.add(key)

    return mention_row.id
