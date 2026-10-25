# composer-http

The HTTP layer every scraper fetches through:

- `__init__.py` — the polite User-Agent (a reachable contact email from
  `SCRAPER_CONTACT_EMAIL` is required before anything is fetched), client
  construction, and retry with backoff that honours `Retry-After`.
- `deadline.py` — `DeadlineTransport`, which `new_client` installs: the client's
  `timeout` is also a total wall-clock deadline per request. httpx's own
  timeouts bound each socket operation, so a server trickling a byte at a time
  could hold a request for as long as it liked (#365); now every connect, write
  and read waits only for what is left of the deadline, and expiry raises an
  `httpx.TimeoutException` that is retried like any other transport error.
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
| `test_deadline.py` | The total deadline, against a real local server trickling one byte every 0.3s under a 1s deadline: a trickled body, trickled headers and a `client.stream` body all fail at the deadline; each request gets a fresh one; `get_text` retries the expiry and a `SourceSession` carries on after it; httpx's per-read timeout still fires on its own. |
| `test_session.py` | `SourceSession`: request spacing, mirror hits costing no pause, the validator keeping error pages out, max age, POST fingerprints, `try_get_text`, JSON mirroring, the run summary, and closing the client. |
| `test_pages.py` | The page mirror: hits and misses, replacement, persistence, a corrupt body or unusable file costing a refetch rather than the run, max age, request fingerprints, and upgrading a pre-fingerprint mirror in place. |
