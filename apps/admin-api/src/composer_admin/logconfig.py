"""Give this app's own loggers a handler, which uvicorn does not.

Uvicorn's default logging config attaches handlers to the ``uvicorn*`` loggers
only and leaves the root logger bare. Records from ``composer_crawler`` /
``composer_extract`` therefore propagate to a root logger with nothing on it and
fall through to :data:`logging.lastResort`, which drops anything below WARNING —
so every progress line a background crawl or extract emits is discarded, and the
long-running stages triggered from the dashboard look silent even when they are
working. Installing one handler here is what makes them visible.

Named ``logconfig`` rather than ``logging`` so nothing inside the package has to
think about which module an ``import logging`` resolves to.
"""

from __future__ import annotations

import logging
import sys

from composer_config import settings

#: Same shape as the CLI's basicConfig, so both surfaces read alike.
FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def safe_for_log(value: str) -> str:
    """A request-supplied value with its line breaks removed (CWE-117).

    :data:`FORMAT` writes one unstructured line per record, so a newline inside an
    interpolated value forges a second entry. The validators this value passes
    upstream (``_validated_segment``, ``_validate_source_name``) already reject
    control characters, which is the actual fix; this is the same barrier spelled
    the way a static analyser can follow it, at the point of use.
    """
    return value.replace("\r\n", "").replace("\n", "").replace("\r", "")


def configure_logging() -> None:
    """Attach a stderr handler at ``$LOG_LEVEL`` to the root logger, once.

    Idempotent, and a no-op when something has already configured the root logger
    (a test harness, or a deployment passing uvicorn its own ``--log-config``):
    the point is to not be silent, not to own everyone else's setup.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
