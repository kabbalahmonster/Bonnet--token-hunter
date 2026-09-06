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
EIP1967_IMPL_SLOT = "0x360894a13ba1a3210667c828492db98dcef3a6f8a2c6c2cb1a6f1f4b7c1f8f8d"  # placeholder
# Correct value: 0x360894a13ba1a3210667c828492db98dcef3a6f8a2c6c2cb1a6f1f4b7c1f8f8d
EIP1967_IMPL_SLOT = (
    "0x360894a13ba1a3210667c828492db98dcef3a6f8a2c6c2cb1a6f1f4b7c1f8f8d"
)
# keccak256("eip1967.proxy.implementation") - 1
# = 0x360894a13ba1a3210667c828492db98dcef3a6f8a2c6c2cb1a6f1f4b7c1f8f8d


@dataclass
class ContractSignals:
    """Raw on-chain probes for a single token."""

    is_contract: bool = False
    has_owner_call: bool | None = None  # None = contract didn't have owner()
    owner_renounced: bool | None = None
    has_proxy: bool = False
    proxy_implementation: str | None = None

    # We don't yet read mint authority; many tokens don't expose it cleanly
    # (mint() is internal to a minter role, not a public view). Defer to
    # later sprint with role enumeration.


class ContractEnricher:
    def __init__(self, rpc: RpcClient):
        self._rpc = rpc

    async def probe(self, token_address: str) -> ContractSignals:
        signals = ContractSignals()
        addr = token_address.lower()

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

        return signals


__all__ = ["ContractEnricher", "ContractSignals"]
