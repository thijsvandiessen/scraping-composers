"""``extract``: LLM-extract concerts/performers/facts from crawled pages into the bucket.

Reads a crawl snapshot (raw pages + their markdown), runs the local Ollama model
over each page once per kind the config enables, and writes the resulting
:class:`WorkMentionDocument` / :class:`EntityDocument` records back to the bucket
under the crawl config's name. The standard ``process`` step then ingests them
like any other snapshot.

Model answers are cached by a fingerprint of the request that produced them, so
re-extracting a crawl only pays for the pages whose text actually changed.
``--no-cache`` forces every page back through the model.
"""

import argparse
import itertools
import logging
from collections.abc import Iterator
from pathlib import Path

from composer_bronze.bucket import LocalBucket, latest_loadable_run_id
from composer_bronze.scraper import write_documents
from composer_config import settings
from composer_crawler.records import CrawlRecord, iter_crawl_records
from composer_extract import (
    OllamaExtractor,
    extract_all,
    open_cache,
    options_per_kind,
    summarize,
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
        "extract %s: reading crawl snapshot %s%s as %s",
        config.name,
        crawl_run_id,
        f" (first {args.max_pages} page(s))" if args.max_pages is not None else "",
        ", ".join(config.extract_kinds),
    )

    def records() -> Iterator[CrawlRecord]:
        """A fresh read of the snapshot; each enabled kind gets its own pass."""
        stream = iter_crawl_records(config.name, crawl_run_id, bucket)
        return itertools.islice(stream, args.max_pages) if args.max_pages is not None else stream

    caching = settings.extract_cache_enabled and not args.no_cache
    cache = open_cache(settings.extract_cache_path, enabled=caching)
    extractor = OllamaExtractor.from_settings(model=args.model, cache=cache)
    options = options_per_kind(config.extract_kinds)
    docs = extract_all(
        config.extract_kinds, records, source_name=config.name, extractor=extractor, options=options
    )

    try:
        run_id = write_documents(bucket, config.name, docs)
    except Exception:
        log.exception("extract failed after %s", summarize(options))
        return 1

    ndjson = Path(args.bucket_path) / config.name / run_id / "records.ndjson"
    print(f"extracted {args.config} (from crawl run {crawl_run_id}) → {ndjson}")
    for kind, opts in options.items():
        print(f"  {kind}: {opts.stats.summary()}")
        if unknown := opts.stats.unknown_summary():
            print(f"    predicates outside the vocabulary: {unknown}")
    if cache is not None:
        print(f"  {cache.summary()}")
    print(f"run_id: {run_id}")
    print(f"next: composer-ingest process {config.name} --run-id {run_id}")
    return 0
