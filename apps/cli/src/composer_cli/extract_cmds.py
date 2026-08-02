"""``extract``: LLM-extract concerts/performers from crawled pages into the bucket.

Reads a crawl snapshot (raw pages + their markdown), runs the local Ollama model
over each page, and writes the resulting :class:`WorkMentionDocument` /
:class:`EntityDocument` records back to the bucket under the crawl config's name.
The standard ``process`` step then ingests them like any other snapshot.

Model answers are cached by a fingerprint of the request that produced them, so
re-extracting a crawl only pays for the pages whose text actually changed.
``--no-cache`` forces every page back through the model.
"""

import argparse
import itertools
import logging
from pathlib import Path

from composer_bronze.bucket import LocalBucket, latest_loadable_run_id
from composer_bronze.scraper import write_documents
from composer_config import settings
from composer_crawler.records import iter_crawl_records
from composer_extract import (
    ExtractOptions,
    OllamaExtractor,
    extract_documents,
    extract_recording_documents,
    open_cache,
)

from .crawl_cmds import crawl_choices

log = logging.getLogger(__name__)


def cmd_extract(args: argparse.Namespace) -> int:
    config = crawl_choices()[args.config]
    bucket = LocalBucket(args.bucket_path)
    crawl_run_id = args.crawl_run_id or latest_loadable_run_id(bucket, config.name)
    if crawl_run_id is None:
        print(f"no crawl snapshots found for '{config.name}' in {args.bucket_path}")
        return 1

    log.info(
        "extract %s: reading crawl snapshot %s%s",
        config.name,
        crawl_run_id,
        f" (first {args.max_pages} page(s))" if args.max_pages is not None else "",
    )
    records = iter_crawl_records(config.name, crawl_run_id, bucket)
    if args.max_pages is not None:
        records = itertools.islice(records, args.max_pages)
    caching = settings.extract_cache_enabled and not args.no_cache
    cache = open_cache(settings.extract_cache_path, enabled=caching)
    extractor = OllamaExtractor.from_settings(model=args.model, cache=cache)
    extract = extract_recording_documents if config.extract_kind == "recordings" else extract_documents
    options = ExtractOptions()
    docs = extract(records, source_name=config.name, extractor=extractor, options=options)

    try:
        run_id = write_documents(bucket, config.name, docs)
    except Exception:
        log.exception("extract failed after %s", options.stats.summary())
        return 1

    ndjson = Path(args.bucket_path) / config.name / run_id / "records.ndjson"
    print(f"extracted {args.config} (from crawl run {crawl_run_id}) → {ndjson}")
    print(f"  {options.stats.summary()}")
    if cache is not None:
        print(f"  {cache.summary()}")
    print(f"run_id: {run_id}")
    print(f"next: composer-ingest process {config.name} --run-id {run_id}")
    return 0
