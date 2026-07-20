"""Frontier queue and link discovery for the crawl loop."""

from __future__ import annotations

import re
from collections import deque
from urllib.parse import urldefrag, urljoin

_HREF = re.compile(r"""href\s*=\s*["']([^"'>]+)["']""", re.IGNORECASE)


def extract_links(html: str, base_url: str) -> list[str]:
    """Absolute http(s) URLs found in ``href`` attributes, fragments stripped."""
    links: list[str] = []
    for href in _HREF.findall(html):
        absolute = urldefrag(urljoin(base_url, href.strip())).url
        if absolute.startswith(("http://", "https://")):
            links.append(absolute)
    return links


def normalize_url(url: str) -> str:
    return urldefrag(url.strip()).url


class Frontier:
    """FIFO queue of ``(url, depth)`` pairs; a URL is only ever enqueued once."""

    def __init__(self) -> None:
        self._queue: deque[tuple[str, int]] = deque()
        self._seen: set[str] = set()

    def add(self, url: str, depth: int) -> bool:
        """Enqueue *url* at *depth* unless it was already enqueued; report whether it was added."""
        url = normalize_url(url)
        if url in self._seen:
            return False
        self._seen.add(url)
        self._queue.append((url, depth))
        return True

    def pop(self) -> tuple[str, int] | None:
        return self._queue.popleft() if self._queue else None

    def __bool__(self) -> bool:
        return bool(self._queue)
