"""Helpers for tests that need a real Neo4j.

Every such test empties the database it is pointed at, so two of them running
at once (xdist workers, or two members' suites on one server) would wipe each
other's graph. ``exclusive_neo4j`` serialises them with a file lock keyed by
the server URL: it holds across processes, whatever order xdist schedules the
tests in.
"""

from __future__ import annotations

import fcntl
import hashlib
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def exclusive_neo4j(url: str) -> Generator[None]:
    """Hold the one lock every test on the Neo4j at ``url`` takes."""
    digest = hashlib.sha256(url.encode()).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"composer-neo4j-{digest}.lock"
    with path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
