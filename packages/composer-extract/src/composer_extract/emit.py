"""What every extraction mode shares: the document stream and the page loop.

Each mode (:mod:`.extract` for concerts and recordings, :mod:`.claims` for open
facts) decides what one page contributes; this module owns the part that is the
same either way, so the modes stay independent of each other.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from composer_crawler.records import CrawlRecord
from composer_schema import EntityDocument, WorkMentionDocument

from .run import ExtractRun

#: The marker every LLM-extracted payload carries in ``raw``, whatever the mode.
#: The derive passes dispatch on it together with a mode-specific ``_kind``.
LLM_SOURCE_MARKER = "llm"

#: What one page contributes to the output stream.
Document = EntityDocument | WorkMentionDocument
#: A mode's per-page emitter, bound to its extractor by each mode's entry point.
Emitter = Callable[[CrawlRecord, ExtractRun], Iterator[Document]]


def emit_pages(records: Iterable[CrawlRecord], emit: Emitter, run: ExtractRun) -> Iterator[Document]:
    """Drive *emit* over every record, counting what each page produced.

    The count is tallied while the documents are handed on rather than by
    collecting them, so the whole extract stays lazy: nothing is held in memory
    just to be able to report it.
    """
    for record in records:
        emitted = 0
        for document in emit(record, run):
            emitted += 1
            yield document
        run.mark_page(record.final_url, emitted)
    run.finish()
