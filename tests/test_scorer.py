"""Tests for the composite scorer across token archetypes."""
from __future__ import annotations

from bonnet.scoring.scorer import score_pair

from . import fixtures as fx


class TestScorer:
    def test_good_steady_above_threshold(self) -> None:
        s = score_pair(
            fx.good_volume_steady(),
            mint_renounced=True,
            freeze_renounced=True,
            owner_renounced=True,
            lp_locked=True,
            lp_lock_days_remaining=180,
            contract_age_hours=14 * 24,
            top10_holder_pct=25.0,
            top1_holder_pct=8.0,
            is_contract_verified=True,
        )
        assert s.composite > 0.5, f"good steady should exceed 0.5, got {s.composite}"
        # Rug resistance should be high
        assert s.components.rug_resistance > 0.7

    def test_rug_in_progress_below_threshold(self) -> None:
        s = score_pair(
            fx.rug_in_progress(),
            mint_renounced=False,  # dev can still mint
            lp_locked=False,
            contract_age_hours=48,
            top10_holder_pct=70.0,
            top1_holder_pct=35.0,
        )
        # Should be low because of direction_penalty + rug signals
        assert s.composite < 0.4, f"rug in progress should be <0.4, got {s.composite}"

    def test_dead_coin_low(self) -> None:
        s = score_pair(fx.dead_coin(), contract_age_hours=60 * 24)
        assert s.composite < 0.3

    def test_honeypot_zeroes_out(self) -> None:
        s = score_pair(
            fx.honeypot_signal(),
            is_honeypot=True,
            mint_renounced=True,
            lp_locked=True,
            lp_lock_days_remaining=30,
            contract_age_hours=1,
        )
        assert s.components.rug_resistance == 0.0
        # Explanation should mention honeypot
        assert any("honeypot" in line for line in s.explanation)

    def test_explanation_includes_components(self) -> None:
        s = score_pair(fx.good_volume_steady(), mint_renounced=True, lp_locked=True)
        joined = "\n".join(s.explanation)
        assert "volume_quality" in joined
        assert "volatility_character" in joined
        assert "rug_resistance" in joined
