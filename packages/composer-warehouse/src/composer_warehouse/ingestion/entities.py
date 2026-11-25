import logging
import re
import uuid
from datetime import datetime

from composer_models import Entity, Source
from composer_models.normalize import entity_key, entity_uuid
from composer_schema import resolve_entity_kind
from composer_schema.spelling import apply_bracketed_spellings
from sqlalchemy import select, update
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def get_or_create_source(session: Session, name: str, base_url: str) -> Source:
    source = session.scalar(select(Source).where(Source.name == name))
    if source is None:
        source = Source(name=name, base_url=base_url)
        session.add(source)
        session.flush()
    return source


PLACEHOLDER_PERSON_KEYS = frozenset(
    {
        # no known composer
        "anon", "anonym", "anonyme", "anonimo", "anonymous", "anonymus",
        "traditional", "trad", "folk song",
        "unknown", "unknown artist", "unknown composer", "unknown composer author",
        "unbekannt", "onbekend", "inconnu", "nn",
        # not one person at all
        "various", "various artists", "various composers", "audience",
    }
)  # fmt: skip
"""Dedup keys of the names sources write where a person would go but none is
known: "Anonymous", "Traditional", "Various", "N.N.", a live album's
"Audience".

Sources keep them verbatim (deciding they are not people is curation, see
:mod:`composer_scrapers.nyphil.people`), so the warehouse decides here. Minted
as a person, each one collects every anonymous work, compilation and applause
track in the corpus (Anonymous alone had 3,907 credits) and enters the person
dedupe pass as a high-reach composer. Matching is exact on the whole key, so
the ensemble "Anonymous 4" and the theorist "Anonymous IV" stay people. The
attribution itself is not lost: a mention keeps it in ``raw_composer``."""


#: ``[Mozart](https://theclassicreview.com/tag/mozart/)``: a tag link kept as markdown.
_MARKDOWN_LINK = re.compile(r"\[([^\[\]]+)\]\(https?://[^()\s]*\)")
#: ``[Senfl, Ludwig]``: the whole name bracketed, an editor's attribution.
_BRACKETED = re.compile(r"^\[([^\[\]]+)\]$")
#: ``Frédéric Chopin (arr. Liszt)``, ``Bach (Arr: Gounod)``, ``(arranged by …)``,
#: ``(arrangement)``, ``Crüger, Johann (nach Loys Bourgeois)``: the arranger or
#: the model, not part of the name. Whole words only, so "(Arrigo Boito)" stays.
_ARRANGER_NOTE = re.compile(r"\s*\((?:arr|arranged|arrangement|nach)\b[^()]*\)\s*$", re.IGNORECASE)
#: ``Jenkins - Jenkins, Karl (Arranger); Häusler, Walter (Arranger)``: a publisher's
#: credit line, the composer before the " - " and the arrangers and editors after.
_CREDIT_LINE = re.compile(r"^(.+?) - .*\((?:Arranger|Editor)\)", re.IGNORECASE)
#: A bare URL (Wikidata's ``.well-known/genid/…`` "unknown value") names no one.
_URL = re.compile(r"^https?://\S+$")
#: What should not survive :func:`clean_person_label`; seeing it means a source
#: writes a shape nobody has handled yet.
_NOISE = re.compile(r"\]\(http|https?://|\[")


def clean_person_label(label: str) -> str:
    """``label`` with the annotations sources wrap around a person's name removed.

    A markdown tag link becomes its text, a bracketed respelling is applied
    (``Bartok [Bartók], Béla`` -> "Bartók, Béla", see
    :mod:`composer_schema.spelling`), a wholly bracketed name becomes its inside,
    a trailing arranger or "nach" note is dropped, and of a publisher's credit
    line (``Britten - Tircuit, Heuwell (Arranger)``) only the composer is kept
    (#457). Bronze keeps snapshots written before a scraper learnt any of this,
    and a rebuild replays them, so the warehouse applies it to every label.
    Each leaves a label
    that shares no key with the plain name: ``[Mozart](…/tag/mozart/)`` is keyed
    "mozarthttpstheclassicreviewcomtagmozart" and collects a year conflict
    instead of folding into the bare "Mozart". Sources keep the verbatim string
    (bronze, ``raw_composer``), so this is the warehouse's reading of it, like
    :func:`is_placeholder_person`. A label that would clean to nothing is kept.
    """
    cleaned = apply_bracketed_spellings(_MARKDOWN_LINK.sub(r"\1", label))
    cleaned = _BRACKETED.sub(r"\1", cleaned.strip())
    cleaned = _CREDIT_LINE.sub(r"\1", cleaned)
    cleaned = _ARRANGER_NOTE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or label


def is_placeholder_person(label: str) -> bool:
    """Whether ``label`` names no particular person: a placeholder (see
    :data:`PLACEHOLDER_PERSON_KEYS`) or a bare URL, such as the Wikidata
    "unknown value" (``…/.well-known/genid/…``) older bronze snapshots carry."""
    cleaned = clean_person_label(label)
    return bool(_URL.match(cleaned)) or entity_key("person", cleaned, None) in PLACEHOLDER_PERSON_KEYS


def get_or_create_entity(
    session: Session,
    cache: dict[tuple[str, str], uuid.UUID],
    kind: str,
    label: str,
    wikidata_id: str | None = None,
) -> uuid.UUID:
    """The id of the entity *label* names, creating it on first sight.

    Every entity in silver is minted here — a source record, a claim's object,
    a mention's composer — which is why the one kind nobody reports reliably is
    settled here too: a participant credit reaches ingest as a ``person``
    whether it names a musician or an orchestra, so
    :func:`~composer_schema.resolve_entity_kind` re-reads the label before the
    kind is baked into the entity's uuid (see #174). The key follows the
    resolved kind: a genre or movement with a QID is keyed on the QID alone
    (see :func:`~composer_models.normalize.entity_key`). A person's label is
    cleaned first (:func:`clean_person_label`), and one that still carries
    markup or a URL is logged, not dropped, so a new source's shape is noticed.
    """
    if kind == "person":
        label = clean_person_label(label)
    kind = resolve_entity_kind(kind, label)
    key = entity_key(kind, label, wikidata_id)
    entity_id = cache.get((kind, key))
    if entity_id is None:
        if kind == "person" and _NOISE.search(label):
            log.warning("person label still carries markup or a URL: %r", label)
        entity_id = entity_uuid(kind, key)
        session.add(Entity(id=entity_id, kind=kind, dedup_key=key, label=label))
        cache[(kind, key)] = entity_id
    return entity_id


def flush_entity_timestamps(
    session: Session,
    seen_ids: set[uuid.UUID],
    edited_ids: set[uuid.UUID],
    now: datetime,
) -> None:
    """Bulk-update last_ingested_at / last_edited_at for entities touched in the current batch."""
    if seen_ids:
        session.execute(update(Entity).where(Entity.id.in_(seen_ids)).values(last_ingested_at=now))
    if edited_ids:
        session.execute(update(Entity).where(Entity.id.in_(edited_ids)).values(last_edited_at=now))
