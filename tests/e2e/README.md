# composer-e2e

End-to-end tests of the whole pipeline, fetch → bucket → process → silver →
dedupe → promote → gold → consumer API, over synthetic sources.

The unit suites seed silver by hand and promote it in-process. This suite runs
what an operator runs: `harness.py` drives the real CLI in a throwaway
directory, one subprocess per step, so each step starts as cold as it does in
production. `python -m composer_e2e <args>` is `composer-ingest <args>` with the
synthetic adapters from `sources.py` registered in place of the real ones. They
keep the real names (`wikidata`, `berlinphil`, `decca`, …) and document shapes,
because the silver passes key on both. Only the network is gone.

Every name in the synthetic dataset is there to exercise one rule;
`sources.py` explains which, and why it should or shouldn't reach gold.

## Tests

```sh
uv run --directory tests/e2e pytest --rootdir . --cov=composer_e2e
```

| File | Covers |
| --- | --- |
| `test_pipeline.py` | One completed snapshot per source; silver keeping every listed name; dedupe linking the QID twins from source evidence; derived concerts and recordings; one work per piece across mentions; gold keeping only credited people, once each, with the duplicate's claims carried over; concerts, recordings and works copied to gold; the API listing composers with three works, citing every source, and resolving concert participants (a dropped conductor stays on the programme unlinked); a second ingestion flowing through to gold; concert and recording ids (and the API links built on them) surviving a second ingestion whose new concert and release sort before the old ones; and `rebuild-silver` reproducing the same gold. |

The coverage figure measures only the harness and synthetic sources, not the
product code the subprocesses run.
