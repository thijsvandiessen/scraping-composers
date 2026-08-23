import argparse
import logging
from dataclasses import asdict
from pathlib import Path

from composer_bronze.bucket import LocalBucket, all_document_run_ids
from composer_bronze.scraper import Scraper, iter_all_from_bucket, iter_from_bucket
from composer_gold import PromoteConfig, Rule1Config, promote
from composer_models.db import get_engine, init_db
from composer_scrapers import REGISTRY
from composer_warehouse.concerts import derive_concerts
from composer_warehouse.ingestion import ingest_documents
from composer_warehouse.rebuild import rebuild_silver
from composer_warehouse.recordings import derive_recordings

from .crawl_cmds import crawl_choices

log = logging.getLogger(__name__)


def cmd_fetch(args: argparse.Namespace) -> int:
    adapter = REGISTRY[args.source]
    bucket = LocalBucket(args.bucket_path)
    try:
        run_id = Scraper(adapter).fetch_to_bucket(bucket, max_pages=args.max_pages)
    except Exception:
        log.exception("fetch failed")
        return 1
    ndjson = Path(args.bucket_path) / args.source / run_id / "records.ndjson"
    print(f"fetched {args.source} → {ndjson}")
    print(f"run_id: {run_id}")
    return 0


def cmd_derive_concerts(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        stats = derive_concerts(session)
    print(f"derived {stats.concerts} concerts")
    print(f"  participant links      {stats.participant_links}")
    print(f"  unresolved names       {stats.unresolved_participant_names}")
    return 0


def cmd_derive_recordings(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        stats = derive_recordings(session)
    print(f"derived {stats.recordings} recordings")
    print(f"  merged duplicates      {stats.merged_duplicates}")
    print(f"  participant links      {stats.participant_links}")
    print(f"  unresolved names       {stats.unresolved_participant_names}")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        try:
            # Concerts and recordings are silver-derived state the gold build
            # copies; refresh them first so promote-after-load never publishes
            # stale derivations.
            derive_concerts(session)
            derive_recordings(session)
            config = PromoteConfig(
                rule1=Rule1Config.from_json(args.rule1_config),
                min_referrers=args.min_referrers,
                drop_unevidenced_persons=args.drop_unevidenced_persons,
                collapse_duplicates=args.collapse_duplicates,
                prune_unreferenced=args.prune_unreferenced,
            )
            stats = promote(session, args.gold_path, config)
        except Exception:
            log.exception("promote failed")
            return 1
    print(f"gold rebuilt at {args.gold_path}")
    for key, value in asdict(stats).items():
        print(f"  {key.replace('_', ' '):<22} {value}")
    return 0


def cmd_rebuild_silver(args: argparse.Namespace) -> int:
    bucket = LocalBucket(args.bucket_path)
    sources = [(adapter.name, adapter.base_url) for adapter in REGISTRY.values()]
    try:
        stats = rebuild_silver(bucket, sources, args.database_url)
    except ValueError as exc:
        print(exc)
        return 1
    except Exception:
        log.exception("rebuild-silver failed")
        return 1
    print("silver rebuilt from the bucket")
    for key, value in asdict(stats).items():
        print(f"  {key.replace('_', ' '):<26} {value}")
    return 0


def _source_identity(source: str) -> tuple[str, str]:
    """(name, base_url) for a bucket source: a registered scraper, or a crawl
    config (whose LLM-extracted docs the ``extract`` step wrote under its name)."""
    adapter = REGISTRY.get(source)
    if adapter is not None:
        return adapter.name, adapter.base_url
    config = crawl_choices().get(source)
    base_url = config.seeds[0] if config and config.seeds else ""
    return source, base_url


def cmd_process(args: argparse.Namespace) -> int:
    source_name, base_url = _source_identity(args.source)
    bucket = LocalBucket(args.bucket_path)
    if args.run_id is not None:
        records = iter_from_bucket(source_name, args.run_id, bucket)
    else:
        run_ids = all_document_run_ids(bucket, source_name)
        if not run_ids:
            print(f"no loadable snapshots found for source '{source_name}' in {args.bucket_path}")
            return 1
        print(f"using {len(run_ids)} run(s): {', '.join(run_ids)}")
        records = iter_all_from_bucket(source_name, run_ids, bucket)
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        run = ingest_documents(session, source_name, base_url, records)
    print(f"seen {run.records_seen}, new {run.records_new}")
    return 0 if run.status == "completed" else 1
