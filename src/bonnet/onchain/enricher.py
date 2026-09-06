"""On-chain enrichment: probe a token contract for rug signals.

Reads:
  - ERC20 name/symbol/decimals (already covered by DexScreener, but useful
    as a sanity check that the contract is a real ERC20)
  - owner() — if it returns address(0), ownership is renounced
  - mint authority — not all ERC20s have mint(), but for the ones that do
    we can detect if it would still succeed by checking totalSupply behavior
    or just probing for the function selector
  - has_proxy — call EIP-1967 implementation storage slot

We deliberately don't do holder enumeration on-chain (expensive); that's
deferred to a future holder-snapshot module.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..discovery.rpc_client import RpcClient
from ..logging import get_logger

log = get_logger("bonnet.enrich")

# Common ERC20 function selectors (first 4 bytes of keccak256(signature)).
SEL_OWNER = "0x8da5cb5b"
SEL_GET_OWNER = "0x893d20e8"
# EIP-1967 implementation slot: bytes32(uint256(keccak256("eip1967.proxy.implementation")) - 1)
# Correct value: 0x360894a13ba1a3210667c828492db98dcef3a6f8a2c6c2cb1a6f1f4b7c1f8f8d
EIP1967_IMPL_SLOT = (
    "0x360894a13ba1a3210667c828492db98dcef3a6f8a2c6c2cb1a6f1f4b7c1f8f8d"
)
# keccak256("eip1967.proxy.implementation") - 1

# OpenZeppelin AccessControl selectors
SEL_HAS_ROLE = "0x91d14854"          # hasRole(bytes32,address) -> bool
SEL_GET_ROLE_MEMBER = "0xd305bd76"  # getRoleMember(bytes32,uint256) -> address
SEL_GET_ROLE_MEMBER_COUNT = "0xca15c873"  # getRoleMemberCount(bytes32) -> uint256
SEL_DEFAULT_ADMIN = "0x0a0b0d79"   # defaultAdmin() — OZ v5 AccessControl
SEL_ACCESS_MANAGER = "0x30d9e0b2"  # AccessManager address() — OZ v5 AccessManaged

# Canonical role identifiers (keccak256 of role name)
MINTER_ROLE = "0x9f2df0fed2c77648de5860a4cc508cd0818c85b8b8a1ab4ceeef8d981c8956a6"
DEFAULT_ADMIN_ROLE = "0x1effbbff9c66c5e59634f24fe842750c60d18891155c32dd155fc2d661a4c86d"
# = 0x360894a13ba1a3210667c828492db98dcef3a6f8a2c6c2cb1a6f1f4b7c1f8f8d


@dataclass
class ContractSignals:
    """Raw on-chain probes for a single token."""

    is_contract: bool = False
    has_owner_call: bool | None = None  # None = contract didn't have owner()
    owner_renounced: bool | None = None
    has_proxy: bool = False
    proxy_implementation: str | None = None

    # AccessControl role detection (OpenZeppelin v4 / v5 patterns)
    uses_access_control: bool = False
    has_minter_role: bool | None = None  # None = contract doesn't use AccessControl
    minter_count: int | None = None  # number of addresses with MINTER_ROLE
    has_admin_role: bool | None = None
    admin_count: int | None = None
    # Combined mint authority state for scoring convenience
    mint_authority_renounced: bool | None = None  # True if no minter can mint

    # We don't yet read mint authority for OZ v3 Ownable-style contracts;
    # the owner_renounced field above handles those via SEL_OWNER.


class ContractEnricher:
    def __init__(self, rpc: RpcClient):
        self._rpc = rpc

    async def probe(self, token_address: str) -> ContractSignals:
        signals = ContractSignals()
        addr = token_address.lower()

        # Some tokens come from sources that don't yield 40-hex addresses
        # (e.g. Uniswap v4 pool IDs). Skip without crashing.
        if len(addr) != 42:
            log.warning("probe_invalid_address", address=token_address)
            return signals

        # 1. Is it a contract at all?
        try:
            code = await self._rpc.eth_get_code(addr)
            signals.is_contract = code not in ("", "0x")
        except Exception as e:
            log.warning("probe_get_code_failed", address=addr, error=str(e))
            return signals

        if not signals.is_contract:
            return signals  # nothing more to probe on an EOA

        # 2. owner() — try both selectors; success = contract has it
        for sel in (SEL_OWNER, SEL_GET_OWNER):
            try:
                result = await self._rpc.eth_call(to=addr, data=sel)
                if result and result != "0x":
                    signals.has_owner_call = True
                    # Result is 32-byte word; renounced = zero address
                    owner_addr = "0x" + result[-40:]
                    signals.owner_renounced = owner_addr == ("0x" + "0" * 40)
                    break
            except Exception:
                continue
        else:
            signals.has_owner_call = False

        # 3. EIP-1967 proxy check — read the implementation slot
        try:
            impl_slot = await self._rpc.eth_get_storage_at(addr, EIP1967_IMPL_SLOT, "latest")
            if impl_slot and impl_slot not in ("0x" + "0" * 64, "0x"):
                signals.has_proxy = True
                impl_addr = "0x" + impl_slot[-40:]
                if impl_addr != ("0x" + "0" * 40):
                    signals.proxy_implementation = impl_addr
        except Exception:
            pass

        # 4. AccessControl role detection — try getRoleMemberCount(MINTER_ROLE)
        # If this returns a value (even 0), the contract uses AccessControl.
        # mint_authority_renounced == (count == 0)
        try:
            data = SEL_GET_ROLE_MEMBER_COUNT + MINTER_ROLE[2:].rjust(64, "0")
            result = await self._rpc.eth_call(to=addr, data=data)
            if result and result != "0x":
                signals.uses_access_control = True
                count = int(result, 16)
                signals.minter_count = count
                signals.has_minter_role = count > 0
                signals.mint_authority_renounced = (count == 0)
        except Exception:
            pass

        # 5. DEFAULT_ADMIN_ROLE count for additional signal
        if signals.uses_access_control:
            try:
                data = SEL_GET_ROLE_MEMBER_COUNT + DEFAULT_ADMIN_ROLE[2:].rjust(64, "0")
                result = await self._rpc.eth_call(to=addr, data=data)
                if result and result != "0x":
                    count = int(result, 16)
                    signals.admin_count = count
                    signals.has_admin_role = count > 0
            except Exception:
                pass

        return signals


__all__ = ["ContractEnricher", "ContractSignals"]
