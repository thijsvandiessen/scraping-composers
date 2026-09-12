"""HTTP access to the catalogue behind deccaclassics.com.

The site is a Next.js front end over ``https://graphql.universal-music.de``, a
public unauthenticated GraphQL API that answers arbitrary queries. Reading it
directly rather than the rendered pages is not an optimisation, it is a
correctness requirement: a product page server-renders only the *first* disc of
a set (``carrierIds`` lists every carrier, ``carriers`` returns one, the rest
arrive by client-side query), so parsing the HTML silently loses discs 2+ of
every box set in the catalogue.

Two details of the query are load-bearing:

``channel: 0``
    Without it ``contributors``, ``artists`` and ``headings`` all come back as
    empty lists — the response still looks well-formed, it just has no credits
    in it, which is the entire point of the fetch.

``language: "en"``
    The catalogue is indexed in German (see :mod:`.urls`) but titles are
    localised, and the rest of this project reads English.

Requests are batched with GraphQL aliases, mirrored per family through
:class:`~composer_http.PageCache`, and rate-limited between uncached calls. A
full sweep is ~715 requests over roughly an hour, so a run that dies partway
must not have to start over: the mirror is keyed per family, not per batch, so
a resumed run only asks for what it is missing.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator, Sequence
from typing import Any

import httpx
from composer_http import PageCache, call_with_retries, get_text, new_client

from .urls import SITEMAP_URL, locations

log = logging.getLogger(__name__)

GRAPHQL_URL = "https://graphql.universal-music.de/"

#: Families per request. Measured against the live API with the field set below:
#: 5 costs 3-11s and 165-655KB, 25 takes 8.5s, and 50 answers HTTP 500. The
#: ceiling is response size rather than count, so this stays low enough that a
#: box set landing in the same batch as four other box sets still fits.
BATCH_SIZE = 5

#: Between uncached requests. The site's robots.txt is ``Allow: /`` with no
#: crawl-delay and the API host publishes none; this is the same politeness rate
#: the other archives here use.
REQUEST_DELAY_S = 0.5

#: Per product family. ``works`` is a connection ranked by ``level`` (1 is the
#: work, 2-5 its acts and movements); ``contributors.function`` is the credit
#: that makes this source worth reading deterministically; ``headings`` is kept
#: as the fallback for the ~13% of tracks carrying no Composer-function credit.
_FAMILY_FIELDS = """
  idRaw headline alternativeHeadline releaseDate
  products { edges { node {
    idRaw sku label configuration family
    productCategory { name }
    playlist { carriers { name tracks {
      titleNumber title isrc duration
      recordingLocation recordingDateYear recordingDateMonth recordingDateDay
      works { edges { node { idRaw name level } } }
      contributors { function artist { idRaw screenname isComposer urlAlias } }
      headings { name type }
    } } }
  } } }
"""

#: Per roster artist. ``name`` is the catalogue's "Lastname, Firstname" form,
#: ``screenname`` the display one.
_ARTIST_FIELDS = """
  idRaw screenname name urlAlias isComposer themeType seoDescription
"""


#: A batch of box sets is a multi-megabyte response the API builds on demand,
#: and the shared 30s default expires on the largest of them — which then burn
#: three retries and two halvings before being dropped. Raised at the call site,
#: as ``composer_http`` intends, rather than making every source wait this long.
TIMEOUT_S = 120.0


def make_client() -> httpx.Client:
    """A client for both hosts: the sitemap on the site, everything else on the API."""
    return new_client(timeout=TIMEOUT_S)


def fetch_sitemap(client: httpx.Client) -> str:
    """The sitemap urlset listing the catalogue.

    ``/robots.txt`` points at a sitemap *index* holding exactly one child, so
    this follows the indirection. An index that grows a second child is
    concatenated rather than silently truncated to the first.
    """
    index = get_text(client, SITEMAP_URL, label="sitemap index")
    children = [url for url in locations(index) if url != SITEMAP_URL]
    if not children:
        return index
    log.info("decca: sitemap index lists %d urlset(s)", len(children))
    return "".join(get_text(client, url, label=f"sitemap {n}") for n, url in enumerate(children))


def _post(client: httpx.Client, query: str, *, label: str) -> dict[str, Any]:
    """One GraphQL POST, retried, with the API's errors surfaced as failures.

    ``composer_http`` only wraps GET, but ``call_with_retries`` is generic over
    the callable, so the backoff and Retry-After handling come for free. A
    GraphQL error arrives as HTTP 200 with an ``errors`` key; raising on it is
    what lets the caller's batch-halving see it.
    """

    def do_request() -> dict[str, Any]:
        response = client.post(GRAPHQL_URL, json={"query": query})
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        errors = payload.get("errors")
        if errors:
            raise httpx.HTTPError(f"{label}: {json.dumps(errors)[:300]}")
        return payload

    return call_with_retries(do_request, label=label)


def _query(selections: Sequence[str]) -> str:
    return f'{{ universalMusic(channel: 0, language: "en") {{ {" ".join(selections)} }} }}'


def _nodes(payload: dict[str, Any]) -> dict[str, Any]:
    """The aliased selections of a response, dropping the ones that resolved null.

    A null is a family or artist the API does not know — not an error, and it
    must not fail the batch around it.
    """
    data = payload.get("data") or {}
    root = data.get("universalMusic") or {}
    return {alias: node for alias, node in root.items() if node}


def _fetch_batch(client: httpx.Client, selections: dict[str, str], *, label: str) -> dict[str, Any]:
    """One aliased batch, as ``{alias: node}``.

    Failure halves the batch and retries each half, so one unservable family
    costs its neighbours a repeat request rather than losing the whole sweep.
    Only a singleton that still fails is given up on, and it says which.
    """
    if not selections:
        return {}
    try:
        return _nodes(_post(client, _query(list(selections.values())), label=label))
    except httpx.HTTPError as exc:
        if len(selections) == 1:
            log.warning("decca: skipping %s: %s", label, exc)
            return {}
        aliases = list(selections)
        middle = len(aliases) // 2
        log.info("decca: %s failed over %d items, halving: %s", label, len(aliases), exc)
        merged: dict[str, Any] = {}
        for half in (aliases[:middle], aliases[middle:]):
            merged.update(_fetch_batch(client, {a: selections[a] for a in half}, label=label))
        return merged


def fetch_families(
    client: httpx.Client, ids: Sequence[int], cache: PageCache | None = None
) -> Iterator[dict[str, Any]]:
    """Every product family in *ids*, mirrored, in the order given.

    Yields as each batch lands rather than accumulating: the caller writes
    documents per family, so a sweep cut short still leaves a usable snapshot.
    """
    pending: list[int] = []
    for family_id in ids:
        mirrored = _mirrored(cache, _family_key(family_id))
        if mirrored is not None:
            yield mirrored
            continue
        pending.append(family_id)
        if len(pending) >= BATCH_SIZE:
            yield from _fetch_family_batch(client, pending, cache)
            pending = []
    yield from _fetch_family_batch(client, pending, cache)


def _fetch_family_batch(
    client: httpx.Client, ids: list[int], cache: PageCache | None
) -> Iterator[dict[str, Any]]:
    if not ids:
        return
    selections = {f"f{i}": f"f{i}: productFamily(id: {i}) {{ {_FAMILY_FIELDS} }}" for i in ids}
    nodes = _fetch_batch(client, selections, label=f"families {ids[0]}..{ids[-1]}")
    time.sleep(REQUEST_DELAY_S)
    for family_id in ids:
        node = nodes.get(f"f{family_id}")
        if node is None:
            continue
        _mirror(cache, _family_key(family_id), node)
        yield node


def fetch_artists(
    client: httpx.Client, slugs: Sequence[str], cache: PageCache | None = None
) -> Iterator[dict[str, Any]]:
    """Every roster artist in *slugs*, mirrored, in the order given."""
    pending: list[str] = []
    for slug in slugs:
        mirrored = _mirrored(cache, _artist_key(slug))
        if mirrored is not None:
            yield mirrored
            continue
        pending.append(slug)
        if len(pending) >= BATCH_SIZE:
            yield from _fetch_artist_batch(client, pending, cache)
            pending = []
    yield from _fetch_artist_batch(client, pending, cache)


def _fetch_artist_batch(
    client: httpx.Client, slugs: list[str], cache: PageCache | None
) -> Iterator[dict[str, Any]]:
    """A batch keyed by position, not slug: a slug is not a GraphQL alias.

    Aliases must match ``[_A-Za-z][_0-9A-Za-z]*`` and roster slugs hold hyphens
    and leading digits, so the alias is the index and the slug rides in the
    argument, JSON-encoded rather than interpolated raw.
    """
    if not slugs:
        return
    selections = {
        f"a{n}": f"a{n}: artist(urlAlias: {json.dumps(slug)}) {{ {_ARTIST_FIELDS} }}"
        for n, slug in enumerate(slugs)
    }
    nodes = _fetch_batch(client, selections, label=f"artists {slugs[0]}..{slugs[-1]}")
    time.sleep(REQUEST_DELAY_S)
    for n, slug in enumerate(slugs):
        node = nodes.get(f"a{n}")
        if node is None:
            continue
        _mirror(cache, _artist_key(slug), node)
        yield node


def _family_key(family_id: int) -> str:
    """The mirror key for a family.

    A synthetic URL, because :class:`~composer_http.PageCache` is keyed by one
    and these are POSTs. Keyed per family rather than per batch so that
    re-running after a partial sweep, or with a different :data:`BATCH_SIZE`,
    still hits everything already fetched.
    """
    return f"graphql://decca/family/{family_id}"


def _artist_key(slug: str) -> str:
    return f"graphql://decca/artist/{slug}"


def _mirrored(cache: PageCache | None, key: str) -> dict[str, Any] | None:
    if cache is None:
        return None
    stored = cache.get(key)
    if stored is None:
        return None
    try:
        node: dict[str, Any] = json.loads(stored)
    except ValueError:
        return None  # A truncated mirror entry is a miss, not a crash.
    return node


def _mirror(cache: PageCache | None, key: str, node: dict[str, Any]) -> None:
    if cache is not None:
        cache.put(key, json.dumps(node))
