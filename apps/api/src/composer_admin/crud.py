from composer_ingest.etl.models import IngestRun, Source
from sqlalchemy import select
from sqlalchemy.orm import Session

from .schemas import RunOut


def _to_run_out(run: IngestRun, source_name: str) -> RunOut:
    return RunOut(
        id=run.id,
        source=source_name,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        records_seen=run.records_seen,
        records_new=run.records_new,
        error=run.error,
    )


def list_runs(session: Session, limit: int) -> list[RunOut]:
    rows = session.execute(
        select(IngestRun, Source.name).join(Source).order_by(IngestRun.started_at.desc()).limit(limit)
    ).all()
    return [_to_run_out(run, name) for run, name in rows]


def get_run(session: Session, run_id: int) -> RunOut | None:
    row = session.execute(select(IngestRun, Source.name).join(Source).where(IngestRun.id == run_id)).first()
    if row is None:
        return None
    return _to_run_out(row[0], row[1])


def has_running(session: Session, source_name: str) -> bool:
    """Whether a run for ``source_name`` is already in progress."""
    run_id = session.scalar(
        select(IngestRun.id)
        .join(Source)
        .where(Source.name == source_name, IngestRun.status == "running")
        .limit(1)
    )
    return run_id is not None
