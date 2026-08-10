"""Argument parsers for the derived databases: gold, the graph, and silver.

Split out of ``__init__`` so the CLI's top-level module stays inside the
project's 300-line cap; these three are one topic — rebuilding a tier from the
one below it.
"""

from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

from composer_bronze.bucket import DEFAULT_BUCKET_PATH
from composer_gold import (
    DEFAULT_GOLD_DB_PATH,
    DEFAULT_MIN_APPEARANCES,
    DEFAULT_MIN_REFERRERS,
    DEFAULT_MIN_SITELINKS,
)

from .ingest_cmds import cmd_promote, cmd_promote_neo4j, cmd_rebuild_silver

if TYPE_CHECKING:
    _SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]  # pyright: ignore[reportPrivateUsage]


def add_build_parsers(sub: _SubParsers) -> None:
    _add_promote(sub)
    _add_promote_neo4j(sub)
    _add_rebuild_silver(sub)


def _add_promote(sub: _SubParsers) -> None:
    p = sub.add_parser("promote", help="rebuild the curated gold database from the silver (staging) database")
    p.add_argument("--gold-path", default=DEFAULT_GOLD_DB_PATH, help="path of the gold SQLite file")
    p.add_argument(
        "--min-sitelinks",
        type=int,
        default=DEFAULT_MIN_SITELINKS,
        help="also promote persons whose Wikipedia sitelink count is at least N, "
        "even without concert/work evidence (default: $GOLD_MIN_SITELINKS or off)",
    )
    p.add_argument(
        "--min-appearances",
        type=int,
        default=DEFAULT_MIN_APPEARANCES,
        help="rule 1: keep people and ensembles credited on at least N concerts/recordings "
        "(default: $GOLD_MIN_APPEARANCES or 1)",
    )
    p.add_argument(
        "--drop-unevidenced-persons",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="rule 1: drop people and ensembles without concert/recording/work evidence",
    )
    p.add_argument(
        "--collapse-duplicates",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="rule 2: collapse duplicate persons into their canonical row",
    )
    p.add_argument(
        "--prune-unreferenced",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="rule 3: prune entities left unreferenced by the other rules",
    )
    p.add_argument(
        "--min-referrers",
        type=int,
        default=DEFAULT_MIN_REFERRERS,
        help="rule 3: keep entities referenced by at least N distinct persons "
        "(default: $GOLD_MIN_REFERRERS or 1)",
    )
    p.set_defaults(func=cmd_promote)


def _add_promote_neo4j(sub: _SubParsers) -> None:
    p = sub.add_parser(
        "promote-neo4j",
        help="export the curated gold database into Neo4j (needs NEO4J_URI and a password)",
    )
    p.add_argument("--gold-path", default=DEFAULT_GOLD_DB_PATH, help="path of the gold SQLite file")
    p.add_argument("--neo4j-uri", default=None, help="override $NEO4J_URI for this run")
    p.add_argument(
        "--include-unperformed-works",
        action="store_true",
        help="also export works no source has on a programme (~120k extra nodes, "
        "which takes an Aura Free instance to ~95%% of its node limit)",
    )
    p.add_argument(
        "--wipe",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="empty the target database first (the export is a full rebuild)",
    )
    p.set_defaults(func=cmd_promote_neo4j)


def _add_rebuild_silver(sub: _SubParsers) -> None:
    p = sub.add_parser(
        "rebuild-silver",
        help="rebuild the silver database from the bucket with the current heuristics "
        "(human review decisions are preserved)",
    )
    p.add_argument("--bucket-path", default=DEFAULT_BUCKET_PATH, help="root directory of the local bucket")
    p.set_defaults(func=cmd_rebuild_silver)
