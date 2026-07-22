import argparse
import dataclasses
import logging
import sys
from pathlib import Path

from composer_bronze.bucket import LocalBucket
from composer_crawler import CRAWL_REGISTRY, CrawlConfig, Crawler, all_crawl_configs


def crawl_choices() -> dict[str, CrawlConfig]:
    """Code-registered plus stored crawl configs.

    A corrupt configs file must not brick unrelated CLI commands, so it
    degrades to the code registry with a warning.
    """
    try:
        return all_crawl_configs()
    except ValueError as exc:
        print(f"warning: ignoring stored crawl configs: {exc}", file=sys.stderr)
        return dict(CRAWL_REGISTRY)


def cmd_crawl(args: argparse.Namespace) -> int:
    config = crawl_choices()[args.config]
    if args.query:
        config = dataclasses.replace(config, relevance_query=args.query)
    bucket = LocalBucket(args.bucket_path)
    try:
        run_id = Crawler(config).crawl_to_bucket(bucket, max_pages=args.max_pages)
    except Exception as exc:
        logging.getLogger(__name__).error("crawl failed: %s: %s", type(exc).__name__, exc)
        return 1
    ndjson = Path(args.bucket_path) / config.name / run_id / "records.ndjson"
    print(f"crawled {args.config} → {ndjson}")
    print(f"run_id: {run_id}")
    return 0
