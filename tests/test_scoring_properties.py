"""Property-based tests for the scorer.

Validates invariants that should hold for *any* valid input:
  - composite score is always in [0, 1]
  - volume_quality is in [0, 1]
  - volatility_character is in [0, 1]
  - rug_resistance is in [0, 1] (or 0.5 for unknown)
  - rug_risk is in [0, 1]
  - explanation is non-empty when score is computed
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from bonnet.models import Chain, Pair, Token
from bonnet.rug import aggregate as aggregate_rug
from bonnet.scoring.scorer import score_pair


def _random_pair(rng: random.Random) -> Pair:
    """Generate a randomized Pair for property testing."""
    days_old = rng.uniform(0, 365)
    created_at = datetime.now(timezone.utc) - timedelta(days=days_old)
    v24 = rng.uniform(0, 1_000_000)
    v6 = rng.uniform(0, v24)
    v1 = rng.uniform(0, v6)
    liq = rng.uniform(100, 1_000_000)
    change24 = rng.uniform(-100, 500)
    change1 = rng.uniform(-50, 100)
    return Pair(
        pair_address="0x" + "a" * 40,
        chain=Chain.ROBINHOOD,
        dex="uniswap",
        token=Token(
            address="0x" + "1" * 40,
            chain=Chain.ROBINHOOD,
            symbol=f"T{rng.randint(0, 9999)}",
        ),
        quote_symbol="WETH",
        created_at=created_at,
        volume_usd_24h=v24,
        volume_usd_6h=v6,
        volume_usd_1h=v1,
        liquidity_usd=liq,
        price_usd=0.001,
        price_change_pct_24h=change24,
        price_change_pct_1h=change1,
        txns_24h=rng.randint(0, 1000),
        txns_24h_buys=rng.randint(0, 500),
        txns_24h_sells=rng.randint(0, 500),
    )


def _random_kwargs(rng: random.Random) -> dict:
    """Random optional scoring kwargs."""
    kwargs: dict = {}
    if rng.random() < 0.5:
        kwargs["mint_renounced"] = rng.choice([True, False])
    if rng.random() < 0.5:
        kwargs["freeze_renounced"] = rng.choice([True, False])
    if rng.random() < 0.5:
        kwargs["owner_renounced"] = rng.choice([True, False])
    if rng.random() < 0.5:
        kwargs["lp_locked"] = rng.choice([True, False])
    if rng.random() < 0.5:
        kwargs["contract_age_hours"] = rng.uniform(0, 24 * 90)
    if rng.random() < 0.3:
        kwargs["top10_holder_pct"] = rng.uniform(0, 100)
    if rng.random() < 0.3:
        kwargs["top1_holder_pct"] = rng.uniform(0, 100)
    if rng.random() < 0.2:
        kwargs["is_honeypot"] = rng.choice([True, False])
    if rng.random() < 0.3:
        kwargs["has_proxy"] = rng.choice([True, False])
    if rng.random() < 0.3:
        kwargs["is_contract_verified"] = rng.choice([True, False])
    return kwargs


class TestScorerInvariants:
    def test_composite_in_unit_interval(self) -> None:
        rng = random.Random(42)
        for _ in range(200):
            pair = _random_pair(rng)
            kwargs = _random_kwargs(rng)
            s = score_pair(pair, **kwargs)
            assert 0.0 <= s.composite <= 1.0, f"composite {s.composite} out of range"

    def test_components_in_unit_interval(self) -> None:
        rng = random.Random(42)
        for _ in range(100):
            pair = _random_pair(rng)
            kwargs = _random_kwargs(rng)
            s = score_pair(pair, **kwargs)
            assert 0.0 <= s.components.volume_quality <= 1.0
            assert 0.0 <= s.components.volatility_character <= 1.0
            assert 0.0 <= s.components.rug_resistance <= 1.0

    def test_rug_risk_in_unit_interval(self) -> None:
        rng = random.Random(42)
        for _ in range(100):
            pair = _random_pair(rng)
            kwargs = _random_kwargs(rng)
            s = score_pair(pair, **kwargs)
            assert 0.0 <= s.rug_signals.rug_risk <= 1.0

    def test_explanation_non_empty(self) -> None:
        rng = random.Random(42)
        for _ in range(50):
            pair = _random_pair(rng)
            kwargs = _random_kwargs(rng)
            s = score_pair(pair, **kwargs)
            assert len(s.explanation) > 0

    def test_honeypot_forces_low_rug_resistance(self) -> None:
        # Honeypot tokens must score rug_resistance = 0
        rng = random.Random(42)
        for _ in range(20):
            pair = _random_pair(rng)
            kwargs = _random_kwargs(rng)
            kwargs["is_honeypot"] = True
            s = score_pair(pair, **kwargs)
            assert s.components.rug_resistance == 0.0


class TestRugAggregatorInvariants:
    def test_aggregate_risk_in_unit_interval(self) -> None:
        rng = random.Random(42)
        for _ in range(100):
            kwargs = {
                "top10_pct": rng.uniform(0, 100) if rng.random() < 0.5 else None,
                "top1_pct": rng.uniform(0, 100) if rng.random() < 0.5 else None,
                "mint_renounced": rng.choice([True, False, None]),
                "freeze_renounced": rng.choice([True, False, None]),
                "owner_renounced": rng.choice([True, False, None]),
                "lp_locked": rng.choice([True, False, None]),
                "lp_lock_days_remaining": (
                    rng.randint(0, 365) if rng.random() < 0.5 else None
                ),
                "contract_age_hours": (
                    rng.uniform(0, 24 * 90) if rng.random() < 0.5 else None
                ),
                "is_honeypot": rng.choice([True, False, None]),
                "deployer_prior_rugs": (
                    rng.randint(0, 10) if rng.random() < 0.5 else None
                ),
                "deployer_prior_tokens": (
                    rng.randint(0, 20) if rng.random() < 0.5 else None
                ),
            }
            signals = aggregate_rug(**kwargs)
            assert 0.0 <= signals.rug_risk <= 1.0

    def test_honeypot_forces_max_risk(self) -> None:
        signals = aggregate_rug(is_honeypot=True)
        assert signals.rug_risk == 1.0

    def test_all_clean_signals_zero_risk(self) -> None:
        # The cleanest possible token
        signals = aggregate_rug(
            top10_pct=10.0,
            top1_pct=3.0,
            mint_renounced=True,
            freeze_renounced=True,
            owner_renounced=True,
            lp_locked=True,
            lp_lock_days_remaining=365,
            contract_age_hours=24 * 90,
        )
        assert signals.rug_risk == 0.0