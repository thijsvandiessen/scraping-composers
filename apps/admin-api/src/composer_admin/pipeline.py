"""Chain crawl → extract → load behind a single trigger.

The stages themselves are unchanged: each still writes its own bucket snapshot or
ingest run and is a self-contained unit of work. This module only removes the
three separate clicks, by running them back to back and stopping at the first one
that fails. When the stages later move onto a queue, what changes is how
:func:`run_pipeline` dispatches them — not the stages.

The crawl and extract stages are injected rather than imported so this module
stays independent of the HTTP layer that owns them (and of its test seams); the
load stage has no such owner and lives here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

from composer_bronze.scraper import new_snapshot_id
from composer_crawler import CrawlConfig
from composer_warehouse.ingestion import create_run

from .deps import session_scope
from .snapshots import process_in_background, source_base_url

log = logging.getLogger(__name__)

#: (config, snapshot_id, max_pages) -> succeeded
CrawlStage = Callable[[CrawlConfig, str, int | None], bool]
#: (source, crawl_run_id, snapshot_id, extract_kinds) -> succeeded
ExtractStage = Callable[[str, str, str, Sequence[str]], bool]


def load_extracted(name: str, snapshot_id: str) -> bool:
    """Ingest an extracted snapshot: open the run, then execute it.

    The same two steps the Load button drives, minus the request-scoped session —
    a background chain has to open its own.
    """
    try:
        with session_scope() as session:
            run_id = create_run(session, name, source_base_url(name)).id
        process_in_background(name, snapshot_id, run_id)
    except Exception:
        log.exception("pipeline load failed for %s/%s", name, snapshot_id)
        return False
    return True


def _stage(name: str, label: str, run: Callable[[], bool]) -> bool:
    log.info("pipeline %s: %s", name, label)
    if run():
        return True
    log.warning("pipeline %s: stopped at %s", name, label)
    return False


def run_pipeline(config: CrawlConfig, crawl_id: str, crawl: CrawlStage, extract: ExtractStage) -> None:
    """Crawl into *crawl_id*, extract what it found, then load the result.

    The extract snapshot is only named once the crawl has finished, so its id
    always sorts after the crawl's and "the latest snapshot" stays unambiguous.
    """
    name = config.name
    if not _stage(name, "crawl", lambda: crawl(config, crawl_id, None)):
        return
    extract_id = new_snapshot_id()
    if not _stage(name, "extract", lambda: extract(name, crawl_id, extract_id, config.extract_kinds)):
        return
    if _stage(name, "load", lambda: load_extracted(name, extract_id)):
        log.info("pipeline %s: complete", name)
