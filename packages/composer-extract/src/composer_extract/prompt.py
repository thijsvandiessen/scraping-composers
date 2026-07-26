"""Prompt construction for the concert/performer extraction call.

The system prompt pins the model to faithful extraction (no invention); the user
prompt carries the page's title/description as context followed by its markdown.
"""

from __future__ import annotations

SYSTEM_PROMPT = (
    "You extract classical-music concert information from the text of a single web page. "
    "Return only concerts that are actually described on the page. Do not invent concerts, "
    "works, performers, dates, or venues. If the page describes no concert, return an empty "
    "list.\n\n"
    "For each concert capture: the date as ISO-8601 (YYYY-MM-DD) when derivable (else null); "
    "the venue/hall (else null); the conductor name(s); the soloists with their instrument or "
    "voice when stated; and the works performed, each with its composer when stated. Use names "
    "exactly as written on the page. Do not translate or reformat names."
)

# Metadata keys worth surfacing to the model as page context.
_CONTEXT_KEYS = ("title", "description", "og:title", "og:description")


def _context_lines(metadata: dict[str, str]) -> list[str]:
    return [f"{key}: {metadata[key]}" for key in _CONTEXT_KEYS if metadata.get(key)]


def build_user_prompt(markdown: str, metadata: dict[str, str]) -> str:
    """The user message: page context (from metadata) then the page markdown."""
    parts: list[str] = []
    context = _context_lines(metadata)
    if context:
        parts.append("Page metadata:\n" + "\n".join(context))
    parts.append("Page content (markdown):\n" + markdown)
    return "\n\n".join(parts)
