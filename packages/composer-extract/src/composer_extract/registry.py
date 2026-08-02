"""The extract kinds a crawl can enable, and the entry point behind each.

Every caller that used to branch on ``extract_kind`` — the CLI's ``extract``
command, the admin API's background extract, the crawl config's validation —
looks the kind up here instead, so a fourth mode is one entry in this dict rather
than another ternary in three files.

A crawl with several kinds enabled runs each of them over the same pages and
chains the results into one snapshot.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from typing import Any

from composer_crawler.config import DEFAULT_EXTRACT_KIND, EXTRACT_KINDS
from composer_crawler.records import CrawlRecord

from .claims import extract_claim_documents
from .emit import Document
from .extract import extract_documents, extract_recording_documents
from .run import ExtractOptions

#: Signature every entry shares: records in, warehouse documents out.
ExtractEntryPoint = Callable[..., Iterator[Document]]

EXTRACTORS: dict[str, ExtractEntryPoint] = {
    "concerts": extract_documents,
    "recordings": extract_recording_documents,
    "claims": extract_claim_documents,
}

# A kind a config may declare but nothing here implements would only surface as a
# KeyError partway through an overnight extract, so the two halves are checked
# against each other at import.
if set(EXTRACTORS) != set(EXTRACT_KINDS):
    raise RuntimeError(
        f"extract kinds out of step: config declares {sorted(EXTRACT_KINDS)}, "
        f"registry implements {sorted(EXTRACTORS)}"
    )

__all__ = [
    "DEFAULT_EXTRACT_KIND",
    "EXTRACTORS",
    "EXTRACT_KINDS",
    "ExtractEntryPoint",
    "extract_all",
    "options_per_kind",
    "summarize",
]


def extract_all(
    kinds: Iterable[str],
    records: Callable[[], Iterable[CrawlRecord]],
    *,
    source_name: str,
    extractor: Any,
    options: dict[str, ExtractOptions],
) -> Iterator[Document]:
    """Run every kind in *kinds* over the crawl's pages, in the order given.

    *records* is a factory, not an iterable: a snapshot is streamed off disk one
    record at a time, and a second kind handed the exhausted iterator would
    silently extract nothing. Each kind gets its own entry in *options*, so the
    caller can report per kind afterwards rather than reading one set of counters
    that every pass has added to.
    """
    for kind in kinds:
        yield from EXTRACTORS[kind](
            records(), source_name=source_name, extractor=extractor, options=options[kind]
        )


def options_per_kind(kinds: Iterable[str]) -> dict[str, ExtractOptions]:
    """A fresh :class:`ExtractOptions` — and so a fresh stats block — per kind."""
    return {kind: ExtractOptions() for kind in kinds}


def summarize(options: dict[str, ExtractOptions]) -> str:
    """One line accounting for every kind a run applied.

    Names the predicates the claims pass coined alongside each kind's counters:
    an unattended run's log is the only place they surface, and they are the
    queue for growing :mod:`.predicates`' vocabulary.
    """
    parts = []
    for kind, opts in options.items():
        unknown = opts.stats.unknown_summary()
        line = f"{kind}: {opts.stats.summary()}"
        parts.append(f"{line} (new predicates: {unknown})" if unknown else line)
    return "; ".join(parts)
