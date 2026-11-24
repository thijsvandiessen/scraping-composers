"""Tests for dashboard Django settings security defaults and fail-closed secret key."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from django.core.exceptions import ImproperlyConfigured

DASHBOARD_DIR = Path(__file__).resolve().parent.parent


def _load_fresh_settings(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> ModuleType:
    for key in ("DASHBOARD_DEBUG", "DASHBOARD_SECRET_KEY"):
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


def test_production_without_secret_key_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ImproperlyConfigured, match="DASHBOARD_SECRET_KEY"):
        _load_fresh_settings(monkeypatch, {})


def test_production_with_secret_key_enables_security_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_fresh_settings(monkeypatch, {"DASHBOARD_SECRET_KEY": "prod-secret"})
    assert mod.DEBUG is False
    assert mod.SECRET_KEY == "prod-secret"
    assert mod.SESSION_COOKIE_SECURE is True
    assert mod.CSRF_COOKIE_SECURE is True
    assert mod.SECURE_HSTS_SECONDS == 31536000
    assert mod.SECURE_CONTENT_TYPE_NOSNIFF is True


def test_debug_allows_default_secret_key_without_https_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_fresh_settings(monkeypatch, {"DASHBOARD_DEBUG": "1"})
    assert mod.DEBUG is True
    assert mod.SECRET_KEY == "dev-only-insecure-key"
    assert not hasattr(mod, "SESSION_COOKIE_SECURE")
    assert not hasattr(mod, "CSRF_COOKIE_SECURE")
    assert not hasattr(mod, "SECURE_HSTS_SECONDS")
    assert not hasattr(mod, "SECURE_CONTENT_TYPE_NOSNIFF")
