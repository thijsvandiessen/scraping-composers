from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///composers.db"
    # Postgres only: the schema holding silver. rebuild-silver builds into a
    # staging schema and renames it into this name, so nothing else may live
    # here — it is replaced wholesale by every rebuild.
    silver_schema: str = "silver"
    # Where promote writes gold and the gold API reads it: a SQLite file, or a
    # Postgres server of its own (see docker-compose's postgres-gold).
    gold_database_url: str = "sqlite:///gold.db"
    # Postgres only: the schema holding gold, replaced wholesale by every promote
    # just as silver_schema is by every rebuild.
    gold_schema: str = "gold"
    gold_min_referrers: int = 1
    bucket_path: str = "./raw-data"
    scraper_contact_email: str | None = None

    admin_api_key: str | None = None

    # Log level for the CLI and the admin API. Fetches narrate themselves at
    # INFO; DEBUG adds a line per request.
    log_level: str = "INFO"

    # Pages already fetched by a scraper, so a source whose archive never changes
    # is fetched once. Deleting the file is the hard reset; PAGE_CACHE_ENABLED=false
    # bypasses it for a run (see composer_http.pages).
    page_cache_path: str = "./page-cache.db"
    page_cache_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
