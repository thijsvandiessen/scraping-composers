"""Test helpers: a :class:`~composer_http.SourceSession` over a mocked transport."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta

import httpx

from .pages import PageCache
from .session import SourceSession

__all__ = ["mock_session"]


def mock_session(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cache: PageCache | None = None,
    max_age: timedelta | None = None,
    source: str = "test",
    follow_redirects: bool = False,
) -> SourceSession:
    """A session whose requests are answered by *handler*, with no politeness delay."""
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=follow_redirects)
    return SourceSession(source, client, delay_s=0.0, max_age=max_age, cache=cache)
