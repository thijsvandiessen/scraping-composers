import argparse
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from composer_models import PersonMatch
from composer_models.db import get_engine, init_db
from composer_warehouse.persons import (
    MODEL_PATH,
    Partition,
    apply_clusters,
    dedupe_persons,
    reset_person_links,
)
from composer_warehouse.persons.evaluation import (
    LabelledPair,
    downsample,
    legacy_score,
    model_scorer,
    write_dataset,
)
from composer_warehouse.persons.match import PersonScorer, default_model
from composer_warehouse.persons.training import TrainingResult
from sqlalchemy import select

# How many rows of each label provenance the committed evaluation set keeps.
# The negatives run to hundreds of thousands; sampling them keeps the file
# reviewable, and the recorded weights restore the true balance when scoring.
EVAL_CAPS = {"year_conflict": 20000, "distinct_musicbrainz": 20000}


def cmd_dedupe_persons(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        if args.recluster_only:
            # Scoring is the expensive half and its verdicts are already in
            # person_matches; rebuilding the partition from them is seconds.
            print(_partition_summary(apply_clusters(session)))
            return 0
        if args.reset:
            deleted, unlinked = reset_person_links(session)
            print(f"reset {deleted} machine match(es), unlinked {unlinked} entity/ies")
        result = dedupe_persons(session)
        summary = _partition_summary(result.partition)
    print(f"auto-linked {result.auto} duplicate(s), {result.review} pair(s) need review")
    print(summary)
    return 0


def _partition_summary(partition: Partition) -> str:
    """The clustering counts, plus what the authority constraints did to them.

    Refusals are reported rather than dropped: a cannot-link that fires on a
    pair the model scored above the auto threshold says something about the
    model, not just about the pair.
    """
    clustering, constraints = partition.clustering, partition.constraints
    authorities = Counter(
        authority for conflict in constraints.conflicts for authority in conflict.authorities
    )
    return (
        f"{len(clustering.clusters)} cluster(s), {clustering.members} member(s), "
        f"largest {clustering.largest}, {len(clustering.refused)} merge(s) refused\n"
        f"{len(constraints.conflicts)} authority conflict(s) "
        f"({', '.join(f'{name} {count}' for name, count in sorted(authorities.items())) or 'none'}), "
        f"{len(constraints.discharged)} discharged as corroborated"
    )


def cmd_person_train(args: argparse.Namespace) -> int:
    """Refit the linkage model and regenerate the evaluation set."""
    from composer_warehouse.persons.training import train

    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        result = train(session)

    model_path = Path(args.model_out or MODEL_PATH)
    result.model.dump(model_path)
    print(f"wrote {model_path} (prior {result.model.prior:.5f})")
    for comparison in result.model.comparisons:
        bits = {level: round(comparison.bits(level), 2) for level in comparison.levels}
        print(f"  {comparison.name}: {bits}")

    if args.dataset_out:
        rows = write_dataset(
            Path(args.dataset_out),
            downsample(result.labelled, EVAL_CAPS, protect=_contested(result)),
        )
        print(f"wrote {args.dataset_out} ({rows} labelled pairs)")
    return 0


def _contested(result: TrainingResult) -> Callable[[LabelledPair], bool]:
    """Rows either scorer rates as a possible link, and so must be kept whole.

    These are the only rows that can ever turn into a false positive; sampling
    them would make the precision estimate a coin flip. See
    :func:`~composer_warehouse.persons.evaluation.downsample`.
    """
    scorer = PersonScorer(result.model, result.corpus)
    scored = (model_scorer(scorer), model_scorer(scorer, with_years=False))

    def contested(pair: LabelledPair) -> bool:
        # Every scorer person-eval reports on, so each one's false positives are
        # counted exactly rather than extrapolated from a sample.
        return any(score(pair) >= 0.5 for score in scored) or legacy_score(pair) >= 0.70

    return contested


def cmd_person_eval(args: argparse.Namespace) -> int:
    """Score the model and the pre-#173 baseline against the labelled set."""
    from composer_warehouse.persons.evaluation import evaluate, read_dataset, split

    pairs = read_dataset(Path(args.dataset))
    if not args.all:
        # Report on the holdout only: the training half set the parameters, so
        # its numbers say how well the model memorised, not how well it works.
        _, pairs = split(pairs)
    print(f"{len(pairs)} labelled pair(s) ({'full set' if args.all else 'held-out test split'})")
    scorer = PersonScorer(default_model())
    runs = (
        ("fellegi-sunter", model_scorer(scorer), args.threshold),
        ("fellegi-sunter (names only)", model_scorer(scorer, with_years=False), args.threshold),
        ("legacy scorer (baseline)", legacy_score, 0.90),
    )
    for name, score_fn, threshold in runs:
        print(f"\n{name} @ {threshold}")
        for provenance, metrics in sorted(evaluate(pairs, score_fn, threshold).items()):
            print(
                f"  {provenance:22s} precision={metrics.precision:.4f} recall={metrics.recall:.4f}"
                f" f1={metrics.f1:.4f} fp={metrics.false_positive:.0f}"
            )
    return 0


def cmd_person_review(args: argparse.Namespace) -> int:
    engine = get_engine(args.database_url)
    session_factory = init_db(engine)
    with session_factory() as session:
        if args.accept is not None or args.reject is not None:
            match_id = args.accept if args.accept is not None else args.reject
            match = session.get(PersonMatch, match_id)
            if match is None or match.status != "needs_review":
                print("no pending match with that id")
                return 1
            if args.accept is not None:
                match.status = "accepted"
                print(f"linked {match.entity.label!r} -> {match.canonical.label!r}")
            else:
                match.status = "rejected"
                print(f"rejected match #{match.id}")
            session.commit()
            # A decision changes the partition, not just this pair: an accept
            # can join two clusters and a reject can split one, and either way
            # the canonical is re-chosen from the whole membership.
            apply_clusters(session)
            return 0

        rows = session.scalars(
            select(PersonMatch)
            .where(PersonMatch.status == "needs_review")
            .order_by(PersonMatch.score.desc())
            .limit(args.limit)
        ).all()
        if not rows:
            print("no person matches need review")
            return 0
        print("person matches needing review (resolve with --accept ID or --reject ID):")
        for match in rows:
            print(
                f"\n#{match.id} [{match.score:.3f} {match.method}]"
                f" {match.entity.label!r} -> {match.canonical.label!r}"
            )
    return 0


__all__ = [
    "cmd_dedupe_persons",
    "cmd_person_eval",
    "cmd_person_review",
    "cmd_person_train",
]
