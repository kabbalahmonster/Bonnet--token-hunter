"""Tests for Settings loading from env."""
from __future__ import annotations

import pytest

from bonnet.settings import Settings, get_settings, reset_cache


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BONNET_RPC_ENDPOINTS", raising=False)
    monkeypatch.delenv("BONNET_SCORE_ALERT_THRESHOLD", raising=False)
    reset_cache()
    s = Settings()
    assert s.score_alert_threshold == 0.65
    assert s.volume_min_usd_24h == 5_000.0
    assert isinstance(s.rpc_endpoints, list)
    assert len(s.rpc_endpoints) >= 1


def test_settings_comma_separated_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BONNET_RPC_ENDPOINTS", "https://a.example, https://b.example ,,https://c.example")
    reset_cache()
    s = Settings()
    assert s.rpc_endpoints == ["https://a.example", "https://b.example", "https://c.example"]


def test_settings_get_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BONNET_SCORE_ALERT_THRESHOLD", raising=False)
    reset_cache()
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
