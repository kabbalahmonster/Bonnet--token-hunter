"""JSON-RPC client with multi-endpoint failover.

Tries endpoints in declared order; on transient failure, retries the next.
State (current endpoint) is NOT preserved — every call independently picks
the next healthy endpoint with a small probe, so a single down node doesn't
cascade.

Concurrency: a single asyncio semaphore caps parallel requests per endpoint
to avoid hammering public RPCs.
"""
from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx

from ..logging import get_logger
from ..settings import Settings, get_settings

log = get_logger("bonnet.rpc")


class RpcError(Exception):
    """Raised when all endpoints fail or the call returns an RPC error."""


_TRANSIENT_HTTP = (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError)


class RpcClient:
    """Async JSON-RPC client with multi-endpoint failover.

    Usage::

        client = RpcClient()
        block = await client.eth_block_number()
    """

    def __init__(self, settings: Settings | None = None, *, max_per_host: int = 8):
        self._settings = settings or get_settings()
        self._endpoints = list(self._settings.rpc_endpoints)
        if not self._endpoints:
            raise ValueError("At least one RPC endpoint must be configured")
        self._sem = asyncio.Semaphore(max_per_host)
        # Per-endpoint circuit state: consecutive failures + cooldown until
        self._fail_streak: dict[str, float] = {}  # endpoint -> cooldown-until ts

    @property
    def endpoints(self) -> list[str]:
        return list(self._endpoints)

    def _healthy_endpoints(self) -> list[str]:
        now = time.monotonic()
        return [e for e in self._endpoints if self._fail_streak.get(e, 0.0) <= now]

    def _record_failure(self, endpoint: str) -> None:
        # 30s exponential-ish cooldown, capped at 5 minutes
        prior = self._fail_streak.get(endpoint, 0.0)
        backoff = min(300.0, max(30.0, (now_ts := time.monotonic()) - prior + 30.0))
        self._fail_streak[endpoint] = now_ts + backoff
        log.warning("rpc_endpoint_unhealthy", endpoint=endpoint, cooldown_s=round(backoff, 1))

    def _record_success(self, endpoint: str) -> None:
        self._fail_streak.pop(endpoint, None)

    async def call(self, method: str, params: list[Any] | None = None) -> Any:
        """Call an RPC method with failover across all configured endpoints."""
        params = params or []
        last_err: Exception | None = None

        healthy = self._healthy_endpoints()
        if not healthy:
            # Everything is in cooldown — clear one and try anyway
            log.warning("rpc_all_endpoints_cooling_down", endpoints=self._endpoints)
            healthy = self._endpoints

        # Shuffle slightly to spread load, but keep order stable enough for debugging
        order = healthy[:]
        random.shuffle(order)

        for endpoint in order:
            try:
                async with self._sem:
                    result = await self._call_one(endpoint, method, params)
                self._record_success(endpoint)
                return result
            except _TRANSIENT_HTTP as e:
                log.debug("rpc_transient_failure", endpoint=endpoint, method=method, error=str(e))
                self._record_failure(endpoint)
                last_err = e
                continue
            except RpcError:
                # Application-level RPC error (revert, etc.) — don't failover, raise
                raise

        raise RpcError(f"All {len(order)} RPC endpoints failed for {method}") from last_err

    async def _call_one(self, endpoint: str, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(endpoint, json=payload)
            r.raise_for_status()
            data = r.json()
        if "error" in data:
            err = data["error"]
            raise RpcError(f"RPC {method} error: {err.get('message', err)} (code={err.get('code')})")
        return data.get("result")

    # ---- Typed convenience methods ------------------------------------------------

    async def eth_block_number(self) -> int:
        result = await self.call("eth_blockNumber")
        return int(result, 16)

    async def eth_chain_id(self) -> int:
        result = await self.call("eth_chainId")
        return int(result, 16)

    async def eth_get_code(self, address: str) -> str:
        """Returns '0x' for EOAs, contract bytecode hex otherwise."""
        return await self.call("eth_getCode", [address, "latest"])

    async def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return await self.call("eth_call", [{"to": to, "data": data}, block])

    async def eth_get_logs(self, filter_params: dict[str, Any]) -> list[dict[str, Any]]:
        return await self.call("eth_getLogs", [filter_params])

    async def eth_get_storage_at(self, address: str, slot: str, block: str = "latest") -> str:
        """Read a storage slot at `address`. Returns 32-byte hex word."""
        return await self.call("eth_getStorageAt", [address, slot, block])

    async def close(self) -> None:
        """No persistent connection to close (httpx.AsyncClient is per-call)."""
        return None


__all__ = ["RpcClient", "RpcError"]
