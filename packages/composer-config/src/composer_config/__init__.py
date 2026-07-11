from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///composers.db"
    gold_db_path: str = "./gold.db"
    gold_min_sitelinks: int | None = None
    bucket_path: str = "./raw-data"
    scraper_contact_email: str | None = None

    admin_api_key: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


settings = Settings()
