"""What every extraction mode shares: the document stream and the page loop.

Each mode (:mod:`.extract` for concerts and recordings, :mod:`.claims` for open
facts) decides what one page contributes; this module owns the part that is the
same either way, so the modes stay independent of each other.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from composer_crawler.records import CrawlRecord, record_content_hash
from composer_schema import EntityDocument, WorkMentionDocument

from .ledger import LedgerContext
from .run import ExtractRun

#: The marker every LLM-extracted payload carries in ``raw``, whatever the mode.
#: The derive passes dispatch on it together with a mode-specific ``_kind``.
LLM_SOURCE_MARKER = "llm"

#: What one page contributes to the output stream.
Document = EntityDocument | WorkMentionDocument
#: A mode's per-page emitter, bound to its extractor by each mode's entry point.
Emitter = Callable[[CrawlRecord, ExtractRun], Iterator[Document]]


def emit_pages(
    records: Iterable[CrawlRecord],
    emit: Emitter,
    run: ExtractRun,
    *,
    ledger_context: LedgerContext | None = None,
) -> Iterator[Document]:
    """Drive *emit* over every record, counting what each page produced.

    The count is tallied while the documents are handed on rather than by
    collecting them, so the whole extract stays lazy: nothing is held in memory
    just to be able to report it — except when *ledger_context* is given,
    where a page's documents are buffered just long enough to also hand them
    to :meth:`~.ledger.DocumentLedger.put` (a page emits few documents, so this
    costs nothing next to the model call it lets a later run skip).

    When *ledger_context* is given, a page whose content hash matches what is
    already on record for its kind and extractor fingerprint is served
    straight from the ledger — *emit* is never called for it, so nothing is
    chunked, prompted, or sent to the model.
    """
    for record in records:
        page_hash = record_content_hash(record)
        if ledger_context is not None:
            key = ledger_context.key_for(
                source=run.source_name, final_url=record.final_url, content_sha256=page_hash
            )
            carried = ledger_context.ledger.get(key)
            if carried is not None:
                run.mark_page(record.final_url, len(carried), carried_forward=True)
                yield from carried
                continue
        emitted: list[Document] = list(emit(record, run))
        yield from emitted
        run.mark_page(record.final_url, len(emitted))
        if ledger_context is not None:
            key = ledger_context.key_for(
                source=run.source_name, final_url=record.final_url, content_sha256=page_hash
            )
            ledger_context.ledger.put(key, emitted)
    run.finish()
