from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///composers.db"
    gold_db_path: str = "./gold.db"
    gold_min_sitelinks: int | None = None
    gold_min_appearances: int = 1
    gold_min_referrers: int = 1
    bucket_path: str = "./raw-data"
    crawl_configs_path: str = "./crawl_configs.json"
    scraper_contact_email: str | None = None

    admin_api_key: str | None = None

    # Neo4j export target (see composer_neo4j). Unset leaves the export off; the
    # admin API then reports "not configured" instead of failing at write time.
    #
    # The username is *not* always "neo4j": an Aura instance may authenticate
    # with its instance id instead, so it is configuration rather than a
    # constant. NEO4J_API_KEY is accepted as an alias for the password because
    # that is what Aura's console calls the value it hands you.
    # Neither the username nor the database is reliably "neo4j": an Aura
    # instance may name both after its instance id. Unset, the database falls
    # back to the connection's home database, which is right for every Aura
    # instance and avoids having to know the name at all.
    neo4j_uri: str | None = None
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = Field(
        default=None, validation_alias=AliasChoices("neo4j_password", "neo4j_api_key")
    )
    neo4j_database: str | None = None

    # Log level for the CLI and the admin API. The crawl and extract stages narrate
    # themselves at INFO; DEBUG adds a line per page, per chunk and per model call.
    log_level: str = "INFO"

    # Local LLM (Ollama) extraction of concerts/performers from crawled pages.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5"
    # Big enough for a full chunk plus its answer: Ollama's own default (4096)
    # silently truncates a 24000-char prompt, which is what sends the model into
    # a repetition loop. Set to None to defer to the server's default.
    ollama_num_ctx: int | None = 16384
    # Hard cap on generated tokens, so a looping model stops instead of emitting
    # tens of thousands of lines of JSON that can only fail to parse.
    ollama_num_predict: int = 4096
    # Per-request timeout; a wedged generation must not stall a whole run.
    ollama_timeout_s: float = 300.0
    # Cap the markdown handed to the model in one call; larger pages are split.
    extract_max_chars: int = 24000
    # Give up on a run after this many chunks in a row yield unusable output:
    # quietly extracting nothing is worse than failing loudly.
    extract_max_consecutive_failures: int = 25
    # Past model answers, keyed by a fingerprint of the request that produced them,
    # so re-extracting a crawl never re-analyses text the model has already read.
    # Deleting the file is the hard reset; `extract --no-cache` bypasses it once.
    extract_cache_path: str = "./extract-cache.db"
    extract_cache_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
