"""Exports out of the gold database, for tools that live outside the pipeline."""

import argparse
import logging
from dataclasses import asdict

from composer_gold import KumuConfig, export_kumu, gold_engine, gold_target
from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)


def cmd_export_kumu(args: argparse.Namespace) -> int:
    target = gold_target(args.gold_url)
    if not target.exists():
        # Asked of the target, never by connecting: opening a sqlite URL
        # creates the file, so a typo in --gold-url would leave an empty
        # database behind and export nothing.
        print(f"no gold database at {target.describe()} — run `composer-ingest promote` first")
        return 1
    engine = gold_engine(args.gold_url)
    try:
        with sessionmaker(engine)() as session:
            config = KumuConfig(
                performer_limit=args.limit,
                min_weight=args.min_weight,
                performances=args.performances,
                claims=args.claims,
            )
            stats = export_kumu(session, args.output, config)
    except Exception:
        log.exception("kumu export failed")
        return 1
    finally:
        engine.dispose()
    print(f"kumu blueprint written to {args.output}")
    for key, value in asdict(stats).items():
        print(f"  {key.replace('_', ' '):<20} {value}")
    return 0
