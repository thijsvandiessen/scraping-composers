# composer-bronze

The raw NDJSON bucket and the fetch workflow that fills it. Bronze holds
everything every source said, verbatim, as complete documents: it is the only
tier with the full fetch output, and the only way data enters the database.

```sh
uv run composer-ingest fetch imslp    # network → ./raw-data/imslp/<run_id>/records.ndjson
uv run composer-ingest process imslp  # disk → DB; every loadable run by default, --run-id to pick one
```

The two phases are deliberately separate commands — source requests are slow,
so scraping never blocks on (or fails with) the database load. After an ETL
change you can re-process a snapshot without hitting the source again.

`bucket.py` stores one NDJSON file per run under `BUCKET_PATH` (default
`./raw-data`); `LocalBucket` can be swapped for an S3 implementation without
touching callers. `scraper.py` runs an adapter into the bucket and reads typed
documents back out.

Each snapshot carries a `manifest.json` recording the fetch's status
(`running`/`completed`/`failed`), record count, and timestamps. A fetch streams
its records as it goes, so a failed or interrupted one keeps everything it
fetched. By default `process` loads every *loadable* run of a source, oldest to
newest: completed and failed ones, but never one still `running` and never a
raw `pages` snapshot left by the retired crawler. Pass `--run-id` to load one
run.

## Tests

```sh
uv run --directory packages/composer-bronze pytest --rootdir . --cov=composer_bronze
```

Everything runs against a `tmp_path` bucket.

| File | Covers |
| --- | --- |
| `test_bucket.py` | `LocalBucket` IO: record round-trips (Unicode, blank and malformed trailing lines), the manifest state machine (and reading one with keys it doesn't define), listing runs, sources and snapshots (size, kind, legacy snapshots without a manifest), which runs count as loadable documents, and rejecting path-traversal source and run ids. |
| `test_scraper.py` | The fetch workflow: snapshot ids, writing records with a completed manifest, `max_pages` and explicit run ids, a failing source leaving a failed manifest, and reading typed documents back from one or all runs. |
