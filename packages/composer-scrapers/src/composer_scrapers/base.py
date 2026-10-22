"""The shape every HTTP source takes.

An adapter subclasses :class:`HttpSourceAdapter`, declares its identity and its
manners as class attributes, and implements :meth:`~HttpSourceAdapter.scrape`
against a :class:`~composer_http.SourceSession`. The base class owns everything
else: building the client, the politeness delay, the page mirror and its
freshness, and the end-of-run log line. A source package then always reads the
same way:

* ``fetch.py`` — URLs and the requests themselves, as functions of a session;
* parse modules — pure functions from a fetched body to records;
* ``__init__.py`` — the adapter: ``name``, ``base_url``, ``cadence``, manners,
  and ``scrape`` gluing the two together.

**The mirror follows the cadence.** A page mirrored by a session is served for
the adapter's :attr:`~composer_schema.SourceAdapter.cadence` interval and
refetched after it, so the run that becomes due sees the source as it is now,
while a run interrupted and restarted inside the interval reuses everything it
already paid for. A ``STATIC`` source — an archive, scraped once — keeps its
mirror forever, and a re-run only fetches what it has not got yet.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from collections.abc import Iterator, Mapping
from typing import ClassVar, Generic, TypeVar

from composer_http import DEFAULT_TIMEOUT_S, SourceSession, new_client, open_page_cache
from composer_schema import EntityDocument, SourceAdapter, WorkMentionDocument

__all__ = ["HttpSourceAdapter"]

log = logging.getLogger(__name__)

#: What a source yields: most sources both kinds, a few only entities.
Doc = TypeVar("Doc", bound=EntityDocument | WorkMentionDocument)


class HttpSourceAdapter(SourceAdapter, Generic[Doc]):
    """A :class:`~composer_schema.SourceAdapter` that fetches over HTTP.

    Parametrised by the documents it yields, so a source that yields only
    entities says so: ``class ImslpAdapter(HttpSourceAdapter[EntityDocument])``.
    """

    #: Minimum gap between two network requests (mirror hits cost nothing).
    request_delay_s: ClassVar[float] = 0.5
    #: Per-request timeout; raise it for a source whose payload is a dump.
    timeout_s: ClassVar[float] = DEFAULT_TIMEOUT_S
    #: Extra request headers, merged over the contact User-Agent.
    headers: ClassVar[Mapping[str, str]] = {}
    #: Whether the client follows redirects (httpx does not by default).
    follow_redirects: ClassVar[bool] = False

    def open_session(self) -> SourceSession:
        """A session with this source's manners, mirroring for its cadence."""
        client = new_client(timeout=self.timeout_s, headers=self.headers)
        client.follow_redirects = self.follow_redirects
        return SourceSession(
            self.name,
            client,
            delay_s=self.request_delay_s,
            max_age=self.cadence.interval,
            cache=open_page_cache(),
        )

    def fetch(self, max_pages: int | None = None) -> Iterator[Doc]:
        with self.open_session() as session:
            yield from self.scrape(session, max_pages)
            log.info("%s: %s", self.name, session.summary())

    @abstractmethod
    def scrape(self, session: SourceSession, max_pages: int | None = None) -> Iterator[Doc]:
        """Every document this source holds, fetched through *session*.

        ``max_pages`` caps the requests that dominate the source's cost, for
        test runs; what exactly it counts is the source's to say.
        """
