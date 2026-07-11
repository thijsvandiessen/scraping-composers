"""Admin API — manage and manually trigger scrapers.

A separate FastAPI app (``admin_app``) from the read-only consumer ``api``
package, so it can be deployed and secured independently (its own process /
environment). Run it with::

    uvicorn composer_admin:admin_app --port 8001
"""

from dotenv import load_dotenv

load_dotenv()

from .main import admin_app

__all__ = ["admin_app"]
