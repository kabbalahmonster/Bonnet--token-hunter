"""DexScreener client.

Free tier: 300 requests/minute. We cache aggressively and respect rate limits
via a token bucket.

Docs: https://docs.dexscreener.com/api/reference
"""
from __future__ import annotations

import asyncio
import time

import httpx

from ..logging import get_logger
from ..models import Chain, DexSource, Pair, Token

log = get_logger("bonnet.dexscreener")


class DexScreenerError(Exception):
    """DexScreener call failed after retries."""


class DexScreenerClient:
    """Read-only client for DexScreener's pairs endpoint."""

    # DexScreener chain slug for Robinhood Chain
    CHAIN_SLUG = "robinhood"

    def __init__(self, base_url: str = "https://api.dexscreener.com/latest", *, rps: float = 4.0):
        self._base = base_url.rstrip("/")
        self._min_interval = 1.0 / max(rps, 0.1)
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._client = httpx.AsyncClient(timeout=20.0, headers={"User-Agent": "bonnet/0.1"})

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last_call + self._min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict:
        await self._throttle()
        url = f"{self._base}{path}"
        for attempt in (1, 2, 3):
            try:
                r = await self._client.get(url, params=params or {})
                if r.status_code == 429:
                    # Honor Retry-After if present
                    retry_after = float(r.headers.get("Retry-After", "2"))
                    log.warning("dexscreener_429", retry_after_s=retry_after)
                    await asyncio.sleep(retry_after)
                    continue
                r.raise_for_status()
                return r.json()
            except (httpx.HTTPError, httpx.StreamError) as e:
                if attempt == 3:
                    raise DexScreenerError(f"DexScreener GET {path} failed: {e}") from e
                await asyncio.sleep(0.5 * attempt)
        raise DexScreenerError(f"DexScreener GET {path} gave up after 3 attempts")

    async def latest_pairs(self) -> list[Pair]:
        """Discover recent pairs on Robinhood Chain.

        DexScreener doesn't expose a 'list all pairs on chain' endpoint — its
        /dex/pairs/{chain} requires a specific pair address. We use a wide
        search query as a heuristic bootstrap. For real chain coverage, pair
        the result with on-chain factory-event scanning (see factory_scanner).
        """
        data = await self._get("/dex/search", params={"q": self.CHAIN_SLUG})
        raw_pairs = data.get("pairs") or []
        return [self._to_pair(p) for p in raw_pairs if p.get("chainId") == self.CHAIN_SLUG]

    async def token_pairs(self, token_address: str) -> list[Pair]:
        """All pairs for a specific token address (across all chains)."""
        addr = token_address.lower()
        data = await self._get(f"/tokens/{addr}")
        raw_pairs = data.get("pairs") or []
        return [
            self._to_pair(p)
            for p in raw_pairs
            if p.get("chainId") == self.CHAIN_SLUG and (p.get("baseToken", {}).get("address", "").lower() == addr)
        ]

    async def search(self, query: str) -> list[Pair]:
        """Free-text search across all chains. Filter to Robinhood."""
        data = await self._get(f"/dex/search", params={"q": query})
        raw_pairs = data.get("pairs") or []
        return [self._to_pair(p) for p in raw_pairs if p.get("chainId") == self.CHAIN_SLUG]

    @staticmethod
    def _to_pair(raw: dict) -> Pair:
        base = raw.get("baseToken") or {}
        try:
            token = Token(
                address=base.get("address", "0x" + "0" * 40),
                chain=Chain.ROBINHOOD,
                symbol=base.get("symbol", "") or "",
                name=base.get("name", "") or "",
                decimals=int(base.get("decimals") or 18),
                discovered_via=DexSource.DEXSCREENER,
            )
        except ValueError:
            # Skip junk entries
            raise
        price_change = raw.get("priceChange") or {}
        txns = (raw.get("txns") or {}).get("h24") or {}
        liquidity = raw.get("liquidity") or {}
        volume = raw.get("volume") or {}
        return Pair(
            pair_address=raw.get("pairAddress", "0x" + "0" * 40),
            chain=Chain.ROBINHOOD,
            dex=raw.get("dexId", "") or "",
            token=token,
            quote_symbol=(raw.get("quoteToken") or {}).get("symbol", "") or "",
            volume_usd_24h=float(volume.get("h24") or 0.0),
            volume_usd_6h=float(volume.get("h6") or 0.0),
            volume_usd_1h=float(volume.get("h1") or 0.0),
            liquidity_usd=float(liquidity.get("usd") or 0.0),
            price_usd=float(raw.get("priceUsd") or 0.0),
            price_change_pct_24h=float(price_change.get("h24") or 0.0),
            price_change_pct_1h=float(price_change.get("h1") or 0.0),
            txns_24h=int(txns.get("count") or 0),
            txns_24h_buys=int(txns.get("buys") or 0),
            txns_24h_sells=int(txns.get("sells") or 0),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "DexScreenerClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()


__all__ = ["DexScreenerClient", "DexScreenerError"]