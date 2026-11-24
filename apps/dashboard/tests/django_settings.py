"""Django settings for the dashboard pytest suite.

Sets ``DASHBOARD_DEBUG=1`` and a test ``DASHBOARD_SECRET_KEY`` before importing
``config.settings`` so pytest-django can initialize without production secrets
and Django's HTTP test client can send session cookies without HTTPS.
"""

import os

os.environ.setdefault("DASHBOARD_DEBUG", "1")
os.environ.setdefault("DASHBOARD_SECRET_KEY", "test-only-secret-key")

from config.settings import *  # noqa: F403
