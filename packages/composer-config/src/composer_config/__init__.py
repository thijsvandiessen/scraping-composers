from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///composers.db"
    gold_db_path: str = "./gold.db"
    gold_min_sitelinks: int | None = None
    bucket_path: str = "./raw-data"
    crawl_configs_path: str = "./crawl_configs.json"
    scraper_contact_email: str | None = None

    admin_api_key: str | None = None

    # Local LLM (Ollama) extraction of concerts/performers from crawled pages.
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5"
    ollama_num_ctx: int | None = None
    # Cap the markdown handed to the model in one call; larger pages are split.
    extract_max_chars: int = 24000

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
