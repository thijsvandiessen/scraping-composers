"""LLM (Ollama) extraction of concerts and performers from crawled pages.

Reads bronze crawl records, extracts concert programmes with a local model, and
emits the warehouse's :class:`~composer_schema.WorkMentionDocument` and
:class:`~composer_schema.EntityDocument` types so the existing
``process -> derive_concerts -> promote`` pipeline consumes them unchanged.
"""

from __future__ import annotations

from .client import OllamaExtractor, OllamaTuning
from .extract import (
    PageExtractor,
    RecordingPageExtractor,
    extract_documents,
    extract_recording_documents,
)
from .markdown import chunk_markdown, record_markdown
from .resilience import ExtractAborted, ExtractStats
from .run import ExtractOptions
from .schema import (
    ExtractedArtist,
    ExtractedConcert,
    ExtractedRecording,
    ExtractedSoloist,
    ExtractedWork,
    PageExtraction,
    PageRecordingExtraction,
)

__all__ = [
    "ExtractAborted",
    "ExtractOptions",
    "ExtractStats",
    "ExtractedArtist",
    "ExtractedConcert",
    "ExtractedRecording",
    "ExtractedSoloist",
    "ExtractedWork",
    "OllamaExtractor",
    "OllamaTuning",
    "PageExtraction",
    "PageExtractor",
    "PageRecordingExtraction",
    "RecordingPageExtractor",
    "chunk_markdown",
    "extract_documents",
    "extract_recording_documents",
    "record_markdown",
]
