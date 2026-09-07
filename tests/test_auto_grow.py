"""Tests for auto-grow labels from scan results."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from bonnet.auto_grow import AutoGrowConfig, auto_grow_from_scores
from bonnet.labels import LabelStore
from bonnet.models import (
    Chain,
    Pair,
    RugSignals,
    Score,
    ScoreComponents,
    Token,
)


def _make_score(address: str, composite: float, symbol: str = "X") -> Score:
    return Score(
        token=Token(address=address, chain=Chain.ROBINHOOD, symbol=symbol),
        pair=Pair(
            pair_address="0x" + "a" * 40,
            chain=Chain.ROBINHOOD,
            token=Token(address=address, chain=Chain.ROBINHOOD, symbol=symbol),
        ),
        components=ScoreComponents(
            volume_quality=composite,
            volatility_character=composite,
            rug_resistance=composite,
        ),
        rug_signals=RugSignals(rug_risk=1 - composite),
        composite=composite,
        scored_at=datetime.now(UTC),
    )


class TestAutoGrow:
    def test_labels_top_and_bottom(self, tmp_path: Path) -> None:
        store = LabelStore.load(tmp_path / "labels.json")
        scores = [
            _make_score("0x" + "1" * 40, 0.9, "HIGH1"),
            _make_score("0x" + "2" * 40, 0.85, "HIGH2"),
            _make_score("0x" + "3" * 40, 0.5, "MID"),
            _make_score("0x" + "4" * 40, 0.1, "LOW1"),
            _make_score("0x" + "5" * 40, 0.05, "LOW2"),
        ]
        added_good, added_rug = auto_grow_from_scores(
            scores, store, AutoGrowConfig(top_n=2, bottom_n=2)
        )
        assert added_good == 2
        assert added_rug == 2

        labels = store.all()
        good = [lbl for lbl in labels if lbl.label == "good"]
        rug = [lbl for lbl in labels if lbl.label == "rug"]
        assert {lbl.symbol for lbl in good} == {"HIGH1", "HIGH2"}
        assert {lbl.symbol for lbl in rug} == {"LOW1", "LOW2"}

    def test_respects_min_score_threshold(self, tmp_path: Path) -> None:
        store = LabelStore.load(tmp_path / "labels.json")
        scores = [
            _make_score("0x" + "1" * 40, 0.6, "MID"),  # below 0.7 min_for_good
            _make_score("0x" + "2" * 40, 0.3, "MIDLOW"),  # above 0.2 max_for_rug
        ]
        added_good, added_rug = auto_grow_from_scores(scores, store)
        # MID not labeled good (below threshold)
        # MIDLOW not labeled rug (above threshold)
        assert added_good == 0
        assert added_rug == 0
        assert store.all() == []

    def test_rug_extreme_labeled(self, tmp_path: Path) -> None:
        store = LabelStore.load(tmp_path / "labels.json")
        scores = [_make_score("0x" + "1" * 40, 0.05, "DEAD")]
        added_good, added_rug = auto_grow_from_scores(scores, store)
        assert added_good == 0
        assert added_rug == 1
        lbl = store.all()[0]
        assert lbl.label == "rug"
        assert lbl.confidence == 0.5
        assert lbl.source == "auto"

    def test_manual_label_wins(self, tmp_path: Path) -> None:
        # Add a manual rug label first, then try to auto-label same address as good
        store = LabelStore.load(tmp_path / "labels.json")
        from bonnet.labels import Label as Lbl

        store.add(Lbl(address="0x" + "1" * 40, label="rug", source="manual", confidence=1.0))
        store.save()

        # Now try to auto-label same address as good (top of scan)
        scores = [_make_score("0x" + "1" * 40, 0.95)]
        added_good, _added_rug = auto_grow_from_scores(scores, store)
        # Manual rug should win; auto-good should be skipped
        assert added_good == 0
        existing = store.get("0x" + "1" * 40)
        assert existing is not None
        assert existing.label == "rug"
        assert existing.source == "manual"

    def test_empty_scores_does_nothing(self, tmp_path: Path) -> None:
        store = LabelStore.load(tmp_path / "labels.json")
        added_good, added_rug = auto_grow_from_scores([], store)
        assert added_good == 0
        assert added_rug == 0
        assert store.all() == []

    def test_default_thresholds(self, tmp_path: Path) -> None:
        cfg = AutoGrowConfig()
        assert cfg.top_n == 3
        assert cfg.bottom_n == 3
        assert cfg.min_score_for_good > 0.5
        assert cfg.max_score_for_rug < 0.3
