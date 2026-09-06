"""Tests for trend analysis (no live RPC)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bonnet.models import Chain, Pair, RugSignals, Score, ScoreComponents, Token
from bonnet.trend import compute_trend, should_alert_with_trend


def _make_score(composite: float, hours_ago: float = 0.0) -> Score:
    """Build a minimal Score object with the given composite."""
    return Score(
        token=Token(address="0x" + "1" * 40, chain=Chain.ROBINHOOD, symbol="T"),
        pair=Pair(pair_address="0x" + "a" * 40, chain=Chain.ROBINHOOD, token=Token(
            address="0x" + "1" * 40, chain=Chain.ROBINHOOD
        )),
        components=ScoreComponents(
            volume_quality=composite,
            volatility_character=composite,
            rug_resistance=composite,
        ),
        rug_signals=RugSignals(rug_risk=1 - composite),
        composite=composite,
        scored_at=datetime.now(UTC) - timedelta(hours=hours_ago),
    )


class TestComputeTrend:
    def test_insufficient_history(self) -> None:
        score = _make_score(0.5)
        trend = compute_trend([score])
        assert trend.direction == "unknown"
        assert trend.samples_used == 1

    def test_rising_direction(self) -> None:
        scores = [_make_score(0.4, hours_ago=2.0), _make_score(0.7, hours_ago=0.0)]
        trend = compute_trend(scores)
        assert trend.direction == "rising"
        assert trend.delta == pytest.approx(0.3)
        assert trend.pct_change == pytest.approx(75.0)

    def test_falling_direction(self) -> None:
        scores = [_make_score(0.7, hours_ago=2.0), _make_score(0.4, hours_ago=0.0)]
        trend = compute_trend(scores)
        assert trend.direction == "falling"
        assert trend.delta < 0

    def test_flat_direction(self) -> None:
        scores = [_make_score(0.5, hours_ago=2.0), _make_score(0.52, hours_ago=0.0)]
        trend = compute_trend(scores)
        assert trend.direction == "flat"

    def test_breakout_detected(self) -> None:
        scores = [
            _make_score(0.5, hours_ago=3.0),
            _make_score(0.62, hours_ago=2.0),
            _make_score(0.68, hours_ago=0.0),  # just crossed 0.65
        ]
        trend = compute_trend(scores, threshold=0.65)
        assert trend.is_breakout is True
        assert trend.is_breakdown is False

    def test_breakdown_detected(self) -> None:
        scores = [
            _make_score(0.7, hours_ago=3.0),
            _make_score(0.68, hours_ago=2.0),
            _make_score(0.6, hours_ago=0.0),  # just crossed 0.65 downward
        ]
        trend = compute_trend(scores, threshold=0.65)
        assert trend.is_breakdown is True
        assert trend.is_breakout is False

    def test_lookback_with_many_samples(self) -> None:
        # 10 samples: trending up over the lookback window
        scores = [_make_score(0.4 + i * 0.05, hours_ago=10 - i) for i in range(10)]
        trend = compute_trend(scores, lookback=5)
        # Should compare current vs sample 5 back
        assert trend.direction == "rising"
        assert trend.samples_used == 10


class TestShouldAlertWithTrend:
    def test_above_threshold_alerts(self) -> None:
        # Going from 0.7 (already above) to 0.75 — no breakout
        score = _make_score(0.75)
        trend = compute_trend([_make_score(0.7), score])
        should, reason = should_alert_with_trend(score, trend)
        assert should is True
        assert reason == "above_threshold"

    def test_breakout_alerts(self) -> None:
        score = _make_score(0.68)
        trend = compute_trend([_make_score(0.5), score])
        should, reason = should_alert_with_trend(score, trend, threshold=0.65)
        assert should is True
        assert reason == "breakout"

    def test_below_threshold_doesnt_alert(self) -> None:
        score = _make_score(0.5)
        trend = compute_trend([_make_score(0.4), score])
        should, reason = should_alert_with_trend(score, trend)
        assert should is False
        assert reason == "below_threshold"

    def test_cooldown_blocks_alert(self) -> None:
        score = _make_score(0.8)
        trend = compute_trend([_make_score(0.5), score])
        should, reason = should_alert_with_trend(
            score, trend, cooldown_hours=6.0, hours_since_last_alert=2.0
        )
        assert should is False
        assert reason == "cooldown"

    def test_rising_fast_below_threshold(self) -> None:
        # Big jump from 0.3 to 0.5 = 67% increase, delta 0.2
        score = _make_score(0.5)
        trend = compute_trend([_make_score(0.3), score])
        should, reason = should_alert_with_trend(score, trend)
        assert should is True
        assert reason == "rising_fast"
