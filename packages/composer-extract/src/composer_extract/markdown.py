"""Turn a crawl record into the compact markdown the LLM reads.

The crawl stores each page as ``fit_markdown`` already, so there is nothing to
convert here. Oversized markdown is split on heading boundaries so each piece
fits one model call.
"""

from __future__ import annotations

from composer_crawler.records import CrawlRecord


def record_markdown(record: CrawlRecord) -> str:
    """The page's markdown, as captured at crawl time."""
    return record.markdown.strip()


def _sections(markdown: str) -> list[str]:
    """Split markdown into sections that each start at a heading line."""
    sections: list[str] = []
    current: list[str] = []
    for line in markdown.splitlines(keepends=True):
        if line.lstrip().startswith("#") and current:
            sections.append("".join(current))
            current = []
        current.append(line)
    if current:
        sections.append("".join(current))
    return sections


def _hard_split(text: str, max_chars: int) -> list[str]:
    return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]


def chunk_markdown(markdown: str, max_chars: int) -> list[str]:
    """Pieces of *markdown*, each within *max_chars*, split on heading boundaries.

    A single section longer than *max_chars* is hard-split by length; an empty or
    whitespace-only input yields no chunks.
    """
    text = markdown.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    buffer = ""
    for section in _sections(text):
        if len(section) > max_chars:
            if buffer:
                chunks.append(buffer)
                buffer = ""
            chunks.extend(_hard_split(section, max_chars))
        elif len(buffer) + len(section) > max_chars:
            chunks.append(buffer)
            buffer = section
        else:
            buffer += section
    if buffer:
        chunks.append(buffer)
    return [chunk.strip() for chunk in chunks if chunk.strip()]
