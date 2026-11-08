"""Run confirmation: ``last_run_id`` only advances once a run completes (#195).

The ingest loop commits in batches, so a run that dies partway has already
written part of its work. Each row's ``last_seen_at`` is bumped as the run
touches it, but ``last_run_id`` is left alone until :func:`confirm_run` moves
it, in the same transaction that marks the run ``completed``. So:

- ``last_seen_at`` is when any run (failed ones included) last saw the row;
- ``last_run_id`` is the last *completed* run that saw it, except for a row a
  failed run created and no completed run has seen since. That row keeps the
  failed run's id (``last_run_id`` is NOT NULL), which is exactly what marks it
  as unconfirmed.

Unconfirmed rows are kept, not deleted: claims, work titles and concert or
recording links point at them, and a later completed run that sees the row
confirms it. :func:`unconfirmed_counts` reports them per source.
"""

from composer_models import EntityRecord, IngestRun, RawWorkMention, Source
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session


def confirm_run(session: Session, run: IngestRun) -> None:
    """Point every row of ``run``'s source that it saw at ``run``.

    "Saw" is ``last_seen_at >= run.started_at``: the loop stamps each record it
    touches with the current time, and only one run per source is open at a
    time. Rows the run created already carry its id.
    """
    started = select(IngestRun.started_at).where(IngestRun.id == run.id).scalar_subquery()
    for model in (EntityRecord, RawWorkMention):
        session.execute(
            update(model)
            .where(
                model.source_id == run.source_id,
                model.last_seen_at >= started,
                model.last_run_id != run.id,
            )
            .values(last_run_id=run.id)
            .execution_options(synchronize_session=False)
        )


def unconfirmed_counts(session: Session) -> dict[str, tuple[int, int]]:
    """Per source, the (entity records, work mentions) no completed run has
    seen: those whose ``last_run_id`` is a ``failed`` run."""
    counts: dict[str, list[int]] = {}
    for index, model in enumerate((EntityRecord, RawWorkMention)):
        rows = session.execute(
            select(Source.name, func.count(model.id))
            .join(IngestRun, IngestRun.id == model.last_run_id)
            .join(Source, Source.id == model.source_id)
            .where(IngestRun.status == "failed")
            .group_by(Source.name)
        )
        for name, count in rows:
            counts.setdefault(name, [0, 0])[index] = count
    return {name: (records, mentions) for name, (records, mentions) in sorted(counts.items())}
