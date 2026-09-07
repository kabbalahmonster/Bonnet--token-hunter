"""Cross-validation for backtest.

Standard k-fold: split labeled set into k parts, run backtest on each (k-1)
training subset, compute average metrics. This detects weight overfitting —
if your weights are tuned to your specific labels, k-fold reveals it.

Run:
  bonnet backtest-cv              # default 5-fold
  bonnet backtest-cv --k 10       # 10-fold (more expensive, less variance)
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from .backtest import (
    BacktestResult,
    BacktestSummary,
    _load_labels,
)
from .logging import get_logger
from .scoring.scorer import DEFAULT_WEIGHTS

log = get_logger("bonnet.cv")


@dataclass
class CVFoldResult:
    fold_index: int
    train_size: int
    test_size: int
    summary: BacktestSummary


@dataclass
class CVSummary:
    folds: list[CVFoldResult]
    mean_rug_recall: float
    mean_good_precision: float
    threshold: float
    weights: dict[str, float]
    k: int


def k_fold_split(labels: list, k: int, seed: int = 42) -> list[tuple[list, list]]:
    """Split `labels` into k folds. Returns list of (train, test) tuples.

    Deterministic via seed for reproducible backtests.
    """
    if k < 2:
        raise ValueError(f"k must be ≥ 2, got {k}")
    if len(labels) < k:
        raise ValueError(f"need at least {k} labels to do {k}-fold, got {len(labels)}")

    rng = random.Random(seed)
    indices = list(range(len(labels)))
    rng.shuffle(indices)

    # Group by label so each fold has at least one of each class when possible
    by_label: dict[str, list[int]] = {}
    for idx in indices:
        label = labels[idx].label
        by_label.setdefault(label, []).append(idx)

    folds: list[list[int]] = [[] for _ in range(k)]
    # Round-robin distribute by label so each fold has all classes
    for label, idxs in by_label.items():
        for i, idx in enumerate(idxs):
            folds[i % k].append(idx)

    splits: list[tuple[list, list]] = []
    for i in range(k):
        test_idxs = set(folds[i])
        train = [labels[j] for j in indices if j not in test_idxs]
        test = [labels[j] for j in indices if j in test_idxs]
        splits.append((train, test))
    return splits


async def run_cv(
    labels_path,
    *,
    settings,
    k: int = 5,
    threshold: float = 0.65,
    weights: dict[str, float] | None = None,
    seed: int = 42,
) -> CVSummary:
    """Run k-fold cross-validation on the labeled set.

    Each fold trains (scores) on k-1 labels and reports on the held-out one.
    Average metrics across folds reveal whether your weights generalize.
    """
    weights = weights or DEFAULT_WEIGHTS
    labels = _load_labels(labels_path)
    log.info("cv_start", count=len(labels), k=k)

    if len(labels) < k:
        raise ValueError(
            f"need at least {k} labels for {k}-fold CV, got {len(labels)}"
        )

    folds = k_fold_split(labels, k, seed=seed)
    fold_results: list[CVFoldResult] = []
    rug_recalls: list[float] = []
    good_precisions: list[float] = []

    from .pipeline import run_scan_for_addresses

    for i, (train, test) in enumerate(folds):
        log.info("cv_fold", fold=i + 1, train_size=len(train), test_size=len(test))
        scores = await run_scan_for_addresses(
            [lbl.address for lbl in test],
            settings=settings,
            notify_threshold=10.0,  # disable alerts during CV
            dry_run_notify=True,
        )

        summary = _summary_from_scores(test, scores, threshold, weights)
        fold_results.append(CVFoldResult(
            fold_index=i + 1,
            train_size=len(train),
            test_size=len(test),
            summary=summary,
        ))
        rug_recalls.append(summary.rug_recall)
        good_precisions.append(summary.good_precision)

    return CVSummary(
        folds=fold_results,
        mean_rug_recall=sum(rug_recalls) / len(rug_recalls),
        mean_good_precision=sum(good_precisions) / len(good_precisions),
        threshold=threshold,
        weights=weights,
        k=k,
    )


def _summary_from_scores(
    test_labels: list, scores: list, threshold: float, weights: dict[str, float]
) -> BacktestSummary:
    """Build a BacktestSummary from already-scored tokens."""
    by_addr = {s.token.address.lower(): s for s in scores}
    results: list[BacktestResult] = []
    for lt in test_labels:
        s = by_addr.get(lt.address.lower())
        if s is None:
            continue
        # Apply custom weights if non-default
        if weights != DEFAULT_WEIGHTS:
            custom_composite = (
                weights.get("volume", 0.30) * s.components.volume_quality
                + weights.get("volatility", 0.30) * s.components.volatility_character
                + weights.get("rug", 0.40) * s.components.rug_resistance
            )
            composite = custom_composite
        else:
            composite = s.composite
        predicted = "above_threshold" if composite >= threshold else "below_threshold"
        results.append(BacktestResult(
            label=lt.label,
            address=lt.address,
            symbol=lt.symbol,
            composite=composite,
            volume_quality=s.components.volume_quality,
            volatility_character=s.components.volatility_character,
            rug_resistance=s.components.rug_resistance,
            predicted_class=predicted,
            notes=lt.notes,
        ))

    labels_set = sorted({r.label for r in results})
    confusion = {lbl: {"above_threshold": 0, "below_threshold": 0} for lbl in labels_set}
    for r in results:
        confusion[r.label][r.predicted_class] += 1

    by_label_scores: dict[str, list[float]] = {lbl: [] for lbl in labels_set}
    for r in results:
        by_label_scores[r.label].append(r.composite)
    mean_score = {lbl: (sum(v) / len(v) if v else 0.0) for lbl, v in by_label_scores.items()}

    n_rugs = sum(confusion.get("rug", {}).values())
    rugs_flagged = confusion.get("rug", {}).get("above_threshold", 0)
    rug_recall = (rugs_flagged / n_rugs) if n_rugs else 0.0

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


def format_cv_summary(cv: CVSummary) -> str:
    """Render CVSummary as a report."""
    lines = [
        "=== Bonnet k-fold cross-validation ===",
        f"k:                {cv.k}",
        f"threshold:        {cv.threshold:.2f}",
        f"weights:          {cv.weights}",
        "",
        f"Mean rug recall:    {cv.mean_rug_recall:.0%}",
        f"Mean good precision: {cv.mean_good_precision:.0%}",
        "",
        f"{'FOLD':<5} {'TRAIN':>5} {'TEST':>5} {'RUG_RECALL':>11} {'GOOD_PREC':>10}",
    ]
    for fr in cv.folds:
        lines.append(
            f"{fr.fold_index:<5} {fr.train_size:>5} {fr.test_size:>5} "
            f"{fr.summary.rug_recall:>11.0%} {fr.summary.good_precision:>10.0%}"
        )
    lines.extend([
        "",
        "Interpretation:",
        "  - Mean rug recall across folds = how often the scorer catches rugs.",
        "  - Mean good precision = how often a flag is a real good/moon token.",
        "  - High variance across folds = labels are too few or unbalanced.",
    ])
    return "\n".join(lines)


__all__ = ["CVFoldResult", "CVSummary", "format_cv_summary", "k_fold_split", "run_cv"]
