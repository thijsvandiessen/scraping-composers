import argparse
import logging
import os
from pathlib import Path

from ..etl.db import get_engine, init_db
from ..etl.ingestion import run_ingest, run_ingest_from_bucket
from ..scraper.bucket import LocalBucket
from ..scraper.raw_fetch import dump_to_bucket, iter_from_bucket
from ..scraper.sources import REGISTRY

DEFAULT_BUCKET_PATH = os.environ.get("BUCKET_PATH", "./raw-data")


def cmd_ingest(args: argparse.Namespace) -> int:
    source_module = REGISTRY[args.source]
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        run = run_ingest(session, source_module, max_pages=args.max_pages)
    return 0 if run.status == "completed" else 1


def cmd_fetch(args: argparse.Namespace) -> int:
    source_module = REGISTRY[args.source]
    bucket = LocalBucket(args.bucket_path)
    try:
        run_id = dump_to_bucket(source_module, bucket, max_pages=args.max_pages)
    except Exception as exc:
        logging.getLogger(__name__).error("fetch failed: %s: %s", type(exc).__name__, exc)
        return 1
    ndjson = Path(args.bucket_path) / args.source / run_id / "records.ndjson"
    print(f"fetched {args.source} → {ndjson}")
    print(f"run_id: {run_id}")
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    source_module = REGISTRY[args.source]
    bucket = LocalBucket(args.bucket_path)
    run_id = args.run_id
    if run_id is None:
        runs = bucket.list_runs(args.source)
        if not runs:
            print(f"no runs found for source '{args.source}' in {args.bucket_path}")
            return 1
        run_id = runs[-1]
        print(f"using latest run: {run_id}")
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        records = iter_from_bucket(args.source, run_id, bucket)
        run = run_ingest_from_bucket(session, source_module.NAME, source_module.BASE_URL, records)
    return 0 if run.status == "completed" else 1
