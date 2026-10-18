"""``composer-ingest`` with the synthetic sources swapped into the registry.

The CLI builds its ``fetch``/``process`` choices from ``REGISTRY`` when it
parses, so registering before ``main()`` is all it takes; everything after that
is the production code path.
"""

from composer_cli import main
from composer_scrapers import REGISTRY

from .sources import SYNTHETIC_SOURCES

for adapter in SYNTHETIC_SOURCES:
    REGISTRY[adapter.name] = adapter

main()
