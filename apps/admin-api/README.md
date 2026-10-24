# admin-api

A FastAPI app for running the pipeline from a browser instead of the CLI. It is
**separate from the read-only consumer API** so it can be deployed in its own,
locked-down environment.

```sh
export ADMIN_API_KEY=dev-key
uv run uvicorn composer_admin:admin_app --port 8001
# open http://localhost:8001/docs and click "Try it out"
```

`ADMIN_API_KEY` is **required**: every request must carry a matching
`X-Admin-Key` header, and while the variable is unset the API fails closed and
refuses all requests with a 503 — so set it for local use too (any value). The
fetch endpoints run the scrapers, so the process also needs
`SCRAPER_CONTACT_EMAIL`.

Each scraper declares a refresh cadence (see
[`composer-scrapers`](../../packages/composer-scrapers/README.md#sources)), and the
API surfaces which scrapers are _due_ so you can refresh by staleness. Fetch and
load are separate endpoints, mirroring the CLI's `fetch` and `process`:

- `GET  /admin/v1/scrapers` (+ `/{name}`) — every scraper with its cadence, last snapshot, `due` flag, and any snapshots still `running`
- `POST /admin/v1/scrapers/{name}/fetch` — fetch one source to the bucket (background, returns the `snapshot_id`)
- `POST /admin/v1/scrapers/fetch-due` — fetch every scraper whose raw data is stale
- `GET  /admin/v1/snapshots` — every raw snapshot in the bucket with its status, record count, and size
- `POST /admin/v1/snapshots/{source}/{snapshot_id}/process` — load a snapshot into the database (background, returns a `run_id`)
- `POST /admin/v1/snapshots/{source}/{snapshot_id}/abandon` — give up on a snapshot stuck on `running`
- `GET  /admin/v1/runs` / `GET /admin/v1/runs/{run_id}` — load history and status
- `GET  /admin/v1/gold` / `POST /admin/v1/promote` — gold status, and rebuilding gold from silver
- `GET` / `PUT /admin/v1/rule1-config` — promote's rule-1 thresholds
- `GET  /admin/v1/silver` / `POST /admin/v1/rebuild-silver` — silver status, and replaying the bucket

A fetch that is killed outright never finalizes its manifest, so it stays
`running` for good: the dashboard keeps showing it as live and no new run for
that source can start. **Abandon** is the way out — it marks the snapshot
failed and corrects its record count to what is on disk, deleting nothing.

Fetch status lives in the snapshot's manifest on disk; loads are recorded in
`ingest_runs` (the same log the CLI `runs` command shows).

## Tests

```sh
uv run --directory apps/admin-api pytest --rootdir . --cov=composer_admin
```

The tests use an in-memory database and a `tmp_path` bucket. Starlette's
`TestClient` runs background tasks synchronously, so a started fetch or load has
finished by the time the POST returns. The Postgres cases are skipped unless
`COMPOSER_TEST_POSTGRES_URL` is set.

| File | Covers |
| --- | --- |
| `test_admin.py` | Scrapers with cadence, `due` and their `running` snapshots (an older stuck one included); fetch writing a snapshot and manifest, conflicts, 404s, and a failed fetch; `fetch-due`; snapshot listing; process, abandon and their conflicts; runs; promote with rule toggles, URLs and `rule1-config` (including validation); `rebuild-silver` and silver status on SQLite and Postgres; promote into Postgres gold; the admin-key guard failing closed. |
| `test_snapshot_kinds.py` | Raw `pages` snapshots left by the retired crawler stay listed and refuse to load, while `documents` snapshots load. |
| `test_logconfig.py` | Request-supplied values cannot forge log lines. |
