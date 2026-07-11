"""Dump the gold consumer API's OpenAPI schema to stdout.

Run from the repo root via uv so the ``composer_api`` workspace package resolves:

    uv run python apps/frontend/scripts/dump_openapi.py > apps/frontend/openapi.json

``gold_app.openapi()`` only introspects the routes and Pydantic models; the app's
database session provider is created lazily on the first request (see
``create_app`` in ``apps/consumer-api/src/composer_api/main.py``), so this needs
neither a running server nor ``gold.db``. The frontend's zod schemas are generated
from the committed ``openapi.json`` (see ``apps/frontend/openapi-ts.config.ts``).
"""

import json

from composer_api import gold_app


def main() -> None:
    print(json.dumps(gold_app.openapi(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
