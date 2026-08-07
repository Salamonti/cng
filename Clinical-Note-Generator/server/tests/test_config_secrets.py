"""Regression tests for Step 1: secrets must come from env, not public placeholders.

Config must fail closed (refuse to boot) rather than silently fall back to a
known/source-visible placeholder JWT secret.
"""
import os

import pytest


STRONG = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"  # 64 bytes


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    from server.core import config as config_mod

    config_mod.get_settings.cache_clear()
    yield
    config_mod.get_settings.cache_clear()


def _fresh_settings():
    from server.core import config as config_mod

    return config_mod.get_settings()


def test_env_secrets_load(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", STRONG)
    monkeypatch.setenv("JWT_REFRESH_SECRET", STRONG)
    s = _fresh_settings()
    assert s.jwt_secret == STRONG
    assert s.jwt_refresh_secret == STRONG


def test_placeholder_from_config_is_ignored(monkeypatch):
    # config.json ships "SET_IN_ENV_JWT_SECRET"; env must win, and a missing
    # env value must NOT fall back to the config.json placeholder.
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.setenv("JWT_REFRESH_SECRET", STRONG)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _fresh_settings()


def test_short_secret_denied(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "short-secret")  # 12 bytes < 32
    monkeypatch.setenv("JWT_REFRESH_SECRET", STRONG)
    with pytest.raises(RuntimeError, match="too short"):
        _fresh_settings()


def test_short_refresh_secret_denied(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", STRONG)
    monkeypatch.setenv("JWT_REFRESH_SECRET", "too-short-refresh")  # < 32
    with pytest.raises(RuntimeError, match="too short"):
        _fresh_settings()


def test_missing_both_denied(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("JWT_REFRESH_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT"):
        _fresh_settings()
