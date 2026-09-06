"""Tests for the honeypot simulator (no live RPC)."""
from __future__ import annotations

from bonnet.onchain.honeypot import HoneypotSimulator


class TestHoneypotSimulatorLogic:
    """Unit-test the static helpers (no RPC)."""

    def test_addr_topic_pads_correctly(self) -> None:
        from bonnet.onchain.honeypot import _addr_topic

        addr = "0x" + "a" * 40
        topic = _addr_topic(addr)
        assert len(topic) == 66  # 0x + 64 hex
        assert topic.endswith("a" * 40)
        assert topic.startswith("0x" + "0" * 24)

    def test_decode_uint_handles_zero(self) -> None:
        from bonnet.onchain.honeypot import _decode_uint

        assert _decode_uint("0x") == 0
        assert _decode_uint("") == 0
        assert _decode_uint("0x" + "0" * 64) == 0

    def test_decode_uint_decodes_value(self) -> None:
        from bonnet.onchain.honeypot import _decode_uint

        val = 10**18
        encoded = hex(val)
        assert _decode_uint(encoded) == val

    def test_tax_tolerance_is_set(self) -> None:
        # HoneypotSimulator.TAX_TOLERANCE_PCT is defined on the class
        assert HoneypotSimulator.TAX_TOLERANCE_PCT > 0
        assert HoneypotSimulator.TAX_TOLERANCE_PCT < 100
