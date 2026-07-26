"""LLM (Ollama) extraction of concerts and performers from crawled pages.

Reads bronze crawl records, extracts concert programmes with a local model, and
emits the warehouse's :class:`~composer_schema.WorkMentionDocument` and
:class:`~composer_schema.EntityDocument` types so the existing
``process -> derive_concerts -> promote`` pipeline consumes them unchanged.
"""

from __future__ import annotations

from .client import OllamaExtractor
from .extract import PageExtractor, extract_documents
from .markdown import chunk_markdown, record_markdown
from .schema import (
    ExtractedConcert,
    ExtractedSoloist,
    ExtractedWork,
    PageExtraction,
)

__all__ = [
    "ExtractedConcert",
    "ExtractedSoloist",
    "ExtractedWork",
    "OllamaExtractor",
    "PageExtraction",
    "PageExtractor",
    "chunk_markdown",
    "extract_documents",
    "record_markdown",
]
