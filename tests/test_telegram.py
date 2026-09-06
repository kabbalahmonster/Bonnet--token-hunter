"""Tests for the Telegram message formatter (no actual sends)."""
from __future__ import annotations

from bonnet.notify.telegram import TelegramNotifier
from bonnet.scoring.scorer import score_pair

from . import fixtures as fx


class TestTelegramFormat:
    def test_format_includes_symbol_and_score(self) -> None:
        pair = fx.good_volume_steady()
        score = score_pair(pair, mint_renounced=True, lp_locked=True)
        msg = TelegramNotifier.format_alert(score)
        assert "GOOD" in msg
        assert "Bonnet alert" in msg
        assert f"{score.composite:.3f}" in msg

    def test_format_includes_volume_and_change(self) -> None:
        pair = fx.pump_and_dump()
        score = score_pair(pair, mint_renounced=False)
        msg = TelegramNotifier.format_alert(score)
        assert "Vol 24h" in msg
        assert "Δ 24h" in msg
        assert "$" in msg  # dollar sign for volume

    def test_format_includes_rug_notes(self) -> None:
        pair = fx.rug_in_progress()
        score = score_pair(pair, mint_renounced=False, lp_locked=False, top10_holder_pct=70.0)
        msg = TelegramNotifier.format_alert(score)
        assert "Rug notes" in msg

    def test_format_does_not_crash_without_pair(self) -> None:
        # Manually build a Score with pair=None to test the fallback
        score = score_pair(fx.good_volume_steady())
        score.pair = None
        msg = TelegramNotifier.format_alert(score)
        assert "Bonnet alert" in msg
