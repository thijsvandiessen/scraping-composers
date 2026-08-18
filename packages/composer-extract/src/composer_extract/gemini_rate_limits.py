"""Per-model free-tier rate limits for :mod:`.gemini_client`, split out to keep
that module under the repo's file-length cap."""

from __future__ import annotations

#: Free-tier (requests/minute, requests/day) per Gemini model, as (min interval
#: between requests in seconds, daily request cap) — min interval is 60/RPM,
#: rounded up so a sustained run stays at or under the ceiling instead of
#: skimming it. Source: Google AI Studio's per-model rate limits page; update
#: these if Google changes a tier, and add a row here for any other model this
#: pipeline is pointed at.
_MODEL_RATE_LIMITS: dict[str, tuple[float, int]] = {
    "gemini-flash-lite-latest": (4.0, 500),  # 15 RPM / 500 RPD
    "gemini-3.1-flash-lite": (4.0, 500),  # 15 RPM / 500 RPD
    "gemma-3-27b-it": (2.0, 14400),  # 30 RPM / 14,400 RPD
}
#: Used for a model not listed above — gemini-flash-lite-latest's limits, the
#: most conservative of the two, so an unrecognised model degrades to "slow but
#: safe" rather than silently getting no pacing at all.
_DEFAULT_RATE_LIMIT = _MODEL_RATE_LIMITS["gemini-flash-lite-latest"]


def rate_limit_for(model: str) -> tuple[float, int]:
    """(min interval seconds, daily request cap) for *model*, falling back to
    :data:`_DEFAULT_RATE_LIMIT` for a model with no known entry."""
    return _MODEL_RATE_LIMITS.get(model, _DEFAULT_RATE_LIMIT)
