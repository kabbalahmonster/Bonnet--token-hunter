"""Hand-crafted fixtures for scorer tests.

These represent 'shape' archetypes we've seen on real chain:
  - good_volume_steady: healthy two-sided action
  - rug_in_progress: heavy one-way downside
  - dead_coin: no volume, no volatility
  - pump_and_dump: extreme single-bar moves
  - honeypot_signal: zero sells possible

Use these to assert the scorer's behavior is sane across the spectrum.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from bonnet.models import Chain, Pair, Token


def _token(symbol: str = "TKN") -> Token:
    return Token(
        address="0x" + "1" * 40,
        chain=Chain.ROBINHOOD,
        symbol=symbol,
        name= symbol + " Token",
        decimals=18,
    )


def good_volume_steady() -> Pair:
    now = datetime.now(UTC) - timedelta(days=14)
    return Pair(
        pair_address="0x" + "a" * 40,
        chain=Chain.ROBINHOOD,
        dex="robinhood_swap",
        token=_token("GOOD"),
        quote_symbol="WETH",
        created_at=now,
        volume_usd_24h=250_000.0,
        volume_usd_6h=60_000.0,
        volume_usd_1h=10_000.0,
        liquidity_usd=120_000.0,
        price_usd=0.001,
        price_change_pct_24h=22.0,
        price_change_pct_1h=3.0,
        txns_24h=420,
        txns_24h_buys=210,
        txns_24h_sells=210,
    )


def rug_in_progress() -> Pair:
    now = datetime.now(UTC) - timedelta(days=2)
    return Pair(
        pair_address="0x" + "b" * 40,
        chain=Chain.ROBINHOOD,
        dex="robinhood_swap",
        token=_token("RUG"),
        quote_symbol="WETH",
        created_at=now,
        volume_usd_24h=80_000.0,
        volume_usd_6h=8_000.0,
        volume_usd_1h=1_500.0,
        liquidity_usd=15_000.0,
        price_usd=0.0001,
        price_change_pct_24h=-78.0,
        price_change_pct_1h=-25.0,
        txns_24h=200,
        txns_24h_buys=20,
        txns_24h_sells=180,
    )


def dead_coin() -> Pair:
    now = datetime.now(UTC) - timedelta(days=60)
    return Pair(
        pair_address="0x" + "c" * 40,
        chain=Chain.ROBINHOOD,
        dex="robinhood_swap",
        token=_token("DEAD"),
        quote_symbol="WETH",
        created_at=now,
        volume_usd_24h=300.0,
        volume_usd_6h=80.0,
        volume_usd_1h=15.0,
        liquidity_usd=5_000.0,
        price_usd=0.00001,
        price_change_pct_24h=0.5,
        price_change_pct_1h=0.1,
        txns_24h=2,
        txns_24h_buys=1,
        txns_24h_sells=1,
    )


def pump_and_dump() -> Pair:
    now = datetime.now(UTC) - timedelta(hours=4)
    return Pair(
        pair_address="0x" + "d" * 40,
        chain=Chain.ROBINHOOD,
        dex="robinhood_swap",
        token=_token("PUMP"),
        quote_symbol="WETH",
        created_at=now,
        volume_usd_24h=500_000.0,
        volume_usd_6h=50_000.0,
        volume_usd_1h=5_000.0,
        liquidity_usd=40_000.0,
        price_usd=0.002,
        price_change_pct_24h=180.0,
        price_change_pct_1h=-15.0,
        txns_24h=1200,
        txns_24h_buys=200,
        txns_24h_sells=1000,
    )


def honeypot_signal() -> Pair:
    now = datetime.now(UTC) - timedelta(hours=1)
    return Pair(
        pair_address="0x" + "e" * 40,
        chain=Chain.ROBINHOOD,
        dex="robinhood_swap",
        token=_token("HP"),
        quote_symbol="WETH",
        created_at=now,
        volume_usd_24h=50_000.0,
        volume_usd_6h=12_000.0,
        volume_usd_1h=2_000.0,
        liquidity_usd=30_000.0,
        price_usd=0.0005,
        price_change_pct_24h=45.0,
        price_change_pct_1h=4.0,
        txns_24h=300,
        txns_24h_buys=290,
        txns_24h_sells=10,
    )


__all__ = [
    "dead_coin",
    "good_volume_steady",
    "honeypot_signal",
    "pump_and_dump",
    "rug_in_progress",
]
