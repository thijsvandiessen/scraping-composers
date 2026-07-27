"""Keep one unusable model response from killing a whole extract run.

A local model occasionally returns JSON that cannot be validated — most often a
truncated answer after it starts repeating itself. Validation is never relaxed to
accommodate that: :func:`extract_chunks` retries the offending chunk once, split
in half (a truncated answer usually means the chunk was too big), then skips it,
counting and logging the failure with the page's url so the run carries on.

Only :class:`ValueError` counts as unusable output — pydantic's
``ValidationError`` subclasses it, as does the empty-response guard in
:mod:`.client`. Transport errors (Ollama down, request timed out) are not, and
still abort the run; so does a long enough streak of consecutive failures. A run
that quietly extracts nothing is worse than one that fails loudly.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import TypeVar

from composer_config import settings
from pydantic import BaseModel

from .markdown import chunk_markdown

log = logging.getLogger(__name__)

_M = TypeVar("_M", bound=BaseModel)

#: Longest error text kept in a log line. An unvalidatable response embeds the
#: model's entire output in the exception — precisely what must not reach the log.
_MAX_ERROR_CHARS = 200


class ExtractAborted(RuntimeError):
    """Too many chunks in a row produced unusable output; the run gave up."""


@dataclass
class ExtractStats:
    """What a run did, so an unattended one can be judged after the fact.

    ``failed`` counts pieces of text given up on (the halves of a retried chunk
    count separately); ``retried`` counts chunks that were split and re-asked.
    """

    pages: int = 0
    chunks: int = 0
    retried: int = 0
    failed: int = 0
    consecutive_failures: int = 0

    def summary(self) -> str:
        return f"{self.pages} pages, {self.chunks} chunks, {self.retried} retried, {self.failed} failed"


def _brief(exc: Exception) -> str:
    """A one-line, bounded rendering of *exc* — validation errors quote the whole
    model response, which can run to tens of thousands of lines."""
    text = " ".join(str(exc).split())
    return text if len(text) <= _MAX_ERROR_CHARS else text[:_MAX_ERROR_CHARS] + "…"


def _log_unusable(url: str, exc: Exception, action: str, chars: int) -> None:
    """Report a chunk the model could not answer usably. The chunk size is part of
    the line because an oversized chunk is the usual reason."""
    log.warning(
        "extract %s: unusable model output for %d chars (%s: %s); %s",
        url,
        chars,
        type(exc).__name__,
        _brief(exc),
        action,
    )


def _halves(chunk: str) -> list[str]:
    """*chunk* split in two on a heading boundary, or nothing if it cannot be split."""
    pieces = chunk_markdown(chunk, max(len(chunk) // 2, 1))
    return pieces if len(pieces) > 1 else []


def _extract_halves(
    pieces: list[str],
    call: Callable[[str, dict[str, str]], _M],
    metadata: dict[str, str],
    *,
    url: str,
    stats: ExtractStats,
) -> list[_M]:
    """The single retry: whichever halves of a failed chunk validate. Empty when
    the chunk could not be split, or when neither half validated either."""
    if not pieces:
        stats.failed += 1
        return []
    stats.retried += 1
    log.debug("extract %s: retrying on %d half/halves", url, len(pieces))
    results: list[_M] = []
    for piece in pieces:
        try:
            results.append(call(piece, metadata))
        except ValueError as exc:
            stats.failed += 1
            _log_unusable(url, exc, "skipping", len(piece))
    return results


def _extract_one(
    chunk: str,
    call: Callable[[str, dict[str, str]], _M],
    metadata: dict[str, str],
    *,
    url: str,
    stats: ExtractStats,
) -> list[_M]:
    """The extractions for one chunk: its answer, else what a single retry salvages."""
    try:
        return [call(chunk, metadata)]
    except ValueError as exc:
        pieces = _halves(chunk)
        _log_unusable(url, exc, "retrying on halves" if pieces else "skipping", len(chunk))
        return _extract_halves(pieces, call, metadata, url=url, stats=stats)


def _note_streak(stats: ExtractStats, limit: int, url: str) -> None:
    """Say so while a run is degrading, not only once it has already given up: a
    streak halfway to the limit is the moment to go and look at the model."""
    if limit > 0 and stats.consecutive_failures == max(limit // 2, 1):
        log.warning(
            "extract %s: %d chunk(s) in a row unusable; the run aborts at %d",
            url,
            stats.consecutive_failures,
            limit,
        )


def extract_chunks(
    chunks: Iterable[str],
    call: Callable[[str, dict[str, str]], _M],
    metadata: dict[str, str],
    *,
    url: str,
    stats: ExtractStats,
) -> Iterator[_M]:
    """Yield one validated extraction per chunk of *url*, tolerating bad answers.

    Raises :class:`ExtractAborted` once ``$EXTRACT_MAX_CONSECUTIVE_FAILURES``
    chunks in a row have yielded nothing usable (0 disables the check).
    """
    limit = settings.extract_max_consecutive_failures
    for index, chunk in enumerate(chunks, start=1):
        stats.chunks += 1
        log.debug("extract %s: chunk %d (%d chars)", url, index, len(chunk))
        started = time.monotonic()
        results = _extract_one(chunk, call, metadata, url=url, stats=stats)
        log.debug(
            "extract %s: chunk %d yielded %d extraction(s) in %.1fs",
            url,
            index,
            len(results),
            time.monotonic() - started,
        )
        stats.consecutive_failures = 0 if results else stats.consecutive_failures + 1
        _note_streak(stats, limit, url)
        if limit > 0 and stats.consecutive_failures >= limit:
            raise ExtractAborted(
                f"{stats.consecutive_failures} chunks in a row produced unusable output "
                f"(last: {url}); giving up rather than extracting nothing"
            )
        yield from results
