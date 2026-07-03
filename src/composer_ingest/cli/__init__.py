import argparse
import logging
import sys

from ..etl.gold import DEFAULT_GOLD_DB_PATH
from ..scraper.bucket import DEFAULT_BUCKET_PATH
from ..scraper.sources import REGISTRY
from .ingest_cmds import cmd_fetch, cmd_process, cmd_promote
from .person_cmds import cmd_dedupe_persons, cmd_person_review
from .query_cmds import cmd_claims, cmd_runs, cmd_stats
from .work_cmds import cmd_rematch, cmd_review, cmd_works


def main() -> None:
    parser = argparse.ArgumentParser(prog="composer-ingest", description="Ingest classical composer data")
    parser.add_argument(
        "--database-url",
        help="SQLAlchemy URL (default: $DATABASE_URL or sqlite:///composers.db)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

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

    p_fetch = sub.add_parser("fetch", help="fetch raw records from a source and store in the bucket")
    p_fetch.add_argument("source", choices=sorted(REGISTRY))
    p_fetch.add_argument("--max-pages", type=int, help="stop after N pages (for testing)")
    p_fetch.add_argument(
        "--bucket-path", default=DEFAULT_BUCKET_PATH, help="root directory of the local bucket"
    )
    p_fetch.set_defaults(func=cmd_fetch)

    p_process = sub.add_parser(
        "process", help="ingest previously fetched records from the bucket into the DB"
    )
    p_process.add_argument("source", choices=sorted(REGISTRY))
    p_process.add_argument("--run-id", help="bucket run_id to process (default: latest)")
    p_process.add_argument(
        "--bucket-path", default=DEFAULT_BUCKET_PATH, help="root directory of the local bucket"
    )
    p_process.set_defaults(func=cmd_process)

    p_promote = sub.add_parser(
        "promote", help="rebuild the curated gold database from the bronze (raw) database"
    )
    p_promote.add_argument("--gold-path", default=DEFAULT_GOLD_DB_PATH, help="path of the gold SQLite file")
    p_promote.set_defaults(func=cmd_promote)

    p_dedupe = sub.add_parser(
        "dedupe-persons", help="link near-duplicate person entities (surname/initials/birth-year heuristics)"
    )
    p_dedupe.set_defaults(func=cmd_dedupe_persons)

    p_preview = sub.add_parser(
        "person-review", help="list (or resolve) person duplicate pairs flagged for review"
    )
    p_preview.add_argument("--limit", type=int, default=20, help="max pairs to list")
    p_preview.add_argument("--accept", type=int, metavar="MATCH_ID", help="confirm a duplicate link")
    p_preview.add_argument("--reject", type=int, metavar="MATCH_ID", help="reject a proposed link")
    p_preview.set_defaults(func=cmd_person_review)

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    raise SystemExit(args.func(args))
