"""``run``: crawl, extract and load one crawl config in a single chain.

The three steps are unchanged — this only saves invoking them by hand, so the
whole thing can be handed to cron and left alone. It stops at the first step that
fails, and resolves the extract's own snapshot before loading rather than
defaulting to "the latest", which for a crawl source could be the raw pages the
crawl just wrote.
"""

import argparse
import logging

from composer_bronze.bucket import LocalBucket, latest_document_run_id

from .crawl_cmds import cmd_crawl
from .extract_cmds import cmd_extract
from .ingest_cmds import cmd_process

log = logging.getLogger(__name__)


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
        no_cache=args.no_cache,
        bucket_path=args.bucket_path,
    )


def _process_args(args: argparse.Namespace, run_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        source=args.config,
        run_id=run_id,
        bucket_path=args.bucket_path,
        database_url=args.database_url,
    )


def _stopped(config: str, step: str) -> int:
    log.warning("pipeline %s: stopped at %s", config, step)
    print(f"pipeline stopped at {step}")
    return 1


def _stage(config: str, label: str) -> None:
    """Announce a stage, so a cron'd chain leaves a readable trail. Mirrors the
    admin API's ``pipeline._stage`` — the same three steps, the same log lines."""
    log.info("pipeline %s: %s", config, label)


def cmd_run(args: argparse.Namespace) -> int:
    config = args.config
    _stage(config, "crawl")
    if cmd_crawl(_crawl_args(args)) != 0:
        return _stopped(config, "crawl")
    _stage(config, "extract")
    if cmd_extract(_extract_args(args)) != 0:
        return _stopped(config, "extract")
    run_id = latest_document_run_id(LocalBucket(args.bucket_path), config)
    if run_id is None:
        print(f"no extracted snapshot for '{config}' to load")
        return _stopped(config, "extract")
    _stage(config, "load")
    if cmd_process(_process_args(args, run_id)) != 0:
        return _stopped(config, "load")
    log.info("pipeline %s: complete", config)
    print(f"pipeline complete for {config}")
    return 0
