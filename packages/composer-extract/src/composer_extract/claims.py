"""Open-ended fact extraction: whatever a page states, as claims on entities.

The other two modes look for one shape and ignore a page that does not have it.
This one records what the page asserts as subject/predicate/object triples, so a
site can state something no scraper models and still contribute. Predicates are
folded onto a vocabulary by :mod:`.predicates` and literals onto the claims
table's conventions by :mod:`.values`; a work the page describes also yields a
mention, so it resolves to a canonical work row.

Meant to run *alongside* concerts or recordings over the same crawl rather than
instead of either.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from composer_crawler.records import CrawlRecord
from composer_schema import EntityDocument, SourceClaim, WorkMentionDocument

from .emit import LLM_SOURCE_MARKER, Document, emit_pages
from .facts import WORK_KIND, entity_kind, object_kind, repair, stated, title_key
from .ledger import DocumentLedger, LedgerContext, request_fingerprint
from .markdown import chunk_markdown, record_markdown
from .predicates import is_known, literal_form, normalize_predicate
from .prompt import CLAIMS_SYSTEM_PROMPT
from .resilience import extract_chunks
from .run import ExtractOptions, ExtractRun
from .schema import ExtractedFact, PageClaimExtraction
from .values import coerce_value

log = logging.getLogger(__name__)


class ClaimPageExtractor(Protocol):
    """Anything that turns a markdown chunk into a :class:`PageClaimExtraction`."""

    def extract_claim_page(self, markdown: str, metadata: dict[str, str]) -> PageClaimExtraction: ...


#: Longest literal stored as a claim. Claims are for facts you can query and
#: compare; an open extractor will sooner or later hand back a whole programme
#: note as a "value", which belongs in the record's ``raw`` payload instead of in
#: a column other passes read.
_MAX_CLAIM_VALUE_CHARS = 500

_CLAIMS_KIND = "work_profile"


@dataclass
class _Subject:
    """One thing the page makes statements about, and what it said."""

    kind: str
    label: str
    claims: list[SourceClaim] = field(default_factory=list)
    facts: list[dict[str, object]] = field(default_factory=list)
    long_values: dict[str, str] = field(default_factory=dict)


def _work_label(title: str, composer: str | None) -> str:
    """Work entities dedup on their label alone, and "Violin Concerto" belongs to
    Beethoven, Brahms, Sibelius and Tchaikovsky alike, so the composer qualifies
    it."""
    title = title.strip()
    composer = (composer or "").strip()
    return f"{composer}: {title}" if composer else title


def _composers_by_work(facts: Iterable[ExtractedFact]) -> dict[str, str]:
    """Work title -> composer, read off the page's ``composed`` edges so a work
    named as a subject gets the same qualified label as the one named as an
    object."""
    composers: dict[str, str] = {}
    for fact in facts:
        if normalize_predicate(fact.predicate) != "composed":
            continue
        if entity_kind(fact.object_kind, "") != WORK_KIND or not fact.object_label:
            continue
        composers.setdefault(title_key(fact.object_label), fact.subject.strip())
    return composers


def _labelled(kind: str, name: str, composers: dict[str, str]) -> str:
    if kind == WORK_KIND:
        return _work_label(name, composers.get(title_key(name)))
    return name.strip()


def _fact_predicate(fact: ExtractedFact, run: ExtractRun) -> str | None:
    """The vocabulary term this fact asserts, counting the ones nobody has
    curated yet so the run can report them."""
    predicate = normalize_predicate(fact.predicate)
    if predicate is None:
        return None
    if not is_known(predicate):
        run.stats.unknown_predicates[predicate] += 1
    return predicate


def _resolve(predicate: str, subject_kind: str) -> str:
    """Disambiguate a predicate that reads differently by subject.

    "Composed" attributes a piece when a person is the subject and dates it when
    the piece itself is — which is how the LA Phil's "At a Glance" block uses it.
    Resolved before the object slot is chosen, because the answer decides whether
    the object is an entity or a literal.
    """
    return literal_form(predicate) if subject_kind == WORK_KIND else predicate


def _add_fact(subject: _Subject, fact: ExtractedFact, predicate: str, composers: dict[str, str]) -> None:
    """Record one fact on its subject, as a claim where it can be one."""
    subject.facts.append(fact.model_dump())
    value_or_label = stated(fact)
    if not value_or_label:
        return
    predicate = _resolve(predicate, subject.kind)
    if (kind := object_kind(fact, predicate)) is not None:
        subject.claims.append(
            SourceClaim(
                predicate=predicate,
                object_kind=kind,
                object_label=_labelled(kind, value_or_label, composers),
            )
        )
        return
    value = coerce_value(predicate, value_or_label)
    if len(value) > _MAX_CLAIM_VALUE_CHARS:
        subject.long_values[predicate] = value
        return
    subject.claims.append(SourceClaim(predicate=predicate, value=value))


def _collect_subjects(facts: Iterable[ExtractedFact], run: ExtractRun) -> list[_Subject]:
    """Fold the page's facts into one subject per thing it talks about."""
    facts = repair(facts)
    composers = _composers_by_work(facts)
    subjects: dict[tuple[str, str], _Subject] = {}
    for fact in facts:
        if not fact.subject.strip():
            continue
        predicate = _fact_predicate(fact, run)
        if predicate is None:
            continue
        kind = entity_kind(fact.subject_kind)
        label = _labelled(kind, fact.subject, composers)
        if not label:
            continue
        subject = subjects.setdefault((kind, label), _Subject(kind=kind, label=label))
        _add_fact(subject, fact, predicate, composers)
    return [subject for subject in subjects.values() if subject.claims or subject.long_values]


def _dedup_claims(claims: Iterable[SourceClaim]) -> tuple[SourceClaim, ...]:
    """A model asked for every fact on a page will state some of them twice; the
    claims table has no unique constraint to catch it downstream."""
    seen: dict[SourceClaim, None] = {}
    for claim in claims:
        seen.setdefault(claim, None)
    return tuple(seen)


def _subject_docs(
    subjects: Iterable[_Subject], url: str, source_name: str, now: datetime
) -> Iterator[EntityDocument]:
    for subject in subjects:
        yield EntityDocument(
            # Namespaced, because the concert and recording passes key a person on
            # ``person:<name>`` and a crawl can enable claims alongside either. An
            # id already ingested is treated as a re-sighting, and a re-sighting
            # adds no claims — these documents would land as no-ops.
            id=f"claims:{subject.kind}:{subject.label}",
            url=url,
            source_name=source_name,
            ingested_at=now,
            name=subject.label,
            kind=subject.kind,
            raw={
                "_source": LLM_SOURCE_MARKER,
                "_kind": _CLAIMS_KIND,
                "url": url,
                "facts": subject.facts,
                **({"long_values": subject.long_values} if subject.long_values else {}),
            },
            claims=_dedup_claims(subject.claims),
        )


def _work_profile_mentions(
    subjects: Iterable[_Subject], url: str, source_name: str, now: datetime
) -> Iterator[WorkMentionDocument]:
    """A mention per work the page describes, so ``works/match`` resolves it to a
    canonical work row the way a concert programme's works are resolved.

    Marked ``_kind: "work_profile"`` so neither derive pass mistakes it for a
    performance or a release.
    """
    for index, subject in enumerate(s for s in subjects if s.kind == WORK_KIND):
        composer, _, title = subject.label.partition(": ")
        if not title:
            composer, title = "", subject.label
        yield WorkMentionDocument(
            id=f"{url}#work{index}",
            url=url,
            source_name=source_name,
            ingested_at=now,
            title=title,
            composer=composer or None,
            raw={
                "_source": LLM_SOURCE_MARKER,
                "_kind": _CLAIMS_KIND,
                "url": url,
                "title": title,
                "composer": composer or None,
            },
        )


def _page_facts(record: CrawlRecord, extractor: ClaimPageExtractor, run: ExtractRun) -> list[ExtractedFact]:
    markdown = record_markdown(record)
    chunks = chunk_markdown(markdown, run.max_chars)
    log.debug("extract %s: %d chunk(s) from %d chars", record.final_url, len(chunks), len(markdown))
    pages = extract_chunks(
        chunks, extractor.extract_claim_page, record.metadata, url=record.final_url, stats=run.stats
    )
    return [fact for page in pages for fact in page.facts]


def _emit_claims(
    record: CrawlRecord, extractor: ClaimPageExtractor, run: ExtractRun
) -> Iterator[EntityDocument | WorkMentionDocument]:
    subjects = _collect_subjects(_page_facts(record, extractor, run), run)
    if not subjects:
        return
    url = record.final_url
    run.stats.claims += sum(len(_dedup_claims(s.claims)) for s in subjects)
    yield from _subject_docs(subjects, url, run.source_name, run.now)
    yield from _work_profile_mentions(subjects, url, run.source_name, run.now)


def extract_claim_documents(
    records: Iterable[CrawlRecord],
    *,
    source_name: str,
    extractor: ClaimPageExtractor,
    options: ExtractOptions | None = None,
    ledger: DocumentLedger | None = None,
) -> Iterator[Document]:
    """Yield entity/work-mention documents from crawled *records* (claims mode)."""
    run = ExtractRun.start(source_name, options)
    context = None
    if ledger is not None:
        fingerprint = request_fingerprint(extractor, CLAIMS_SYSTEM_PROMPT, PageClaimExtraction)
        context = LedgerContext(ledger, "claims", fingerprint)
    yield from emit_pages(
        records, lambda record, r: _emit_claims(record, extractor, r), run, ledger_context=context
    )
