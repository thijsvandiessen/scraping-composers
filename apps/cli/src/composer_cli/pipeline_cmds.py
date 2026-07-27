"""``run``: crawl, extract and load one crawl config in a single chain.

The three steps are unchanged — this only saves invoking them by hand, so the
whole thing can be handed to cron and left alone. It stops at the first step that
fails, and resolves the extract's own snapshot before loading rather than
defaulting to "the latest", which for a crawl source could be the raw pages the
crawl just wrote.
"""

import argparse

from composer_bronze.bucket import LocalBucket, latest_document_run_id

from .crawl_cmds import cmd_crawl
from .extract_cmds import cmd_extract
from .ingest_cmds import cmd_process


def _crawl_args(args: argparse.Namespace) -> argparse.Namespace:
    return argparse.Namespace(
        config=args.config,
        query=args.query,
        max_pages=args.max_pages,
        bucket_path=args.bucket_path,
    )


def _extract_args(args: argparse.Namespace) -> argparse.Namespace:
    """``--max-pages`` caps the crawl, not the extract: everything just crawled
    is worth extracting, and ``crawl_run_id=None`` picks up that same snapshot."""
    return argparse.Namespace(
        config=args.config,
        crawl_run_id=None,
        model=args.model,
        max_pages=None,
        bucket_path=args.bucket_path,
    )


def _process_args(args: argparse.Namespace, run_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        source=args.config,
        run_id=run_id,
        bucket_path=args.bucket_path,
        database_url=args.database_url,
    )


def _stopped(step: str) -> int:
    print(f"pipeline stopped at {step}")
    return 1


def cmd_run(args: argparse.Namespace) -> int:
    if cmd_crawl(_crawl_args(args)) != 0:
        return _stopped("crawl")
    if cmd_extract(_extract_args(args)) != 0:
        return _stopped("extract")
    run_id = latest_document_run_id(LocalBucket(args.bucket_path), args.config)
    if run_id is None:
        print(f"no extracted snapshot for '{args.config}' to load")
        return _stopped("extract")
    if cmd_process(_process_args(args, run_id)) != 0:
        return _stopped("load")
    print(f"pipeline complete for {args.config}")
    return 0
