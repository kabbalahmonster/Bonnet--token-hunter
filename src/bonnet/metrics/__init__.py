"""Volume and volatility scoring.

All functions return 0.0..1.0 (higher = better candidate).

Key idea: we want *sustained* volume, not spike-and-fade. We also want
*meaningful* volatility — not zero (dead) and not one-way (rug).
"""
from __future__ import annotations

import math

from ..models import Pair


def volume_quality(p: Pair) -> tuple[float, list[str]]:
    """Reward sustained volume; penalize 24h-spike-over-1h ratio (wash-ish)."""
    notes: list[str] = []

    v24 = p.volume_usd_24h
    if v24 < 1000:
        return 0.0, [f"24h volume too low (${v24:,.0f})"]

    # 1. Baseline log scale: $1k→0.0, $1M→0.5, $10M+→1.0
    baseline = min(1.0, max(0.0, (math.log10(max(v24, 1)) - 3.0) / 2.0))

    # 2. Sustainedness: ratio of 6h to 24h (if 6h >> 24h/4, healthy).
    # Expected: 6h ≈ 24h/4 if volume is uniform. Allow some variance.
    if v24 > 0 and p.volume_usd_6h > 0:
        six_to_24_ratio = (p.volume_usd_6h * 4) / v24
        # Centered at 1.0, half-credit at 0.5 or 1.5, zero at 0/2+
        if six_to_24_ratio < 0.25 or six_to_24_ratio > 3.0:
            sustainedness = 0.2
            notes.append(f"volume not sustained (6h/24h ratio {six_to_24_ratio:.2f})")
        else:
            sustainedness = 1.0 - min(1.0, abs(1.0 - six_to_24_ratio) / 1.0) * 0.5
    else:
        sustainedness = 0.5  # unknown
        notes.append("6h volume missing — sustainedness unknown")

    # 3. Liquidity ratio: volume / liquidity. Too high = easy to move price.
    liq_usd = p.liquidity_usd
    if liq_usd > 0:
        vol_to_liq = v24 / liq_usd
        if vol_to_liq > 5.0:
            liquidity_penalty = 0.5
            notes.append(f"vol/liquidity ratio {vol_to_liq:.1f} (thin book)")
        elif vol_to_liq > 2.0:
            liquidity_penalty = 0.2
        else:
            liquidity_penalty = 0.0
    else:
        liquidity_penalty = 0.4
        notes.append("no liquidity reported")

    score = max(0.0, min(1.0, baseline * sustainedness - liquidity_penalty))
    return score, notes


def volatility_character(p: Pair) -> tuple[float, list[str]]:
    """We want *active* price action with bounded downside.

    Penalize:
      - Zero volatility (dead)
      - One-way downside (rug-in-progress)
      - Extreme single-bar moves (pump-and-dump signature)

    Reward:
      - Reasonable 1h and 24h absolute swings
      - Mixed buy/sell txn ratio (healthy two-sided action)
    """
    notes: list[str] = []

    abs_24 = abs(p.price_change_pct_24h)
    abs_1h = abs(p.price_change_pct_1h)

    if abs_24 < 1.0 and abs_1h < 0.5:
        return 0.0, ["too quiet — no volatility"]

    # 24h swing score — bell curve peaking around 20-40% absolute move
    if abs_24 <= 0:
        s24 = 0.0
    elif abs_24 < 100.0:
        # Centered at 30%, full credit at 30%, half at 10 or 60, zero at 0 or 100+
        s24 = max(0.0, 1.0 - (abs(abs_24 - 30.0) / 30.0)) ** 0.5  # gentler falloff
    else:
        s24 = 0.0
        notes.append(f"abs 24h move {abs_24:.0f}% — looks like a pump/dump")

    # 1h swing score — smaller bell, peaks 3-8%
    if abs_1h <= 0:
        s1h = 0.0
    elif abs_1h < 30.0:
        s1h = max(0.0, 1.0 - (abs(abs_1h - 5.0) / 5.0)) ** 0.5
    else:
        s1h = 0.0
        notes.append(f"abs 1h move {abs_1h:.0f}% — extreme")

    # Direction penalty: heavy one-way downside
    direction_penalty = 0.0
    if p.price_change_pct_24h < -50.0:
        direction_penalty = 0.5
        notes.append(f"24h down {abs_24:.0f}% — possible rug in progress")

    # Txn balance — both buys and sells > 0 is healthy
    buys = p.txns_24h_buys
    sells = p.txns_24h_sells
    if buys + sells > 0:
        ratio = buys / (buys + sells)
        balance = 1.0 - abs(ratio - 0.5) * 2.0  # 0.5 → 1.0, 0/1 → 0.0
    else:
        balance = 0.0
        notes.append("no txns in 24h")

    score = max(0.0, min(1.0, 0.45 * s24 + 0.30 * s1h + 0.25 * balance - direction_penalty))
    return score, notes


__all__ = ["volatility_character", "volume_quality"]
