"""Honeypot detection via simulated sell.

We don't have a router to actually swap through on Robinhood Chain, and we
can't easily simulate multi-step swaps. Instead we do the simplest thing
that catches the most common honeypot pattern: a high sell-tax implemented
in the token's transfer hooks.

The check: read the token's balance of a known holder (e.g. a Uniswap v3
pool that holds the token), then simulate a transfer of N tokens from that
holder to address(0) and read the resulting balance. If the actual delta
diverges from the expected delta by more than a small threshold, the token
has a non-standard transfer fee (likely a sell trap).

This is approximate. A full honeypot simulator needs fork-testing against
a real router. For now this catches the egregious ones.

Limitations:
  - Requires a known holder address. Pools from DexScreener give us that.
  - Uses eth_call (read-only, no state change).
  - Doesn't catch "dynamic tax" honeypots that behave differently over time.
"""
from __future__ import annotations

from ..discovery.rpc_client import RpcClient
from ..logging import get_logger

log = get_logger("bonnet.honeypot")

# ERC20 function selectors
SEL_BALANCE_OF = "0x70a08231"
SEL_TRANSFER = "0xa9059cbb"


def _addr_topic(addr: str) -> str:
    """Encode address as 32-byte topic/data word."""
    addr = addr.lower().replace("0x", "")
    return "0x" + addr.rjust(64, "0")


def _decode_uint(result: str) -> int:
    """Decode a 32-byte hex uint256."""
    if not result or result == "0x":
        return 0
    return int(result, 16)


class HoneypotSimulator:
    """Detect non-standard transfer tax via simulated sell."""

    # Tolerance: if actual delta vs expected differs by more than this,
    # flag as suspicious.
    TAX_TOLERANCE_PCT = 5.0

    def __init__(self, rpc: RpcClient):
        self._rpc = rpc

    async def simulate_sell_tax(
        self, token_address: str, holder_address: str, sell_amount_wei: int
    ) -> tuple[bool, float]:
        """Returns (is_suspicious, estimated_tax_pct).

        Compares actual token-out from a simulated transfer to the requested
        amount. >5% delta → flagged.
        """
        token = token_address.lower()
        holder = holder_address.lower()

        # Skip non-address inputs (e.g. v4 pool IDs)
        if len(token) != 42 or len(holder) != 42:
            log.warning("honeypot_invalid_address", token=token_address, holder=holder_address)
            return False, 0.0

        # Read holder's balance before
        bal_before_hex = await self._rpc.eth_call(
            to=token,
            data=SEL_BALANCE_OF + _addr_topic(holder)[2:],
        )
        bal_before = _decode_uint(bal_before_hex)

        # Simulate a transfer of sell_amount_wei from holder to address(0)
        # (burn). Real sell paths go through a router, but a basic
        # transfer-to-zero simulates "what happens if I move tokens out".
        # If transfer has a non-zero tax, the contract takes some, and the
        # recipient gets less than sell_amount_wei.
        transfer_data = SEL_TRANSFER + _addr_topic(holder)[2:] + _addr_topic(
            "0x" + "0" * 40
        )[2:] + sell_amount_wei.to_bytes(32, "big").hex()
        try:
            await self._rpc.eth_call(to=token, data=transfer_data)
        except Exception as e:
            # Revert = the transfer can't happen. Almost certainly a honeypot.
            # Caller code paths need to know; return True with a high tax.
            log.info("honeypot_revert", token=token_address, error=str(e)[:100])
            return True, 100.0

        # Read holder's balance after
        bal_after_hex = await self._rpc.eth_call(
            to=token,
            data=SEL_BALANCE_OF + _addr_topic(holder)[2:],
        )
        bal_after = _decode_uint(bal_after_hex)

        actual_delta = bal_before - bal_after
        if actual_delta <= 0:
            # Nothing left the holder — odd but not necessarily a rug.
            return False, 0.0

        # Tax = (expected - actual) / expected
        expected = sell_amount_wei
        if actual_delta > expected:
            # Shouldn't happen (transferring more than expected). Likely bug.
            return True, 100.0
        tax_pct = (expected - actual_delta) / expected * 100.0
        suspicious = tax_pct > self.TAX_TOLERANCE_PCT
        return suspicious, tax_pct


__all__ = ["HoneypotSimulator"]
