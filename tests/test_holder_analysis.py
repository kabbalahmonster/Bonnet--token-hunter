"""Tests for HolderAnalyzer + LPLockDetector (no live RPC — log/heuristic logic only)."""
from __future__ import annotations

from bonnet.onchain.holders import _addr_from_topic, _decode_uint_from_word
from bonnet.onchain.lp_lock import _addr_topic as lp_addr_topic


class TestAddrFromTopic:
    def test_extracts_address_correctly(self) -> None:
        # 32-byte zero-padded address
        padded = "0x" + "0" * 24 + "ab" * 20
        assert _addr_from_topic(padded) == "0x" + "ab" * 20

    def test_lowercases(self) -> None:
        padded = "0x" + "0" * 24 + "AB" * 20
        assert _addr_from_topic(padded) == "0x" + "ab" * 20

    def test_handles_partial_topic(self) -> None:
        # 64-hex string
        padded = "0x" + "f" * 64
        assert _addr_from_topic(padded) == "0x" + "f" * 40


class TestDecodeUintFromWord:
    def test_decodes_at_offset_zero(self) -> None:
        data = "0x" + "00" * 31 + "0a"  # 10
        assert _decode_uint_from_word(data, 0) == 10

    def test_decodes_at_offset_one(self) -> None:
        # first word 1 (32 bytes), second word 255 (32 bytes)
        data = "0x" + "00" * 31 + "01" + "00" * 31 + "ff"
        assert _decode_uint_from_word(data, 0) == 1
        assert _decode_uint_from_word(data, 1) == 255

    def test_handles_empty_data(self) -> None:
        assert _decode_uint_from_word("", 0) == 0
        assert _decode_uint_from_word("0x", 0) == 0


class TestLpAddrTopic:
    def test_padding(self) -> None:
        addr = "0x" + "a" * 40
        topic = lp_addr_topic(addr)
        assert len(topic) == 66
        assert topic.endswith("a" * 40)
        assert topic.startswith("0x" + "0" * 24)

    def test_already_lowercase(self) -> None:
        # 40 hex chars total after 0x — 6 random + 34 zeros
        addr = "0xABCDEF" + "0" * 34
        topic = lp_addr_topic(addr)
        # function lowercases internally and pads to 32 bytes (64 hex chars)
        # 64 - 6 (hex content) - 34 (zeros) = 24 leading zeros
        assert topic == "0x" + "0" * 24 + "abcdef" + "0" * 34
