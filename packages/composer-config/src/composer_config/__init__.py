from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///composers.db"
    gold_db_path: str = "./gold.db"
    gold_min_referrers: int = 1
    bucket_path: str = "./raw-data"
    crawl_configs_path: str = "./crawl_configs.json"
    scraper_contact_email: str | None = None

    admin_api_key: str | None = None

    # Log level for the CLI and the admin API. The crawl and extract stages narrate
    # themselves at INFO; DEBUG adds a line per page, per chunk and per model call.
    log_level: str = "INFO"

    # Which backend extracts concerts/performers from crawled pages: "ollama" (a
    # local model) or "gemini" (Google's hosted API). One provider per run — the
    # extractor built from these settings is what every extract kind uses.
    llm_provider: str = "ollama"

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

    # Google AI (Gemini) extraction, used instead of Ollama when llm_provider="gemini".
    google_ai_api_key: str | None = None
    google_ai_model: str = "gemini-flash-lite-latest"
    google_ai_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # Same role as ollama_num_predict: a hard cap on generated tokens.
    google_ai_max_output_tokens: int = 4096
    google_ai_timeout_s: float = 300.0
    google_ai_min_interval_s: float | None = None
    google_ai_max_requests_per_day: int | None = None
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
    # What each page last produced per extract kind (same file, a separate table),
    # so a page whose content and extractor fingerprint are unchanged skips the
    # model entirely instead of only having its answer cache-hit. `extract
    # --no-ledger` bypasses it once; `--no-cache` bypasses both (see extract_cmds).
    extract_ledger_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
