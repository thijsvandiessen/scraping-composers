from __future__ import annotations

import argparse
import logging
import sys
from typing import TYPE_CHECKING

from composer_bronze.bucket import DEFAULT_BUCKET_PATH
from composer_gold import DEFAULT_GOLD_DB_PATH, DEFAULT_MIN_SITELINKS
from composer_scrapers import REGISTRY

from .crawl_cmds import cmd_crawl, crawl_choices
from .extract_cmds import cmd_extract
from .ingest_cmds import (
    cmd_derive_concerts,
    cmd_derive_recordings,
    cmd_fetch,
    cmd_process,
    cmd_promote,
    cmd_rebuild_silver,
)
from .person_cmds import cmd_dedupe_persons, cmd_person_review
from .query_cmds import cmd_claims, cmd_runs, cmd_stats
from .work_cmds import cmd_rematch, cmd_review, cmd_works

if TYPE_CHECKING:
    # What ArgumentParser.add_subparsers returns; argparse doesn't expose a
    # public name for it (and the runtime class is not subscriptable).
    _SubParsers = argparse._SubParsersAction[argparse.ArgumentParser]  # pyright: ignore[reportPrivateUsage]


def _add_query_parsers(sub: _SubParsers) -> None:
    p_stats = sub.add_parser("stats", help="show dataset counts")
    p_stats.set_defaults(func=cmd_stats)

    p_claims = sub.add_parser(
        "claims", help="show an entity's claims and which source asserts each (to compare and choose)"
    )
    p_claims.add_argument("name", help="entity name (exact dedup-key match or label substring)")
    p_claims.add_argument("--kind", help="restrict to an entity kind (person, work, place, ...)")
    p_claims.add_argument("--predicate", help="restrict to one predicate (e.g. born_on)")
    p_claims.add_argument("--source", help="restrict to one source (e.g. wikidata)")
    p_claims.add_argument("--limit", type=int, default=10, help="max matching entities to show")
    p_claims.set_defaults(func=cmd_claims)

    p_runs = sub.add_parser("runs", help="show the ingest run log")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.set_defaults(func=cmd_runs)


def _add_work_parsers(sub: _SubParsers) -> None:
    p_works = sub.add_parser("works", help="search resolved works (by composer or title) and their aliases")
    p_works.add_argument("name", help="composer name or title substring")
    p_works.add_argument("--limit", type=int, default=20, help="max matching works to show")
    p_works.set_defaults(func=cmd_works)

    p_review = sub.add_parser("review", help="list (or resolve) work mentions the matcher flagged for review")
    p_review.add_argument("--limit", type=int, default=20, help="max mentions to list")
    p_review.add_argument(
        "--accept",
        nargs=2,
        metavar=("MENTION_ID", "WORK_ID"),
        help="match a mention to an existing work",
    )
    p_review.add_argument("--new", type=int, metavar="MENTION_ID", help="create a new work from a mention")
    p_review.set_defaults(func=cmd_review)

    p_rematch = sub.add_parser(
        "rematch", help="re-run matching over unmatched/needs-review mentions (after tuning)"
    )
    p_rematch.set_defaults(func=cmd_rematch)


def _add_pipeline_parsers(sub: _SubParsers) -> None:
    p_fetch = sub.add_parser("fetch", help="fetch raw records from a source and store in the bucket")
    p_fetch.add_argument("source", choices=sorted(REGISTRY))
    p_fetch.add_argument("--max-pages", type=int, help="stop after N pages (for testing)")
    p_fetch.add_argument(
        "--bucket-path", default=DEFAULT_BUCKET_PATH, help="root directory of the local bucket"
    )
    p_fetch.set_defaults(func=cmd_fetch)

    p_crawl = sub.add_parser(
        "crawl",
        help="crawl raw pages/endpoints into the bucket, no parsing (configs come from "
        "composer_crawler.CRAWL_REGISTRY and the dashboard-managed crawl configs file)",
    )
    p_crawl.add_argument("config", choices=sorted(crawl_choices()))
    p_crawl.add_argument("--max-pages", type=int, help="cap on URLs scraped (overrides the config)")
    p_crawl.add_argument(
        "--query",
        help="rank discovered URLs by relevance to this topic (overrides the config's relevance_query)",
    )
    p_crawl.add_argument(
        "--bucket-path", default=DEFAULT_BUCKET_PATH, help="root directory of the local bucket"
    )
    p_crawl.set_defaults(func=cmd_crawl)

    p_extract = sub.add_parser(
        "extract",
        help="LLM-extract concerts/performers from a crawl snapshot into the bucket "
        "(local Ollama model; runs between crawl and process)",
    )
    p_extract.add_argument("config", choices=sorted(crawl_choices()))
    p_extract.add_argument("--crawl-run-id", help="crawl run to read (default: latest completed)")
    p_extract.add_argument("--model", help="Ollama model to use (overrides $OLLAMA_MODEL / the default)")
    p_extract.add_argument("--max-pages", type=int, help="stop after N crawled pages (for testing)")
    p_extract.add_argument(
        "--bucket-path", default=DEFAULT_BUCKET_PATH, help="root directory of the local bucket"
    )
    p_extract.set_defaults(func=cmd_extract)

    p_process = sub.add_parser(
        "process", help="ingest previously fetched records from the bucket into the DB"
    )
    p_process.add_argument("source", choices=sorted(set(REGISTRY) | set(crawl_choices())))
    p_process.add_argument("--run-id", help="bucket run_id to process (default: latest)")
    p_process.add_argument(
        "--bucket-path", default=DEFAULT_BUCKET_PATH, help="root directory of the local bucket"
    )
    p_process.set_defaults(func=cmd_process)

    p_promote = sub.add_parser(
        "promote", help="rebuild the curated gold database from the silver (staging) database"
    )
    p_promote.add_argument("--gold-path", default=DEFAULT_GOLD_DB_PATH, help="path of the gold SQLite file")
    p_promote.add_argument(
        "--min-sitelinks",
        type=int,
        default=DEFAULT_MIN_SITELINKS,
        help="also promote persons whose Wikipedia sitelink count is at least N, "
        "even without concert/work evidence (default: $GOLD_MIN_SITELINKS or off)",
    )
    p_promote.add_argument(
        "--drop-unevidenced-persons",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="rule 1: drop persons without concert/work evidence",
    )
    p_promote.add_argument(
        "--collapse-duplicates",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="rule 2: collapse duplicate persons into their canonical row",
    )
    p_promote.add_argument(
        "--prune-unreferenced",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="rule 3: prune entities left unreferenced by the other rules",
    )
    p_promote.set_defaults(func=cmd_promote)

    p_rebuild = sub.add_parser(
        "rebuild-silver",
        help="rebuild the silver database from the bucket with the current heuristics "
        "(human review decisions are preserved)",
    )
    p_rebuild.add_argument(
        "--bucket-path", default=DEFAULT_BUCKET_PATH, help="root directory of the local bucket"
    )
    p_rebuild.set_defaults(func=cmd_rebuild_silver)


def _add_person_parsers(sub: _SubParsers) -> None:
    p_dedupe = sub.add_parser(
        "dedupe-persons", help="link near-duplicate person entities (surname/initials/birth-year heuristics)"
    )
    p_dedupe.set_defaults(func=cmd_dedupe_persons)

    p_concerts = sub.add_parser(
        "derive-concerts", help="rebuild the concert tables from the work mentions' performance context"
    )
    p_concerts.set_defaults(func=cmd_derive_concerts)

    p_recordings = sub.add_parser(
        "derive-recordings", help="rebuild the recording tables from the work mentions' release context"
    )
    p_recordings.set_defaults(func=cmd_derive_recordings)

    p_preview = sub.add_parser(
        "person-review", help="list (or resolve) person duplicate pairs flagged for review"
    )
    p_preview.add_argument("--limit", type=int, default=20, help="max pairs to list")
    p_preview.add_argument("--accept", type=int, metavar="MATCH_ID", help="confirm a duplicate link")
    p_preview.add_argument("--reject", type=int, metavar="MATCH_ID", help="reject a proposed link")
    p_preview.set_defaults(func=cmd_person_review)


def main() -> None:
    parser = argparse.ArgumentParser(prog="composer-ingest", description="Ingest classical composer data")
    parser.add_argument(
        "--database-url",
        help="SQLAlchemy URL (default: $DATABASE_URL or sqlite:///composers.db)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    _add_query_parsers(sub)
    _add_work_parsers(sub)
    _add_pipeline_parsers(sub)
    _add_person_parsers(sub)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    raise SystemExit(args.func(args))
