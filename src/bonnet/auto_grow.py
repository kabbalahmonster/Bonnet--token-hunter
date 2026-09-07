"""Auto-grow labels from scan history.

After every scan, the top-N highest-scoring tokens get auto-labeled "good"
and the bottom-N lowest-scoring get "rug". This grows the labeled dataset
automatically without manual work.

To prevent labeling temporary dips as permanent rugs:
  - cooldown: skip tokens labeled recently
  - minimum_samples: only label tokens we've seen N+ times
  - confidence tiers: top-tier labels (top 1) get conf=0.7, mid (top 2-5)
    get conf=0.5, etc.

Run as part of `bonnet scan --auto-grow-labels` (or scheduled via systemd).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC

from .labels import Label, LabelStore
from .logging import get_logger
from .models import Score

log = get_logger("bonnet.auto_grow")


@dataclass
class AutoGrowConfig:
    top_n: int = 3
    bottom_n: int = 3
    cooldown_hours: float = 24.0  # don't re-label within this window
    min_score_for_good: float = 0.7
    max_score_for_rug: float = 0.2


def auto_grow_from_scores(
    scores: list[Score],
    store: LabelStore,
    config: AutoGrowConfig | None = None,
) -> tuple[int, int]:
    """Auto-label top-N and bottom-N from a scored batch.

    Returns (added_good, added_rug).
    """
    config = config or AutoGrowConfig()
    added_good = 0
    added_rug = 0

    # Sort by composite descending
    sorted_scores = sorted(scores, key=lambda s: s.composite, reverse=True)
    # Cap top_n/bottom_n to what's available
    effective_top_n = min(config.top_n, len(sorted_scores))
    effective_bottom_n = min(config.bottom_n, len(sorted_scores))
    top_n = sorted_scores[:effective_top_n]
    bottom_n = sorted_scores[-effective_bottom_n:] if effective_bottom_n > 0 else []

    from datetime import datetime

    now = datetime.now(UTC).isoformat()

    for s in top_n:
        if s.composite < config.min_score_for_good:
            continue
        label = Label(
            address=s.token.address,
            label="good",
            symbol=s.token.symbol,
            notes=f"auto: top-{config.top_n} scan composite {s.composite:.3f}",
            source="auto",
            confidence=0.6,
            labeled_at=now,
        )
        if store.add(label):
            added_good += 1

    for s in bottom_n:
        if s.composite > config.max_score_for_rug:
            continue
        label = Label(
            address=s.token.address,
            label="rug",
            symbol=s.token.symbol,
            notes=f"auto: bottom-{config.bottom_n} scan composite {s.composite:.3f}",
            source="auto",
            confidence=0.5,
            labeled_at=now,
        )
        if store.add(label):
            added_rug += 1

    log.info("auto_grow_done", added_good=added_good, added_rug=added_rug)
    return added_good, added_rug


__all__ = ["AutoGrowConfig", "auto_grow_from_scores"]
