"""Tests for rug heuristics aggregation."""
from __future__ import annotations

from bonnet.rug import aggregate


class TestRugAggregation:
    def test_clean_signals_zero_risk(self) -> None:
        s = aggregate(
            top10_pct=20.0,
            top1_pct=5.0,
            mint_renounced=True,
            freeze_renounced=True,
            owner_renounced=True,
            lp_locked=True,
            lp_lock_days_remaining=180,
            contract_age_hours=24 * 30,
        )
        assert s.rug_risk == 0.0

    def test_honeypot_max_risk(self) -> None:
        s = aggregate(is_honeypot=True)
        assert s.rug_risk == 1.0

    def test_concentrated_holders_increases_risk(self) -> None:
        clean = aggregate(top10_pct=20.0, contract_age_hours=24 * 30)
        concentrated = aggregate(top10_pct=85.0, contract_age_hours=24 * 30)
        assert concentrated.rug_risk > clean.rug_risk

    def test_young_contract_penalized(self) -> None:
        old = aggregate(contract_age_hours=24 * 30)
        young = aggregate(contract_age_hours=2)
        assert young.rug_risk > old.rug_risk

    def test_unlocked_lp_penalized(self) -> None:
        locked = aggregate(lp_locked=True, lp_lock_days_remaining=180)
        unlocked = aggregate(lp_locked=False)
        assert unlocked.rug_risk > locked.rug_risk

    def test_notes_appear_when_problematic(self) -> None:
        s = aggregate(top10_pct=85.0, lp_locked=False)
        joined = " ".join(s.notes)
        assert "top10" in joined
        assert "LP not locked" in joined

    def test_risk_clamped_to_unit_interval(self) -> None:
        # Pile on everything bad — should not exceed 1.0
        s = aggregate(
            top10_pct=99.0,
            top1_pct=70.0,
            mint_renounced=False,
            freeze_renounced=False,
            owner_renounced=False,
            lp_locked=False,
            contract_age_hours=1,
            deployer_prior_rugs=10,
            deployer_prior_tokens=12,
            is_contract_verified=False,
            has_proxy=True,
        )
        assert 0.0 <= s.rug_risk <= 1.0
