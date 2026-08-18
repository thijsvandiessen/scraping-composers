"""Settings tests: default values, env var overrides, and .env file loading."""

from pathlib import Path

import pytest
from composer_config import Settings, settings


@pytest.fixture(autouse=True)
def _clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate every test from the caller's real shell env (e.g. a dev's
    exported SCRAPER_CONTACT_EMAIL) and from the repo-root .env: chdir into an
    empty tmp_path and clear any var a field would read."""
    monkeypatch.chdir(tmp_path)
    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)


def test_defaults() -> None:
    s = Settings()
    assert s.database_url == "sqlite:///composers.db"
    assert s.gold_db_path == "./gold.db"
    assert s.gold_min_referrers == 1
    assert s.bucket_path == "./raw-data"
    assert s.crawl_configs_path == "./crawl_configs.json"
    assert s.scraper_contact_email is None
    assert s.admin_api_key is None
    assert s.log_level == "INFO"
    assert s.llm_provider == "ollama"
    assert s.ollama_base_url == "http://localhost:11434"
    assert s.ollama_model == "qwen2.5"
    assert s.ollama_num_ctx == 16384
    assert s.ollama_num_predict == 4096
    assert s.ollama_timeout_s == 300.0
    assert s.google_ai_api_key is None
    assert s.google_ai_model == "gemini-flash-lite-latest"
    assert s.google_ai_base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert s.google_ai_max_output_tokens == 4096
    assert s.google_ai_timeout_s == 300.0
    assert s.google_ai_min_interval_s is None
    assert s.google_ai_max_requests_per_day is None
    assert s.extract_max_chars == 24000
    assert s.extract_max_consecutive_failures == 25
    assert s.extract_cache_path == "./extract-cache.db"
    assert s.extract_cache_enabled is True
    assert s.extract_ledger_enabled is True


def test_env_vars_override_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("GOLD_MIN_REFERRERS", "3")
    monkeypatch.setenv("OLLAMA_TIMEOUT_S", "12.5")
    monkeypatch.setenv("SCRAPER_CONTACT_EMAIL", "me@example.com")

    s = Settings()

    assert s.database_url == "postgresql://x"
    assert s.gold_min_referrers == 3
    assert s.ollama_timeout_s == 12.5
    assert s.scraper_contact_email == "me@example.com"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("true", True), ("1", True), ("yes", True), ("false", False), ("0", False), ("no", False)],
)
def test_bool_env_var_accepts_common_spellings(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("EXTRACT_LEDGER_ENABLED", raw)
    assert Settings().extract_ledger_enabled is expected


def test_env_var_names_are_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("log_level", "DEBUG")
    assert Settings().log_level == "DEBUG"


def test_unknown_env_vars_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    # extra="ignore": settings for other apps' env vars (e.g. the dashboard's
    # DASHBOARD_SECRET_KEY) share the same .env and must not break this one.
    monkeypatch.setenv("DASHBOARD_SECRET_KEY", "unrelated")
    Settings()


def test_env_file_is_loaded(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///from-file.db\nGOLD_MIN_REFERRERS=9\n")
    s = Settings()
    assert s.database_url == "sqlite:///from-file.db"
    assert s.gold_min_referrers == 9


def test_unknown_key_in_env_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SOME_OTHER_APPS_SETTING=x\n")
    Settings()


def test_real_env_var_takes_precedence_over_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("DATABASE_URL=sqlite:///from-file.db\n")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///from-shell.db")
    assert Settings().database_url == "sqlite:///from-shell.db"


def test_model_config_reads_dotenv_and_ignores_extra_keys() -> None:
    assert Settings.model_config.get("env_file") == ".env"
    assert Settings.model_config.get("extra") == "ignore"


def test_module_level_settings_singleton_is_a_settings_instance() -> None:
    assert isinstance(settings, Settings)
