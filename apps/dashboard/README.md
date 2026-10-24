# dashboard

A Django web UI on top of the admin and consumer APIs, styled with
[Unfold](https://unfoldadmin.com/) and living behind the Django admin login,
with one page per pipeline phase:

- **Scrapers** — every scraper with its cadence, due/fresh state, and last
  snapshot; a **Fetch** button each and a **Fetch all due** button. Fetching
  only writes raw data to the bucket. A snapshot still marked `running` gets an
  **Abandon** button, for a fetch whose process was killed (see the
  [admin API](../admin-api/README.md)); an older one is flagged too, since the
  page otherwise shows only the last snapshot and it still blocks fetching.
- **Load** — every raw snapshot in the bucket (status, record count, size)
  with a **Load into DB** button on complete ones, plus the recent-loads log.
- **Promote** — gold status (last rebuild, curation stats), the rule-1
  thresholds, and the button to rebuild gold from silver.
- **Data (silver)** (Overview / Entities / Works / Review) — inspect the
  staging data: dataset counts, a searchable entity browser (per-kind pages,
  random sampling, claims with per-source provenance, cross-linked entities),
  a searchable works browser, and the work-mention review queue (resolving
  stays on the CLI: `review --accept` / `--new`).
- **Musicians (gold)** (Composers / Soloists / Conductors / Concerts /
  Recordings) — role-based people pages over gold with per-person concert counts
  (sortable), and concert and recording browsers with their musicians and
  programme.

Scrapers/Load/Promote pages auto-refresh every 5 seconds while work is in
progress.

```sh
export ADMIN_API_KEY=dev-key  # the admin API requires it; the dashboard forwards it
export DASHBOARD_DEBUG=1      # DEBUG defaults off; local dev needs it on (see below)
uv run python apps/dashboard/manage.py migrate            # once: Django's own tables
uv run python apps/dashboard/manage.py createsuperuser    # once: your login
uv run uvicorn composer_api:gold_app --port 8000      # gold API (Musicians pages)
uv run uvicorn composer_admin:admin_app --port 8001   # admin API (scrape/load/promote)
uv run uvicorn composer_api:silver_app --port 8003    # silver API (Data pages)
uv run python apps/dashboard/manage.py runserver 8002             # the dashboard
# open http://localhost:8002 and log in
```

`DEBUG` is off unless `DASHBOARD_DEBUG=1` is set. With `DEBUG` off, `runserver`
does not serve static files, so the Unfold admin CSS breaks — always set it for
local development.

The dashboard never touches a composer database. Pipeline actions go through
the admin API (`ADMIN_API_URL`, default `http://localhost:8001`; `ADMIN_API_KEY`
forwarded as `X-Admin-Key`, so it must be set in the dashboard's environment
too), and data pages go through the consumer APIs (`GOLD_API_URL`, default
`http://localhost:8000`; `SILVER_API_URL`, default `http://localhost:8003`).

## Tests

```sh
uv run --directory apps/dashboard pytest --rootdir . --cov=scrapers --cov=config
```

`test_dashboard.py` tests the API clients against a mock transport and the
views against a stubbed client. No network.

| Area | Covers |
| --- | --- |
| API clients | Admin and data client endpoints and parameters, the admin-key header, and surfacing API errors and connection failures. |
| Access | Every page requiring the admin login, and POST-only endpoints rejecting GET. |
| Scrapers and Load | The due badge, starting a fetch (and a conflict), fetch-due, abandoning a stuck snapshot (an older one flagged, an API error shown), processing a snapshot, auto-refresh while work runs, and the error banner when the API is down. |
| Promote | Gold status, the promote form and its options and validation, and the rule-1 config form. |
| Data (silver) | The overview, entity pages (pagination, kind tabs, random sample, claims and links), works pages, and the review queue. |
| Musicians (gold) | People pages per role with concert counts and sorting, a person's concerts, concert and recording lists and details, gold work details, and entity pages surviving gold being down. |

Not covered: most error-handling branches in the API clients (`scrapers/api.py`)
and the gold views.
