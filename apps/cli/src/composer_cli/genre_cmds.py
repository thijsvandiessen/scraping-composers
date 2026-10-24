import argparse

from composer_models.db import get_engine, init_db
from composer_models.genres import GENRE_KINDS, TOP_LEVEL
from composer_warehouse.genres import GenreReport, genre_report, link_label_genres


def _print_report(report: GenreReport) -> None:
    print(f"{report.kind}s: {report.total}")
    print(f"  keyed on a QID         {report.qid_keyed}")
    print(f"  label only             {report.label_only} ({report.legacy_linked} linked to a QID one)")
    print(f"  with a parent          {report.with_parent}")
    print(f"  orphans                {report.orphans} (no subclass_of path to a top-level {report.kind})")
    if report.missing_top_level:
        print(f"  top-level not in silver: {', '.join(report.missing_top_level)}")
    if report.top_orphans:
        print("  most-used orphans:")
        for orphan in report.top_orphans:
            qid = f" ({orphan.wikidata_id})" if orphan.wikidata_id else ""
            print(f"    {orphan.persons:>6}  {orphan.label}{qid}")


def cmd_genres(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    kinds = [args.kind] if args.kind else list(GENRE_KINDS)
    with session_factory() as session:
        for kind in kinds:
            _print_report(genre_report(session, kind, TOP_LEVEL[kind], limit=args.limit))
    return 0


def cmd_link_genres(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        stats = link_label_genres(session)
    print(f"linked {stats.linked} label-only genres/movements to their QID entity")
    print(f"  ambiguous              {stats.ambiguous}")
    print(f"  unmatched              {stats.unmatched}")
    return 0
