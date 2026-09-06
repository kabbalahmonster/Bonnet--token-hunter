"""Backtest mode — validate scoring weights against historical labels.

Given a set of labeled tokens (good vs rug), score them as if they were
newly discovered and report how well the weights separate them. Useful for
tuning `DEFAULT_WEIGHTS` in scoring/scorer.py.

Reads from `data/labels.json` (LabelStore) by default — that's where
`bonnet label`, `bonnet label-import`, and `bonnet label-auto` write.

Run:
  bonnet backtest
  bonnet backtest --threshold 0.7
  bonnet backtest --labels-file custom.json --weight-volume 0.4
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .discovery.dexscreener import DexScreenerClient
from .labels import Label, LabelStore
from .logging import get_logger
from .models import Pair
from .onchain.enricher import ContractEnricher
from .onchain.holders import HolderAnalyzer
from .onchain.honeypot import HoneypotSimulator
from .onchain.lp_lock import LPLockDetector
from .pipeline import score_one_pair
from .scoring.scorer import DEFAULT_WEIGHTS

log = get_logger("bonnet.backtest")


@dataclass
class BacktestResult:
    label: str
    address: str
    symbol: str
    composite: float
    volume_quality: float
    volatility_character: float
    rug_resistance: float
    predicted_class: str  # "above_threshold" | "below_threshold"
    notes: str = ""


@dataclass
class BacktestSummary:
    results: list[BacktestResult]
    threshold: float
    weights: dict[str, float]
    confusion: dict[str, dict[str, int]]  # confusion[true_label][predicted_class] = count
    mean_score_by_label: dict[str, float]
    rug_recall: float  # fraction of rugs flagged (above threshold)
    good_precision: float  # fraction of flagged tokens that are good/moon


def _load_labels(path: Path) -> list[Label]:
    """Load labels from a LabelStore JSON file."""
    store = LabelStore.load(path)
    return store.all()


async def run_backtest(
    labels_path: Path,
    *,
    settings,
    threshold: float = 0.65,
    weights: dict[str, float] | None = None,
) -> BacktestSummary:
    """Score every labeled token and compute confusion matrix."""
    weights = weights or DEFAULT_WEIGHTS
    labeled = _load_labels(labels_path)
    log.info("backtest_start", count=len(labeled), threshold=threshold)

    results: list[BacktestResult] = []

    async with DexScreenerClient() as dex:
        # Look up pairs for each labeled token via DexScreener
        for lt in labeled:
            try:
                pairs = await dex.token_pairs(lt.address)
            except Exception:
                pairs = []
            if not pairs:
                # Fallback: search by partial address (works when token isn't
                # in the per-address index but is in the global search index)
                try:
                    pairs = await dex.search(lt.symbol or lt.address[:10])
                except Exception as e:
                    log.warning("backtest_search_failed", address=lt.address[:10], error=str(e))
                    continue
                # Filter to the labeled address
                addr = lt.address.lower()
                pairs = [p for p in pairs if p.token.address.lower() == addr]
            if not pairs:
                log.warning("backtest_no_pair", address=lt.address[:10])
                continue
            # Pick the highest-liquidity pair
            best: Pair = max(pairs, key=lambda p: p.liquidity_usd)

            score = await score_one_pair(
                best,
                enricher=ContractEnricher(_rpc_for_backtest(settings)),
                honeypot=HoneypotSimulator(_rpc_for_backtest(settings)),
                holder_analyzer=HolderAnalyzer(_rpc_for_backtest(settings)),
                lp_detector=LPLockDetector(_rpc_for_backtest(settings)),
            )

            # Apply custom weights if provided
            if weights != DEFAULT_WEIGHTS:
                custom_composite = (
                    weights.get("volume", 0.30) * score.components.volume_quality
                    + weights.get("volatility", 0.30) * score.components.volatility_character
                    + weights.get("rug", 0.40) * score.components.rug_resistance
                )
                score_composite = custom_composite
            else:
                score_composite = score.composite

            predicted = "above_threshold" if score_composite >= threshold else "below_threshold"
            results.append(BacktestResult(
                label=lt.label,
                address=lt.address,
                symbol=lt.symbol,
                composite=score_composite,
                volume_quality=score.components.volume_quality,
                volatility_character=score.components.volatility_character,
                rug_resistance=score.components.rug_resistance,
                predicted_class=predicted,
                notes=lt.notes,
            ))

    # Build confusion matrix
    labels_set = sorted({r.label for r in results})
    confusion = {lbl: {"above_threshold": 0, "below_threshold": 0} for lbl in labels_set}
    for r in results:
        confusion[r.label][r.predicted_class] += 1

    # Mean score per label
    by_label: dict[str, list[float]] = {lbl: [] for lbl in labels_set}
    for r in results:
        by_label[r.label].append(r.composite)
    mean_score = {lbl: (sum(v) / len(v) if v else 0.0) for lbl, v in by_label.items()}

    # Recall (rugs flagged): fraction of rugs above threshold
    n_rugs = sum(confusion.get("rug", {}).values())
    rugs_flagged = confusion.get("rug", {}).get("above_threshold", 0)
    rug_recall = (rugs_flagged / n_rugs) if n_rugs else 0.0

    # Precision (flagged tokens are good/moon)
    flagged_total = sum(confusion[lbl]["above_threshold"] for lbl in labels_set)
    flagged_good = (
        confusion.get("good", {}).get("above_threshold", 0)
        + confusion.get("moon", {}).get("above_threshold", 0)
    )
    good_precision = (flagged_good / flagged_total) if flagged_total else 0.0

    return BacktestSummary(
        results=results,
        threshold=threshold,
        weights=weights,
        confusion=confusion,
        mean_score_by_label=mean_score,
        rug_recall=rug_recall,
        good_precision=good_precision,
    )


def _rpc_for_backtest(settings):
    """Lazy import to avoid top-level cycle."""
    from .discovery.rpc_client import RpcClient
    return RpcClient(settings)


def format_summary(summary: BacktestSummary) -> str:
    """Render a BacktestSummary as a human-readable report."""
    lines = [
        "=== Bonnet backtest ===",
        f"threshold: {summary.threshold:.2f}",
        f"weights:   {summary.weights}",
        "",
        "Confusion matrix (rows = true label, cols = predicted class):",
        f"  {'label':<10} {'flagged':>10} {'skipped':>10}  total",
    ]
    for label, counts in summary.confusion.items():
        flagged = counts["above_threshold"]
        skipped = counts["below_threshold"]
        lines.append(f"  {label:<10} {flagged:>10} {skipped:>10}  {flagged + skipped}")
    lines.extend([
        "",
        "Mean composite score by label:",
    ])
    for label, mean in summary.mean_score_by_label.items():
        lines.append(f"  {label:<10} {mean:.3f}")
    lines.extend([
        "",
        f"Rug recall:    {summary.rug_recall:.0%} (fraction of rugs flagged)",
        f"Good precision: {summary.good_precision:.0%} (fraction of flagged tokens that are good/moon)",
        "",
        "Per-token scores:",
        f"  {'LABEL':<8} {'SYMBOL':<10} {'COMP':>6} {'VOL':>5} {'VOLAT':>5} {'RUG':>5}  ADDR",
        "  " + "-" * 70,
    ])
    for r in summary.results:
        flag = "🚩" if r.predicted_class == "below_threshold" else "  "
        lines.append(
            f"  {flag} {r.label:<6} {r.symbol:<10} {r.composite:>6.3f} "
            f"{r.volume_quality:>5.2f} {r.volatility_character:>5.2f} "
            f"{r.rug_resistance:>5.2f}  {r.address[:10]}…"
        )
    return "\n".join(lines)


__all__ = [
    "BacktestResult",
    "BacktestSummary",
    "format_summary",
    "run_backtest",
]
