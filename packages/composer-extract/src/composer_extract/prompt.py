"""Prompt construction for the concert/recording/claim extraction calls.

The system prompts pin the model to faithful extraction (no invention); the user
prompt carries the page's title/description as context followed by its markdown.
"""

from __future__ import annotations

from .predicates import vocabulary_hint

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

RECORDING_SYSTEM_PROMPT = (
    "You extract classical-music recording/album releases from the text of a single web page. "
    "Return only recordings that are actually described on the page. Do not invent recordings, "
    "works, performers, labels, dates, or catalogue numbers. If the page describes no recording, "
    "return an empty list.\n\n"
    "For each recording capture: the album/recording title; the release date as ISO-8601 "
    "(YYYY-MM-DD) when derivable (else null); the record label (else null); the label's catalogue "
    "number (else null); the format such as CD/Vinyl/Digital (else null); the artists, each with "
    "their role ('conductor', 'soloist', or 'ensemble') and, for soloists, their instrument or "
    "voice when stated; and the works on the recording, each with its composer when stated. Use "
    "names exactly as written on the page. Do not translate or reformat names."
)

CLAIMS_SYSTEM_PROMPT = (
    "You extract factual statements about classical music from the text of a single web page. "
    "Return only what the page actually states. Do not infer, complete, or add anything you know "
    "independently of the page. If the page states no facts, return an empty list.\n\n"
    "Express each statement as a triple: the subject (who or what it is about, named exactly as "
    "the page names it), the kind of subject ('person', 'work', 'ensemble', or 'place'), and a "
    "predicate. Put the stated value in 'value' when it is a literal such as a date, a number, or "
    "a text. When the object is itself a named thing, leave 'value' null and give 'object_kind' "
    "('work', 'place', 'profession', 'genre') with 'object_label' instead.\n\n"
    "Prefer these predicates whenever one of them fits: "
    f"{vocabulary_hint()}. "
    "If a page states something none of them covers, coin a short lowercase snake_case predicate "
    "for it. Never use a predicate to mean something other than its plain reading.\n\n"
    "Set 'subject_kind' to what the subject *is*, not to what the page is about: a piece of "
    "music is 'work' even when the page is named after it, and only a human being is 'person'.\n\n"
    "Attribution always runs from the composer to the piece. On a page headed 'Violin Concerto' "
    "by Ludwig van Beethoven, the attribution is subject 'Ludwig van Beethoven' (person), "
    "predicate 'composed', object_kind 'work', object_label 'Violin Concerto' — never the other "
    "way round. Facts about the piece itself (when it was written, how long it lasts, what it is "
    "scored for) take the piece as their subject, with subject_kind 'work'.\n\n"
    "Dates as ISO-8601 (YYYY-MM-DD) when the page gives a full one. Durations in minutes. Use "
    "names exactly as written on the page; do not translate or reformat them."
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
