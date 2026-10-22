# composer-http

The HTTP layer every scraper fetches through:

- `__init__.py` — the polite User-Agent (a reachable contact email from
  `SCRAPER_CONTACT_EMAIL` is required before anything is fetched), client
  construction, and retry with backoff that honours `Retry-After`.
- `session.py` — `SourceSession`: one client per adapter run, the delay between
  network requests, the mirror rules, and tolerant `try_get_*` helpers.
- `pages.py` — the page mirror: a gzipped SQLite store at `PAGE_CACHE_PATH`
  that serves a page again instead of refetching it, bounded by the adapter's
  cadence and keyed on a fingerprint of the request for POSTed queries.
- `testing.py` — `mock_session(handler)`, which scrapers test against instead
  of the network.

When to mirror and when not to is described in
[`composer-scrapers`](../composer-scrapers/README.md#adding-a-source).

## Tests

```sh
uv run --directory packages/composer-http pytest --rootdir . --cov=composer_http
```

| File | Covers |
| --- | --- |
| `test_http.py` | Retry and backoff (including `Retry-After`), the contact email requirement, the User-Agent and client options, and `get_text`/`get_json` retrying error statuses and undecodable bodies. |
| `test_session.py` | `SourceSession`: request spacing, mirror hits costing no pause, the validator keeping error pages out, max age, POST fingerprints, `try_get_text`, JSON mirroring, the run summary, and closing the client. |
| `test_pages.py` | The page mirror: hits and misses, replacement, persistence, a corrupt body or unusable file costing a refetch rather than the run, max age, request fingerprints, and upgrading a pre-fingerprint mirror in place. |
