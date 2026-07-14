"""Django settings for the scraper dashboard (Unfold admin theme).

The dashboard shows and triggers scrapers exclusively through the admin
FastAPI API — it never connects to the composers database. The SQLite file
configured below belongs to Django itself (admin login users and sessions,
nothing else); scraper data cannot end up in it because the ``scrapers`` app
defines no models.
"""

import os
from pathlib import Path

from django.urls import reverse_lazy

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DASHBOARD_SECRET_KEY", "dev-only-insecure-key")
DEBUG = os.environ.get("DASHBOARD_DEBUG", "0") == "1"
ALLOWED_HOSTS = os.environ.get("DASHBOARD_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")

# Where the FastAPI apps live; everything the dashboard shows comes from
# these values plus what the APIs return. Gold is the curated database
# (built by promote), silver the staging database.
ADMIN_API_URL = os.environ.get("ADMIN_API_URL", "http://localhost:8001")
ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY")
GOLD_API_URL = os.environ.get("GOLD_API_URL", "http://localhost:8000")
SILVER_API_URL = os.environ.get("SILVER_API_URL", "http://localhost:8003")

INSTALLED_APPS = [
    "unfold",  # must precede django.contrib.admin
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "scrapers",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    }
]

# Django's own storage (auth users, sessions) — NOT the composers database.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("DASHBOARD_DB_PATH", str(BASE_DIR / "dashboard.sqlite3")),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"
USE_TZ = True

UNFOLD = {
    "SITE_TITLE": "composer-ingest",
    "SITE_HEADER": "composer-ingest",
    "SITE_URL": None,
    "SIDEBAR": {
        "show_search": False,
        "navigation": [
            {
                "title": "Ingestion",
                "items": [
                    {
                        "title": "Scrapers",
                        "icon": "cloud_download",
                        "link": reverse_lazy("scrapers_index"),
                    },
                    {
                        "title": "Load",
                        "icon": "database",
                        "link": reverse_lazy("load_index"),
                    },
                    {
                        "title": "Promote",
                        "icon": "upgrade",
                        "link": reverse_lazy("promote"),
                    },
                ],
            },
            {
                "title": "Data (silver)",
                "items": [
                    {
                        "title": "Overview",
                        "icon": "monitoring",
                        "link": reverse_lazy("data_overview"),
                    },
                    {
                        "title": "Entities",
                        "icon": "person_search",
                        "link": reverse_lazy("entities"),
                    },
                    {
                        "title": "Works",
                        "icon": "library_music",
                        "link": reverse_lazy("works"),
                    },
                    {
                        "title": "Review",
                        "icon": "rule",
                        "link": reverse_lazy("review"),
                    },
                ],
            },
            {
                "title": "Musicians (gold)",
                "items": [
                    {
                        "title": "Composers",
                        "icon": "music_note",
                        "link": reverse_lazy("people", args=["composers"]),
                    },
                    {
                        "title": "Soloists",
                        "icon": "star",
                        "link": reverse_lazy("people", args=["soloists"]),
                    },
                    {
                        "title": "Conductors",
                        "icon": "front_hand",
                        "link": reverse_lazy("people", args=["conductors"]),
                    },
                    {
                        "title": "Works",
                        "icon": "library_music",
                        "link": reverse_lazy("gold_works"),
                    },
                    {
                        "title": "Concerts",
                        "icon": "event",
                        "link": reverse_lazy("concerts_list"),
                    },
                ],
            },
        ],
    },
}
