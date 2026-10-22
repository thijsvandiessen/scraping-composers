# composer-config

The one pydantic-settings `Settings` object every other member reads. Each field
is read from the environment variable of the same name (case-insensitive) or
from `.env`; a real environment variable wins over `.env`, and unknown keys are
ignored.

| Setting | Default | Used for |
| --- | --- | --- |
| `DATABASE_URL` | `sqlite:///composers.db` | silver |
| `SILVER_SCHEMA` | `silver` | Postgres silver's schema, replaced by every `rebuild-silver` |
| `GOLD_DATABASE_URL` | `sqlite:///gold.db` | gold |
| `GOLD_SCHEMA` | `gold` | Postgres gold's schema, replaced by every `promote` |
| `GOLD_MIN_REFERRERS` | `1` | promote's prune rule |
| `NEO4J_URL`, `NEO4J_USER`, `NEO4J_PASSWORD` | `bolt://localhost:7688`, `neo4j`, `composers` | `export-neo4j` |
| `BUCKET_PATH` | `./raw-data` | the bronze bucket |
| `SCRAPER_CONTACT_EMAIL` | unset | required before any fetch |
| `ADMIN_API_KEY` | unset | required by the admin API, which refuses every request without it |
| `LOG_LEVEL` | `INFO` | the CLI and the admin API |
| `PAGE_CACHE_PATH`, `PAGE_CACHE_ENABLED` | `./page-cache.db`, `true` | the scrapers' page mirror |

## Tests

```sh
uv run --directory packages/composer-config pytest --rootdir . --cov=composer_config
```

| File | Covers |
| --- | --- |
| `test_settings.py` | Defaults, environment overrides (case-insensitive, common boolean spellings), `.env` loading, an environment variable beating `.env`, ignored unknown keys, and the module-level `settings` instance. |
