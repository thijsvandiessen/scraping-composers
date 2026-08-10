"""LLM (Ollama) extraction of concerts and performers from crawled pages.

Reads bronze crawl records, extracts concert programmes with a local model, and
emits the warehouse's :class:`~composer_schema.WorkMentionDocument` and
:class:`~composer_schema.EntityDocument` types so the existing
``process -> derive_concerts -> promote`` pipeline consumes them unchanged.
"""

from __future__ import annotations

from .cache import ExtractCache, open_cache, request_key
from .claims import ClaimPageExtractor, extract_claim_documents
from .client import OllamaExtractor, OllamaTuning
from .extract import PageExtractor, RecordingPageExtractor, extract_documents, extract_recording_documents
from .ledger import DocumentLedger, LedgerContext, LedgerKey, open_ledger, request_fingerprint
from .markdown import chunk_markdown, record_markdown
from .predicates import ALIASES, DENYLIST, VOCABULARY, is_known, normalize_predicate
from .registry import (
    DEFAULT_EXTRACT_KIND,
    EXTRACT_KINDS,
    EXTRACTORS,
    extract_all,
    options_per_kind,
    summarize,
)
from .resilience import ExtractAborted, ExtractStats
from .run import ExtractOptions
from .schema import (
    ExtractedArtist,
    ExtractedConcert,
    ExtractedFact,
    ExtractedRecording,
    ExtractedSoloist,
    ExtractedWork,
    PageClaimExtraction,
    PageExtraction,
    PageRecordingExtraction,
)
from .values import coerce_value

__all__ = [
    "ALIASES",
    "DEFAULT_EXTRACT_KIND",
    "DENYLIST",
    "EXTRACTORS",
    "EXTRACT_KINDS",
    "VOCABULARY",
    "ClaimPageExtractor",
    "DocumentLedger",
    "ExtractAborted",
    "ExtractCache",
    "ExtractOptions",
    "ExtractStats",
    "ExtractedArtist",
    "ExtractedConcert",
    "ExtractedFact",
    "ExtractedRecording",
    "ExtractedSoloist",
    "ExtractedWork",
    "LedgerContext",
    "LedgerKey",
    "OllamaExtractor",
    "OllamaTuning",
    "PageClaimExtraction",
    "PageExtraction",
    "PageExtractor",
    "PageRecordingExtraction",
    "RecordingPageExtractor",
    "chunk_markdown",
    "coerce_value",
    "extract_all",
    "extract_claim_documents",
    "extract_documents",
    "extract_recording_documents",
    "is_known",
    "normalize_predicate",
    "open_cache",
    "open_ledger",
    "options_per_kind",
    "record_markdown",
    "request_fingerprint",
    "request_key",
    "summarize",
]
