"""Tests for dashboard Django settings security defaults, typed config, and fail-closed secret key."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from django.core.exceptions import ImproperlyConfigured

DASHBOARD_DIR = Path(__file__).resolve().parent.parent

_ENV_KEYS = (
    "DASHBOARD_DEBUG",
    "DASHBOARD_SECRET_KEY",
    "DASHBOARD_ALLOWED_HOSTS",
    "DASHBOARD_DB_PATH",
    "ADMIN_API_URL",
    "ADMIN_API_KEY",
    "GOLD_API_URL",
    "SILVER_API_URL",
)


def _load_fresh_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str]) -> ModuleType:
    monkeypatch.chdir(tmp_path)
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    if str(DASHBOARD_DIR) not in sys.path:
        monkeypatch.syspath_prepend(str(DASHBOARD_DIR))

    # Load config.settings in an isolated module name so we don't mutate the live
    # module already bound into django.conf.settings.
    spec = importlib.util.spec_from_file_location(
        "_test_config_settings", DASHBOARD_DIR / "config/settings.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_production_without_secret_key_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    with pytest.raises(ImproperlyConfigured, match="DASHBOARD_SECRET_KEY"):
        _load_fresh_settings(monkeypatch, tmp_path, {})


def test_production_with_secret_key_enables_security_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _load_fresh_settings(monkeypatch, tmp_path, {"DASHBOARD_SECRET_KEY": "prod-secret"})
    assert mod.DEBUG is False
    assert mod.SECRET_KEY == "prod-secret"
    assert mod.SESSION_COOKIE_SECURE is True
    assert mod.CSRF_COOKIE_SECURE is True
    assert mod.SECURE_HSTS_SECONDS == 31536000
    assert mod.SECURE_CONTENT_TYPE_NOSNIFF is True


def test_debug_allows_default_secret_key_without_https_cookies(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mod = _load_fresh_settings(monkeypatch, tmp_path, {"DASHBOARD_DEBUG": "1"})
    assert mod.DEBUG is True
    assert mod.SECRET_KEY == "dev-only-insecure-key"
    assert not hasattr(mod, "SESSION_COOKIE_SECURE")
    assert not hasattr(mod, "CSRF_COOKIE_SECURE")
    assert not hasattr(mod, "SECURE_HSTS_SECONDS")
    assert not hasattr(mod, "SECURE_CONTENT_TYPE_NOSNIFF")


def test_typed_settings_defaults_and_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    mod = _load_fresh_settings(
        monkeypatch,
        tmp_path,
        {
            "DASHBOARD_DEBUG": "true",
            "DASHBOARD_ALLOWED_HOSTS": "example.com, admin.example.com , ",
            "DASHBOARD_DB_PATH": "/tmp/custom-dashboard.sqlite3",
            "ADMIN_API_URL": "http://admin.internal:9001",
            "ADMIN_API_KEY": "top-secret-key",
            "GOLD_API_URL": "http://gold.internal:9000",
            "SILVER_API_URL": "http://silver.internal:9003",
        },
    )
    assert mod.DEBUG is True
    assert mod.ALLOWED_HOSTS == ["example.com", "admin.example.com"]
    assert mod.DATABASES["default"]["NAME"] == "/tmp/custom-dashboard.sqlite3"
    assert mod.ADMIN_API_URL == "http://admin.internal:9001"
    assert mod.ADMIN_API_KEY == "top-secret-key"
    assert mod.GOLD_API_URL == "http://gold.internal:9000"
    assert mod.SILVER_API_URL == "http://silver.internal:9003"
    # Secrets are masked in DashboardSettings repr.
    rendered = repr(mod._env)
    assert "top-secret-key" not in rendered
    assert "**********" in rendered


def test_typed_settings_reads_dotenv_and_ignores_extra_keys(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / ".env").write_text(
        "DASHBOARD_DEBUG=1\nADMIN_API_URL=http://from-dotenv:8001\nUNRELATED_KEY=ignored\n"
    )
    mod = _load_fresh_settings(monkeypatch, tmp_path, {})
    assert mod.DEBUG is True
    assert mod.ADMIN_API_URL == "http://from-dotenv:8001"
