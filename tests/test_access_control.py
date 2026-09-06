"""Tests for the role selector encoding and constants."""
from __future__ import annotations

from bonnet.onchain.enricher import (
    DEFAULT_ADMIN_ROLE,
    MINTER_ROLE,
    SEL_GET_ROLE_MEMBER_COUNT,
)


class TestAccessControlConstants:
    def test_minter_role_is_32_bytes(self) -> None:
        assert len(MINTER_ROLE) == 66  # 0x + 64 hex
        assert MINTER_ROLE.startswith("0x")

    def test_default_admin_role_is_32_bytes(self) -> None:
        assert len(DEFAULT_ADMIN_ROLE) == 66
        assert DEFAULT_ADMIN_ROLE.startswith("0x")

    def test_minter_and_admin_differ(self) -> None:
        assert MINTER_ROLE != DEFAULT_ADMIN_ROLE

    def test_get_role_member_count_selector_is_4_bytes(self) -> None:
        assert len(SEL_GET_ROLE_MEMBER_COUNT) == 10  # 0x + 8 hex = 4 bytes


class TestEncodeRoleCall:
    """Test the data encoding used in the enricher's role probe."""

    def test_role_call_data_is_36_bytes(self) -> None:
        # selector (4 bytes) + role (32 bytes) = 36 bytes total = 0x + 72 hex chars
        data = SEL_GET_ROLE_MEMBER_COUNT + MINTER_ROLE[2:].rjust(64, "0")
        assert len(data) == 74  # 0x + 72 hex chars

    def test_padded_role_does_not_truncate(self) -> None:
        # Ensure rjust actually pads when role hex is shorter than 64
        short_role_hex = "abcd"
        padded = short_role_hex.rjust(64, "0")
        assert len(padded) == 64
        assert padded == "0" * 60 + "abcd"
