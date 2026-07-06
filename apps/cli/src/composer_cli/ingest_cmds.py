import argparse
import logging
from dataclasses import asdict
from pathlib import Path

from composer_ingest.etl.db import get_engine, init_db
from composer_ingest.etl.gold import promote
from composer_ingest.etl.ingestion import ingest_documents
from composer_ingest.scraper.bucket import LOADABLE_STATUSES, LocalBucket
from composer_ingest.scraper.scraper import Scraper, iter_from_bucket
from composer_ingest.scraper.sources import REGISTRY


def cmd_fetch(args: argparse.Namespace) -> int:
    adapter = REGISTRY[args.source]
    bucket = LocalBucket(args.bucket_path)
    try:
        run_id = Scraper(adapter).fetch_to_bucket(bucket, max_pages=args.max_pages)
    except Exception as exc:
        logging.getLogger(__name__).error("fetch failed: %s: %s", type(exc).__name__, exc)
        return 1
    ndjson = Path(args.bucket_path) / args.source / run_id / "records.ndjson"
    print(f"fetched {args.source} → {ndjson}")
    print(f"run_id: {run_id}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        try:
            stats = promote(session, args.gold_path)
        except Exception as exc:
            logging.getLogger(__name__).error("promote failed: %s: %s", type(exc).__name__, exc)
            return 1
    print(f"gold rebuilt at {args.gold_path}")
    for key, value in asdict(stats).items():
        print(f"  {key.replace('_', ' '):<22} {value}")
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    adapter = REGISTRY[args.source]
    bucket = LocalBucket(args.bucket_path)
    run_id = args.run_id
    if run_id is None:
        # Latest loadable snapshot: skip fetches that are still running or crashed.
        loadable = [
            s.manifest.run_id
            for s in bucket.list_snapshots(args.source)
            if s.manifest.status in LOADABLE_STATUSES
        ]
        if not loadable:
            print(f"no complete snapshots found for source '{args.source}' in {args.bucket_path}")
            return 1
        run_id = loadable[-1]
        print(f"using latest run: {run_id}")
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        records = iter_from_bucket(args.source, run_id, bucket)
        run = ingest_documents(session, adapter.name, adapter.base_url, records)
    return 0 if run.status == "completed" else 1
