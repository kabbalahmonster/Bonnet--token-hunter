"""Trend analysis: compare current score to historical to detect momentum.

Useful for catching tokens that are *rising* into the alert threshold, not
just those already above it. Many real moonshots spend days climbing up
through 0.5 → 0.6 → 0.7 → 0.8 — alerting only at 0.65 misses the early
accumulation. Trend-aware alerts fire when:
  - composite delta vs N samples ago is positive AND large (rising)
  - composite crosses the threshold for the first time (breakout)
"""
from __future__ import annotations

from dataclasses import dataclass

from .logging import get_logger
from .models import Score

log = get_logger("bonnet.trend")


@dataclass(frozen=True)
class Trend:
    """Computed trend signal for a token's score over recent samples."""

    direction: str  # "rising" | "falling" | "flat" | "unknown"
    delta: float  # composite - composite_at_lookback
    pct_change: float  # delta / composite_at_lookback * 100 (or 0 if zero)
    samples_used: int
    is_breakout: bool  # True if composite just crossed threshold upward
    is_breakdown: bool  # True if composite just crossed threshold downward
    notes: list[str]


def compute_trend(
    history: list[Score],
    *,
    threshold: float = 0.65,
    lookback: int = 5,
) -> Trend:
    """Compute trend from a chronological list of historical scores.

    Args:
      history: list of Score, oldest first
      threshold: alert threshold for breakout detection
      lookback: how many samples back to compare (default: 5)
    """
    notes: list[str] = []
    if len(history) < 2:
        return Trend(
            direction="unknown",
            delta=0.0,
            pct_change=0.0,
            samples_used=len(history),
            is_breakout=False,
            is_breakdown=False,
            notes=["insufficient history"],
        )

    current = history[-1].composite
    # Find the score `lookback` samples back, falling back to oldest if fewer
    ref_idx = max(0, len(history) - 1 - lookback)
    reference = history[ref_idx].composite
    delta = current - reference

    pct_change = (delta / reference * 100.0) if reference > 0 else 0.0

    # Direction with hysteresis: 2% of score range matters
    if delta > 0.05:
        direction = "rising"
    elif delta < -0.05:
        direction = "falling"
    else:
        direction = "flat"

    # Breakout: crossed threshold upward since reference
    is_breakout = reference < threshold <= current
    is_breakdown = reference >= threshold > current

    if is_breakout:
        notes.append(f"breakout: crossed {threshold:.2f} upward")
    if is_breakdown:
        notes.append(f"breakdown: crossed {threshold:.2f} downward")

    return Trend(
        direction=direction,
        delta=delta,
        pct_change=pct_change,
        samples_used=len(history),
        is_breakout=is_breakout,
        is_breakdown=is_breakdown,
        notes=notes,
    )


def should_alert_with_trend(
    score: Score,
    trend: Trend,
    *,
    threshold: float = 0.65,
    cooldown_hours: float = 6.0,
    hours_since_last_alert: float | None = None,
) -> tuple[bool, str]:
    """Decide whether to alert, taking trend into account.

    Returns (should_alert, reason). Reasons:
      - "above_threshold" — static threshold crossed
      - "breakout" — just crossed threshold upward (high priority)
      - "rising_fast" — delta > 0.10 even if below threshold
      - "cooldown" — above threshold but alerted recently
      - "below_threshold" — neither condition met
    """
    above = score.composite >= threshold
    in_cooldown = (
        hours_since_last_alert is not None
        and hours_since_last_alert < cooldown_hours
    )

    if trend.is_breakout and not in_cooldown:
        return True, "breakout"
    if above and not in_cooldown:
        return True, "above_threshold"
    if trend.delta > 0.10 and trend.pct_change > 25.0 and not in_cooldown:
        # Below threshold but rising fast — could be early accumulation
        return True, "rising_fast"
    if above and in_cooldown:
        return False, "cooldown"
    return False, "below_threshold"


__all__ = ["Trend", "compute_trend", "should_alert_with_trend"]
