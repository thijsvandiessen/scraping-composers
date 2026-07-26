"""``extract``: LLM-extract concerts/performers from crawled pages into the bucket.

Reads a crawl snapshot (raw pages + their markdown), runs the local Ollama model
over each page, and writes the resulting :class:`WorkMentionDocument` /
:class:`EntityDocument` records back to the bucket under the crawl config's name.
The standard ``process`` step then ingests them like any other snapshot.
"""

import argparse
import itertools
import logging
from pathlib import Path

from composer_bronze.bucket import LocalBucket, latest_loadable_run_id
from composer_bronze.scraper import write_documents
from composer_crawler.records import iter_crawl_records
from composer_extract import OllamaExtractor, extract_documents

from .crawl_cmds import crawl_choices

log = logging.getLogger(__name__)


def cmd_extract(args: argparse.Namespace) -> int:
    config = crawl_choices()[args.config]
    bucket = LocalBucket(args.bucket_path)
    crawl_run_id = args.crawl_run_id or latest_loadable_run_id(bucket, config.name)
    if crawl_run_id is None:
        print(f"no crawl snapshots found for '{config.name}' in {args.bucket_path}")
        return 1

    records = iter_crawl_records(config.name, crawl_run_id, bucket)
    if args.max_pages is not None:
        records = itertools.islice(records, args.max_pages)
    extractor = OllamaExtractor.from_settings(model=args.model)
    docs = extract_documents(records, source_name=config.name, extractor=extractor)

    try:
        run_id = write_documents(bucket, config.name, docs)
    except Exception as exc:
        log.error("extract failed: %s: %s", type(exc).__name__, exc)
        return 1

    ndjson = Path(args.bucket_path) / config.name / run_id / "records.ndjson"
    print(f"extracted {args.config} (from crawl run {crawl_run_id}) → {ndjson}")
    print(f"run_id: {run_id}")
    print(f"next: composer-ingest process {config.name} --run-id {run_id}")
    return 0
