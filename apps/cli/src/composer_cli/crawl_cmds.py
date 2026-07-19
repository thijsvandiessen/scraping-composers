import argparse
import logging
from pathlib import Path

from composer_bronze.bucket import LocalBucket
from composer_crawler import CRAWL_REGISTRY, Crawler


def cmd_crawl(args: argparse.Namespace) -> int:
    config = CRAWL_REGISTRY[args.config]
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
