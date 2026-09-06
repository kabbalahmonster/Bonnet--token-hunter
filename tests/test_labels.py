"""Tests for the labels module."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bonnet.labels import (
    AUTO_GOOD_CHANGE_24H_MAX,
    AUTO_GOOD_CHANGE_24H_MIN,
    AUTO_GOOD_VOLUME_24H_USD,
    AUTO_RUG_PRICE_DROP_24H_PCT,
    VALID_LABELS,
    Label,
    LabelStore,
    auto_label_from_pair,
    import_labels_from_csv,
    import_labels_from_json,
)


class TestLabel:
    def test_valid_label_accepted(self) -> None:
        for lbl in VALID_LABELS:
            Label(address="0x" + "a" * 40, label=lbl)

    def test_invalid_label_rejected(self) -> None:
        with pytest.raises(ValueError, match="label must be one of"):
            Label(address="0x" + "a" * 40, label="neutral")

    def test_address_lowercased(self) -> None:
        lbl = Label(address="0x" + "A" * 40, label="good")
        assert lbl.address == "0x" + "a" * 40


class TestLabelStore:
    def test_load_empty(self, tmp_path: Path) -> None:
        store = LabelStore.load(tmp_path / "labels.json")
        assert store.all() == []

    def test_save_and_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.json"
        store = LabelStore.load(path)
        store.add(Label(address="0x" + "a" * 40, label="good", symbol="GOOD"))
        store.add(Label(address="0x" + "b" * 40, label="rug", symbol="RUG"))
        store.save()

        store2 = LabelStore.load(path)
        assert len(store2.all()) == 2
        assert store2.get("0x" + "a" * 40).symbol == "GOOD"
        assert store2.get("0x" + "b" * 40).label == "rug"

    def test_add_returns_new_vs_existing(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.json"
        store = LabelStore.load(path)
        # First label: manual "good"
        assert store.add(Label(address="0x" + "a" * 40, label="good", source="manual")) is True
        # Second label same address, but auto source — manual should win
        assert store.add(Label(address="0x" + "a" * 40, label="rug", source="auto")) is False
        # Original manual label still wins
        store.save()
        store2 = LabelStore.load(path)
        assert store2.get("0x" + "a" * 40).label == "good"

    def test_remove(self, tmp_path: Path) -> None:
        store = LabelStore.load(tmp_path / "labels.json")
        store.add(Label(address="0x" + "a" * 40, label="good"))
        assert store.remove("0x" + "a" * 40) is True
        assert store.remove("0x" + "a" * 40) is False  # already gone

    def test_filter(self, tmp_path: Path) -> None:
        store = LabelStore.load(tmp_path / "labels.json")
        store.add(Label(address="0x" + "a" * 40, label="good"))
        store.add(Label(address="0x" + "b" * 40, label="rug"))
        store.add(Label(address="0x" + "c" * 40, label="moon"))
        assert len(store.filter("good")) == 1
        assert len(store.filter("rug")) == 1
        assert len(store.filter("moon")) == 1
        assert len(store.all()) == 3

    def test_save_writes_atomic(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.json"
        store = LabelStore.load(path)
        store.add(Label(address="0x" + "a" * 40, label="good"))
        store.save()
        # No .tmp file should remain
        assert not (tmp_path / "labels.json.tmp").exists()

    def test_manual_wins_over_auto(self, tmp_path: Path) -> None:
        store = LabelStore.load(tmp_path / "labels.json")
        store.add(Label(address="0x" + "a" * 40, label="good", source="manual"))
        # Try to overwrite with auto label — should be rejected
        result = store.add(Label(
            address="0x" + "a" * 40,
            label="rug",
            source="auto",
        ))
        assert result is False
        assert store.get("0x" + "a" * 40).label == "good"


class TestLabelImport:
    def test_csv_import(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "labels.csv"
        csv_path.write_text(
            "address,label,symbol,notes\n"
            "0x" + "a" * 40 + ",good,GOOD,test\n"
            "0x" + "b" * 40 + ",rug,RUG,scammed\n"
        )
        labels = import_labels_from_csv(csv_path)
        assert len(labels) == 2
        assert labels[0].symbol == "GOOD"
        assert labels[1].label == "rug"
        assert all(lbl.source == "import" for lbl in labels)

    def test_csv_import_skips_invalid_rows(self, tmp_path: Path) -> None:
        csv_path = tmp_path / "labels.csv"
        csv_path.write_text(
            "address,label,symbol,notes\n"
            "0x" + "a" * 40 + ",good,GOOD\n"
            "0x" + "b" * 40 + ",INVALID_LABEL,BAD\n"
            "0xinvalid,good,X\n"  # bad address
        )
        labels = import_labels_from_csv(csv_path)
        # Only the first row is valid
        assert len(labels) == 1
        assert labels[0].label == "good"

    def test_json_import_array(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.json"
        path.write_text(json.dumps([
            {"address": "0x" + "a" * 40, "label": "good", "symbol": "GOOD"},
            {"address": "0x" + "b" * 40, "label": "rug", "symbol": "RUG"},
        ]))
        labels = import_labels_from_json(path)
        assert len(labels) == 2
        assert all(lbl.source == "import" for lbl in labels)

    def test_json_import_envelope(self, tmp_path: Path) -> None:
        path = tmp_path / "labels.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "labels": [
                {"address": "0x" + "a" * 40, "label": "good"},
            ],
        }))
        labels = import_labels_from_json(path)
        assert len(labels) == 1


class TestAutoLabel:
    def test_rug_label_on_big_drop(self) -> None:
        lbl = auto_label_from_pair("X", "0x" + "a" * 40, change_24h=-90.0, vol_24h=10_000.0)
        assert lbl is not None
        assert lbl.label == "rug"
        assert lbl.confidence < 1.0  # auto-labeled

    def test_no_label_when_drop_above_threshold(self) -> None:
        # -50% isn't enough to flag as rug
        lbl = auto_label_from_pair("X", "0x" + "a" * 40, change_24h=-50.0, vol_24h=10_000.0)
        assert lbl is None

    def test_good_label_on_healthy_token(self) -> None:
        lbl = auto_label_from_pair(
            "X", "0x" + "a" * 40, change_24h=15.0,
            vol_24h=AUTO_GOOD_VOLUME_24H_USD + 1000,
        )
        assert lbl is not None
        assert lbl.label == "good"

    def test_no_good_label_when_volume_too_low(self) -> None:
        lbl = auto_label_from_pair(
            "X", "0x" + "a" * 40, change_24h=15.0,
            vol_24h=AUTO_GOOD_VOLUME_24H_USD / 2,
        )
        assert lbl is None

    def test_thresholds_are_reasonable(self) -> None:
        # Sanity checks on the heuristic constants
        assert AUTO_RUG_PRICE_DROP_24H_PCT < -50.0
        assert AUTO_GOOD_VOLUME_24H_USD > 1000
        assert AUTO_GOOD_CHANGE_24H_MIN < 0 < AUTO_GOOD_CHANGE_24H_MAX
