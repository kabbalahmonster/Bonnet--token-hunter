"""LP lock detection.

For a Uniswap v2-style pair, the pair contract itself IS the LP token
(receives transfers of both tokens, issues its own ERC20 to LPs). For v3,
LP positions are NFTs (ERC721), not fungible.

Detection approach:
  1. Get the pair's token0/token1 from the factory (or read from the pair
     contract via getReserves)
  2. Read the LP token's `totalSupply()` and check if the **dead address**
     (0x000000000000000000000000000000000000dead) holds a meaningful share
     — many lockers send to dead address for "permanent" locks
  3. Check if a known locker contract (e.g. Unicrypt, Team.Finance) holds
     a meaningful share. On Robinhood Chain we'd populate these as we
     identify them.
  4. Read the LP token's owner / getOwner and check if it's a known
     locker or a multisig.

If a meaningful share (≥ 50%) of LP tokens is held by a single non-pair,
non-router address and that address looks like a known locker or the dead
address, mark `lp_locked=True`.

Limitations:
  - On Robinhood Chain, common lockers may not exist yet. The check
    defaults to "unknown" rather than guessing.
  - We don't try to call lock contracts to read unlock timestamps.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..discovery.rpc_client import RpcClient
from ..logging import get_logger

log = get_logger("bonnet.lp_lock")

# ERC20 selectors
SEL_BALANCE_OF = "0x70a08231"
SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_OWNER = "0x8da5cb5b"
SEL_GET_OWNER = "0x893d20e8"
SEL_FACTORY = "0xc45a0155"  # factory()
SEL_TOKEN0 = "0x0dfe1681"  # token0()
SEL_TOKEN1 = "0xd21220a7"  # token1()
SEL_GET_RESERVES = "0x0902f1ac"  # getReserves() returns (uint112, uint112, uint32)

# Dead address — used by some lockers for "permanent" LP locks
DEAD_ADDRESS = "0x000000000000000000000000000000000000dead"

# Known locker contracts. Empty for now — populate as we identify them
# on Robinhood Chain (Unicrypt, Team.Finance, etc.).
KNOWN_LOCKERS: set[str] = set()


@dataclass
class LPLockInfo:
    is_lp_token: bool = False  # whether the pair address actually has ERC20 LP semantics
    lp_total_supply: int = 0
    dead_address_share_pct: float | None = None  # % of LP held by 0x..dead
    top_holder_address: str | None = None
    top_holder_share_pct: float | None = None
    locked_via_known_locker: bool = False
    lp_locked: bool | None = None  # True / False / None (unknown)
    notes: list[str] | None = None


def _addr_topic(addr: str) -> str:
    addr = addr.lower().replace("0x", "")
    return "0x" + addr.rjust(64, "0")


def _decode_uint(value_hex: str) -> int:
    if not value_hex or value_hex == "0x":
        return 0
    return int(value_hex, 16)


class LPLockDetector:
    """Detect whether a Uniswap v2-style pair has its LP locked."""

    def __init__(self, rpc: RpcClient):
        self._rpc = rpc

    async def probe(self, pair_address: str) -> LPLockInfo:
        """Probe a pair for LP lock status."""
        pair = pair_address.lower()
        info = LPLockInfo()
        notes: list[str] = []

        # Some chains use 64-hex pool IDs (Uniswap v4) that aren't contract
        # addresses — eth_call rejects them. Skip silently.
        if len(pair) != 42:
            notes.append(f"pair address length {len(pair)} (not 40-hex); LP probe skipped")
            info.notes = notes
            return info

        # 1. Read totalSupply of the pair (which IS the LP token for v2)
        try:
            ts_hex = await self._rpc.eth_call(to=pair, data=SEL_TOTAL_SUPPLY)
            ts = _decode_uint(ts_hex)
            if ts > 0:
                info.is_lp_token = True
                info.lp_total_supply = ts
            else:
                notes.append("pair has zero total supply (not a real LP)")
                info.notes = notes
                return info
        except Exception as e:
            notes.append(f"totalSupply() failed: {e}")
            info.notes = notes
            return info

        # 2. Read dead address share
        try:
            bal_hex = await self._rpc.eth_call(
                to=pair,
                data=SEL_BALANCE_OF + _addr_topic(DEAD_ADDRESS)[2:],
            )
            bal = _decode_uint(bal_hex)
            if bal > 0:
                share = bal / ts * 100.0
                info.dead_address_share_pct = share
                notes.append(f"dead address holds {share:.1f}% of LP")
        except Exception:
            pass

        # 3. We can't enumerate all LP holders cheaply (would need
        # Transfer log scan again). For a quick heuristic: if the
        # pair's owner is a known locker or the dead address, mark locked.
        try:
            owner_result = None
            for sel in (SEL_OWNER, SEL_GET_OWNER):
                try:
                    r = await self._rpc.eth_call(to=pair, data=sel)
                    if r and r != "0x":
                        owner_result = "0x" + r[-40:]
                        break
                except Exception:
                    continue
            if owner_result is not None:
                owner_lower = owner_result.lower()
                if owner_lower == DEAD_ADDRESS:
                    info.lp_locked = True
                    notes.append("pair owner is dead address (effectively renounced)")
                elif owner_lower in KNOWN_LOCKERS:
                    info.lp_locked = True
                    info.locked_via_known_locker = True
                    notes.append(f"pair owned by known locker {owner_lower[:10]}…")
                elif owner_lower == ("0x" + "0" * 40):
                    notes.append("pair owner renounced (zero address)")
                    info.lp_locked = None  # unknown — renounced ≠ locked
                else:
                    notes.append(f"pair owner is {owner_lower[:10]}… (not a known locker)")
                    info.lp_locked = False
        except Exception as e:
            notes.append(f"owner probe failed: {e}")

        # 4. Heuristic: high dead-address share often indicates intentional lock
        if info.dead_address_share_pct is not None and info.dead_address_share_pct >= 50.0:
            info.lp_locked = True
            notes.append("≥50% LP in dead address → treated as locked")

        info.notes = notes
        return info


__all__ = ["KNOWN_LOCKERS", "LPLockDetector", "LPLockInfo"]
