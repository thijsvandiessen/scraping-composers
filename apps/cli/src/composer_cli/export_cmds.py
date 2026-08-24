"""Exports out of the gold database, for tools that live outside the pipeline."""

import argparse
import logging
from dataclasses import asdict
from pathlib import Path

from composer_gold import KumuConfig, export_kumu
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

log = logging.getLogger(__name__)


def cmd_export_kumu(args: argparse.Namespace) -> int:
    gold_path = Path(args.gold_path)
    if not gold_path.exists():
        # Deliberately not create_all()-ing: a typo in --gold-path would
        # otherwise leave an empty database behind and export nothing.
        print(f"no gold database at {gold_path} — run `composer-ingest promote` first")
        return 1
    session_factory = sessionmaker(create_engine(f"sqlite:///{gold_path}"))
    with session_factory() as session:
        try:
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
    print(f"kumu blueprint written to {args.output}")
    for key, value in asdict(stats).items():
        print(f"  {key.replace('_', ' '):<20} {value}")
    return 0
