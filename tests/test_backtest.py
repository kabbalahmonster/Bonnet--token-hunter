"""Tests for backtest label loading + summary formatting (no live RPC)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bonnet.backtest import BacktestResult, BacktestSummary, _load_labels, format_summary
from bonnet.labels import Label


def test_load_labels_minimal(tmp_path: Path) -> None:
    p = tmp_path / "labels.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "labels": [
            {"address": "0x" + "a" * 40, "symbol": "A", "label": "good"},
            {"address": "0x" + "b" * 40, "symbol": "B", "label": "rug"},
        ],
    }))
    labels = _load_labels(p)
    assert len(labels) == 2
    assert isinstance(labels[0], Label)
    assert labels[0].symbol == "A"
    assert labels[1].label == "rug"


def test_load_labels_lowercases_address(tmp_path: Path) -> None:
    p = tmp_path / "labels.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "labels": [
            {"address": "0x" + "ABCDEF" + "0" * 34, "symbol": "X", "label": "moon"},
        ],
    }))
    labels = _load_labels(p)
    assert labels[0].address == "0x" + "abcdef" + "0" * 34


def test_load_labels_handles_missing_address(tmp_path: Path) -> None:
    """A label entry without 'address' raises (KeyError from dataclass)."""
    p = tmp_path / "labels.json"
    p.write_text(json.dumps({
        "schema_version": 1,
        "labels": [{"symbol": "A", "label": "good"}],
    }))
    with pytest.raises(KeyError):
        _load_labels(p)


def test_format_summary_basic() -> None:
    results = [
        BacktestResult(
            label="good", address="0x" + "a" * 40, symbol="GOOD",
            composite=0.7, volume_quality=0.8, volatility_character=0.7,
            rug_resistance=0.6, predicted_class="above_threshold",
        ),
        BacktestResult(
            label="rug", address="0x" + "b" * 40, symbol="RUG",
            composite=0.2, volume_quality=0.3, volatility_character=0.1,
            rug_resistance=0.3, predicted_class="below_threshold",
        ),
    ]
    summary = BacktestSummary(
        results=results,
        threshold=0.65,
        weights={"volume": 0.3, "volatility": 0.3, "rug": 0.4},
        confusion={
            "good": {"above_threshold": 1, "below_threshold": 0},
            "rug": {"above_threshold": 0, "below_threshold": 1},
        },
        mean_score_by_label={"good": 0.7, "rug": 0.2},
        rug_recall=0.0,
        good_precision=1.0,
    )
    out = format_summary(summary)
    assert "backtest" in out.lower()
    assert "GOOD" in out
    assert "RUG" in out
    assert "0.700" in out
    assert "Confusion" in out
    assert "Rug recall" in out
    assert "Good precision" in out
