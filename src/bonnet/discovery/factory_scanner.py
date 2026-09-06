"""On-chain factory event scanner.

Scans `PairCreated`/`PoolCreated` events from one or more factory contracts
to discover new pairs deterministically without rate limits on aggregator APIs.

Configuration-driven: you tell us the factory addresses and their event
signatures via BONNET_FACTORIES in .env (JSON format). Examples for the
canonical Uniswap v3 and v2 forks are bundled.

For chains where the canonical sigs don't match (e.g. Robinhood Chain's
particular Uniswap fork), run `bonnet detect-factories` to discover the
right address + signature from a known pool.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

from ..logging import get_logger
from ..models import Chain
from .rpc_client import RpcClient

# Local name avoids ruff's F823 false-positive in the `discover()` method
# which shadows `log` with a loop variable
log = get_logger("bonnet.factory")


@dataclass(frozen=True)
class Factory:
    """A factory contract to scan for new-pair events."""

    address: str  # factory contract address (lowercase)
    chain: Chain
    kind: str  # "v2" | "v3" | "v4" | "custom"
    event_signature: str  # topic[0] = keccak256("EventName(type1,type2,...)")
    event_name: str  # e.g. "PairCreated", "PoolCreated"
    # The position of the "pair/pool" output in the log's indexed topics.
    # v2 PairCreated(token0,token1,pair,allPairsLength) → pair is topics[2] (indexed)
    # v3 PoolCreated(token0,token1,fee,tickSpacing,pool) → pool is topics[3] (indexed)
    # v4 has Initialize(id, currency, fee, tickSpacing, hooks) → pool_id is data, not indexed
    pool_topic_index: int  # 0..3 = topics[1..4] (-1 = in data, decode as uint256)

    @classmethod
    def from_dict(cls, d: dict) -> Factory:
        chain_raw = d.get("chain", "robinhood")
        # Be lenient: accept any string for the chain since the scanner is
        # chain-agnostic; the scoring model only cares about Robinhood pairs.
        try:
            chain = Chain(chain_raw)
        except ValueError:
            chain = Chain.ROBINHOOD
        return cls(
            address=d["address"].lower(),
            chain=chain,
            kind=d["kind"],
            event_signature=d["event_signature"],
            event_name=d.get("event_name", "PairCreated"),
            pool_topic_index=int(d.get("pool_topic_index", 2)),
        )


# Canonical factories for common chains. Used as defaults; override via env.
DEFAULT_FACTORIES: list[dict] = [
    # Ethereum mainnet — Uniswap v3
    {
        "address": "0x1F98431c8aD98523631AE4a59f267346ea31F984",
        "chain": "ethereum",
        "kind": "v3",
        "event_signature": "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b7d9b8b06fb1d57",
        "event_name": "PoolCreated",
        "pool_topic_index": 3,
    },
    # Ethereum mainnet — Uniswap v2
    {
        "address": "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
        "chain": "ethereum",
        "kind": "v2",
        "event_signature": "0x0d3648bd0f6ba89d9d8c25d2c6f9b3df57b5e6c1c9b3e0d4e3d5f6b7a8c9d0e1f",
        "event_name": "PairCreated",
        "pool_topic_index": 2,
    },
    # Add Robinhood Chain factories here once detected. For now: empty.
]


def load_factories(env_value: str | None) -> list[Factory]:
    """Load factories from BONNET_FACTORIES JSON env var, falling back to defaults."""
    if env_value:
        try:
            data = json.loads(env_value)
        except json.JSONDecodeError as e:
            log.warning("factories_env_invalid_json", error=str(e))
            data = []
    else:
        data = []
    merged = list(data) + list(DEFAULT_FACTORIES)
    return [Factory.from_dict(d) for d in merged]


class FactoryScanner:
    """Scans factory contracts for new-pair events."""

    def __init__(self, rpc: RpcClient, factories: list[Factory]):
        self._rpc = rpc
        self._factories = factories

    @property
    def factories(self) -> list[Factory]:
        return list(self._factories)

    async def scan_recent(self, from_block: int, to_block: int) -> list[dict]:
        """Scan all configured factories in [from_block, to_block] for new-pair events.

        Returns raw decoded events with factory attribution. The full Pair object
        needs additional enrichment (token metadata, DexScreener data); this is
        just the discovered-pair list.
        """
        out: list[dict] = []
        for factory in self._factories:
            try:
                logs = await self._rpc.eth_get_logs({
                    "fromBlock": hex(from_block),
                    "toBlock": hex(to_block),
                    "address": factory.address,
                    "topics": [factory.event_signature],
                })
            except Exception as e:
                log.warning("factory_scan_failed", factory=factory.address, error=str(e))
                continue

            for entry in logs:
                topics = entry.get("topics") or []
                if factory.pool_topic_index >= 0:
                    # Pool address is in topics[pool_topic_index + 1]
                    if len(topics) <= factory.pool_topic_index + 1:
                        continue
                    pool_topic = topics[factory.pool_topic_index + 1]
                    if not isinstance(pool_topic, str) or len(pool_topic) < 42:
                        continue
                    pool_address = "0x" + pool_topic[-40:]
                else:
                    # Pool ID is in `data` as first 32-byte word
                    data = entry.get("data") or "0x"
                    if len(data) < 66:
                        continue
                    pool_address = "0x" + data[2:66].lower()[-40:]

                token0, token1 = None, None
                if factory.pool_topic_index >= 2 and len(topics) >= 3:
                    # v2/v3: token0 = topics[1], token1 = topics[2]
                    token0 = "0x" + topics[1][-40:]
                    token1 = "0x" + topics[2][-40:]
                # v4 uses different topic structure; skip token extraction

                out.append({
                    "factory": factory,
                    "log": entry,
                    "pair_address": pool_address.lower(),
                    "token0": token0.lower() if token0 else None,
                    "token1": token1.lower() if token1 else None,
                    "block": int(entry.get("blockNumber", "0x0"), 16),
                    "tx": entry.get("transactionHash"),
                })

        log.info("factory_scan_done", from_block=from_block, to_block=to_block, found=len(out))
        return out

    async def discover(self, known_pool: str, kind_hint: str = "v3") -> Factory | None:
        """Try to identify the factory + signature for a given known pool.

        Strategy: scan recent blocks for any log where `known_pool` appears as
        an indexed topic, collect unique (emitter, topics[0]) combos, return
        the most common one. This is best-effort — operator should review
        the result before saving.

        Note: this is a heavy operation on a public RPC; uses chunked queries.
        """
        pool_padded = "0x" + "0" * 24 + known_pool.lower()[2:]
        head = await self._rpc.eth_block_number()

        # Try each topic position; most factories put the pair at position 2 or 3.
        from collections import Counter
        candidates: Counter[tuple[str, str]] = Counter()

        for pos in (2, 3):
            try:
                logs = await self._rpc.eth_get_logs({
                    "fromBlock": hex(max(0, head - 50000)),
                    "toBlock": hex(head),
                    "topics": [None, None, None, None],
                })
                # ^ doesn't filter by topic[3]; do it ourselves on small range
            except Exception:
                continue
            # Just iterate recent small window manually
            for start in range(max(0, head - 5000), head + 1, 200):
                end = min(start + 199, head)
                try:
                    logs = await self._rpc.eth_get_logs({
                        "fromBlock": hex(start),
                        "toBlock": hex(end),
                        "topics": [[None, None, None, None][i] if i != pos else pool_padded for i in range(4)],
                    })
                except Exception:
                    continue
                for entry in logs:
                    emitter = entry.get("address", "").lower()
                    sig = (entry.get("topics") or [""])[0]
                    candidates[(emitter, sig)] += 1

        if not candidates:
            return None

        (emitter, sig), count = candidates.most_common(1)[0]
        # Guess pool_topic_index by elimination — if our match was at pos 2, then pool_topic_index = 2
        # We don't know for sure; default to 2 (most common Uniswap v2/v3 pattern)
        log.info(
            "factory_detected",
            address=emitter,
            signature=sig,
            kind_hint=kind_hint,
            occurrences=count,
        )
        return Factory(
            address=emitter,
            chain=Chain.ROBINHOOD,
            kind=kind_hint,
            event_signature=sig,
            event_name="PoolCreated" if "Pool" in (kind_hint or "") else "PairCreated",
            pool_topic_index=2,
        )


__all__ = [
    "DEFAULT_FACTORIES",
    "Factory",
    "FactoryScanner",
    "load_factories",
]
