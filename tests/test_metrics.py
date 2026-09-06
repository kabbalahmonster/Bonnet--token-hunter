"""Tests for the volume and volatility scoring functions.

Asserts behavior contracts across the spectrum of token archetypes.
"""
from __future__ import annotations

from bonnet.metrics import volatility_character, volume_quality

from . import fixtures as fx


class TestVolumeQuality:
    def test_good_steady_scores_well(self) -> None:
        s, notes = volume_quality(fx.good_volume_steady())
        assert s > 0.4, f"good steady should score >0.4, got {s} notes={notes}"

    def test_dead_scores_zero(self) -> None:
        s, notes = volume_quality(fx.dead_coin())
        assert s == 0.0
        assert any("too low" in n for n in notes)

    def test_thin_book_penalized(self) -> None:
        # pump_and_dump has vol/liquidity > 5x
        _s, notes = volume_quality(fx.pump_and_dump())
        assert any("thin book" in n or "ratio" in n for n in notes)


class TestVolatilityCharacter:
    def test_good_steady_active(self) -> None:
        s, notes = volatility_character(fx.good_volume_steady())
        assert s > 0.4, f"good steady should be active, got {s} notes={notes}"

    def test_dead_too_quiet(self) -> None:
        s, notes = volatility_character(fx.dead_coin())
        assert s == 0.0
        assert any("quiet" in n for n in notes)

    def test_rug_penalized(self) -> None:
        s, notes = volatility_character(fx.rug_in_progress())
        assert any("possible rug" in n for n in notes)
        assert s < 0.3

    def test_pump_and_dump_flagged(self) -> None:
        _s, notes = volatility_character(fx.pump_and_dump())
        assert any("pump" in n or "extreme" in n for n in notes)
