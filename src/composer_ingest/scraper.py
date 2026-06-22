"""The one scraper that drives every source.

A source's special cases are *injected*, not subclassed: a :class:`SourceConfig`
(name, base URL, HTTP settings) plus two strategy callables —

- ``pages(client, max_pages)`` yields raw payloads (a page of JSON, an HTML
  string, a parsed dataset, ...), owning pagination and polite delays;
- ``parse(raw)`` turns one payload into :class:`Document` objects via the
  factories in :mod:`composer_ingest.document`.

:meth:`Scraper.fetch_documents` owns the ``httpx.Client`` lifecycle and stamps
every document centrally (source name, ingestion time, content hash), so a new
source is just config + two small functions registered in ``sources.REGISTRY``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Generic, TypeVar

import httpx

from .document import Document, stamp

DEFAULT_USER_AGENT = "composer-ingest/0.1 (research; thijsvandiessen@gmail.com)"

# the raw payload a source's pages yield and its parse consumes
RawT = TypeVar("RawT")

Pages = Callable[[httpx.Client, "int | None"], Iterator[RawT]]
Parse = Callable[[RawT], Iterator[Document]]


@dataclass(frozen=True)
class SourceConfig:
    """Everything the scraper needs to talk to a source that is not behaviour."""

    name: str
    base_url: str
    user_agent: str = DEFAULT_USER_AGENT
    timeout: float = 30.0
    # extra request headers (e.g. Accept-Language) merged over the User-Agent
    headers: dict[str, str] = field(default_factory=dict)


class Scraper(Generic[RawT]):
    """Drives one source: pages -> raw payloads -> stamped documents."""

    def __init__(self, config: SourceConfig, pages: Pages[RawT], parse: Parse[RawT]) -> None:
        self.config = config
        self._pages = pages
        self._parse = parse

    # Upper-case for compatibility with the existing ingest/cli call sites.
    @property
    def NAME(self) -> str:
        return self.config.name

    @property
    def BASE_URL(self) -> str:
        return self.config.base_url

    def fetch_documents(self, max_pages: int | None = None) -> Iterator[Document]:
        headers = {"User-Agent": self.config.user_agent, **self.config.headers}
        with httpx.Client(headers=headers, timeout=self.config.timeout) as client:
            for raw in self._pages(client, max_pages):
                for doc in self._parse(raw):
                    yield stamp(doc, self.config.name)
