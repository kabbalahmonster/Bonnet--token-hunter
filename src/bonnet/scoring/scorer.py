"""Composite scorer.

Combines volume_quality, volatility_character, and rug resistance into a
final Score object with explainable components.
"""
from __future__ import annotations

from datetime import UTC, datetime

from ..metrics import volatility_character, volume_quality
from ..models import Pair, RugSignals, Score, ScoreComponents
from ..rug import aggregate as aggregate_rug

# Default weights — tuned so a "good" candidate lands ~0.65-0.80.
# Override per-environment by editing here, or by passing a ScoreConfig.
DEFAULT_WEIGHTS = {"volume": 0.30, "volatility": 0.30, "rug": 0.40}


def score_pair(
    pair: Pair,
    *,
    # Optional rug inputs; if not provided, we build an "unknown" RugSignals
    top10_holder_pct: float | None = None,
    top1_holder_pct: float | None = None,
    mint_renounced: bool | None = None,
    freeze_renounced: bool | None = None,
    owner_renounced: bool | None = None,
    lp_locked: bool | None = None,
    lp_lock_days_remaining: int | None = None,
    contract_age_hours: float | None = None,
    is_honeypot: bool | None = None,
    deployer_prior_rugs: int | None = None,
    deployer_prior_tokens: int | None = None,
    is_contract_verified: bool | None = None,
    has_proxy: bool = False,
    weights: dict[str, float] | None = None,
) -> Score:
    """Score a Pair end-to-end."""
    weights = weights or DEFAULT_WEIGHTS

    vol_score, vol_notes = volume_quality(pair)
    volat_score, volat_notes = volatility_character(pair)

    pair_age_h = None
    if pair.created_at:
        pair_age_h = max(0.0, (datetime.now(UTC) - pair.created_at).total_seconds() / 3600.0)

    rug_signals: RugSignals = aggregate_rug(
        top10_pct=top10_holder_pct,
        top1_pct=top1_holder_pct,
        mint_renounced=mint_renounced,
        freeze_renounced=freeze_renounced,
        owner_renounced=owner_renounced,
        lp_locked=lp_locked,
        lp_lock_days_remaining=lp_lock_days_remaining,
        contract_age_hours=contract_age_hours,
        pair_age_hours=pair_age_h,
        is_honeypot=is_honeypot,
        deployer_prior_tokens=deployer_prior_tokens,
        deployer_prior_rugs=deployer_prior_rugs,
        is_contract_verified=is_contract_verified,
        has_proxy=has_proxy,
    )

    # rug_resistance is the inverse of rug_risk.
    # Hard rule: a confirmed honeypot zeroes out.
    if rug_signals.is_honeypot is True:
        rug_res = 0.0
    else:
        # If we have NO rug signals at all (everything is None), default to a
        # pessimistic mid-score — not a generous near-1.0. Unknown ≠ safe.
        has_any_signal = any(
            x is not None
            for x in (
                rug_signals.top10_holder_pct,
                rug_signals.top1_holder_pct,
                rug_signals.mint_authority_renounced,
                rug_signals.freeze_authority_renounced,
                rug_signals.owner_renounced,
                rug_signals.lp_locked,
                rug_signals.is_contract_verified,
            )
        )
        rug_res = 0.5 if not has_any_signal else max(0.0, min(1.0, 1.0 - rug_signals.rug_risk))

    components = ScoreComponents(
        volume_quality=vol_score,
        volatility_character=volat_score,
        rug_resistance=rug_res,
    )

    composite = (
        weights.get("volume", 0.30) * components.volume_quality
        + weights.get("volatility", 0.30) * components.volatility_character
        + weights.get("rug", 0.40) * components.rug_resistance
    )

    explanation = [
        f"volume_quality: {components.volume_quality:.2f}",
        *(f"  vol: {n}" for n in vol_notes),
        f"volatility_character: {components.volatility_character:.2f}",
        *(f"  volat: {n}" for n in volat_notes),
        f"rug_resistance: {components.rug_resistance:.2f} (rug_risk={rug_signals.rug_risk:.2f})",
        *(f"  rug: {n}" for n in rug_signals.notes),
    ]

    return Score(
        token=pair.token,
        pair=pair,
        components=components,
        rug_signals=rug_signals,
        composite=max(0.0, min(1.0, composite)),
        scored_at=datetime.now(UTC),
        explanation=explanation,
    )


__all__ = ["DEFAULT_WEIGHTS", "score_pair"]
