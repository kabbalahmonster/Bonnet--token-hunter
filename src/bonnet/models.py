"""Core domain types for Bonnet.

These are the canonical shapes that flow through the system: from discovery
through scoring through alerts. Kept deliberately lean — only fields that are
actually used by downstream stages.
"""
from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Chain(StrEnum):
    """Supported chains. Today: just Robinhood Chain."""

    ROBINHOOD = "robinhood"


class DexSource(StrEnum):
    """Where the pair was discovered."""

    DEXSCREENER = "dexscreener"
    ONCHAIN_FACTORY = "onchain_factory"


class Token(BaseModel):
    """A token, normalized across discovery sources."""

    address: str = Field(..., description="Contract address (lowercased, checksummed on output)")
    chain: Chain = Chain.ROBINHOOD
    symbol: str = ""
    name: str = ""
    decimals: int = 18

    # Provenance
    first_seen_at: datetime | None = None
    discovered_via: DexSource = DexSource.DEXSCREENER

    @field_validator("address")
    @classmethod
    def _lower_address(cls, v: str) -> str:
        if not v.startswith("0x") or len(v) != 42:
            raise ValueError(f"address must be 0x-prefixed 40-hex, got {v!r}")
        return v.lower()

    @property
    def short_address(self) -> str:
        return f"{self.address[:6]}…{self.address[-4:]}"


class Pair(BaseModel):
    """A trading pair (token paired against a quote, usually WETH/USDC on RH)."""

    pair_address: str
    chain: Chain = Chain.ROBINHOOD
    dex: str = ""
    token: Token
    quote_symbol: str = ""

    created_at: datetime | None = None

    # Volume
    volume_usd_24h: float = 0.0
    volume_usd_6h: float = 0.0
    volume_usd_1h: float = 0.0

    # Liquidity
    liquidity_usd: float = 0.0

    # Price action
    price_usd: float = 0.0
    price_change_pct_24h: float = 0.0
    price_change_pct_1h: float = 0.0

    # Transactions
    txns_24h: int = 0
    txns_24h_buys: int = 0
    txns_24h_sells: int = 0

    @field_validator("pair_address")
    @classmethod
    def _lower_pair_address(cls, v: str) -> str:
        # Uniswap v4 pools use 64-hex pool IDs; everything else uses 40-hex.
        if not v.startswith("0x") or len(v) not in (42, 66):
            raise ValueError(f"pair_address must be 0x-prefixed 40- or 64-hex, got {v!r}")
        return v.lower()


class RugSignals(BaseModel):
    """All rug-resistance signals for a token. Higher rug_risk = worse."""

    # Holder concentration (from ERC20 holders enumeration or DexScreener enrichment)
    top10_holder_pct: float | None = None  # None = unknown yet
    top1_holder_pct: float | None = None

    # Contract verification
    is_contract_verified: bool | None = None
    has_proxy: bool = False

    # Authority
    mint_authority_renounced: bool | None = None
    freeze_authority_renounced: bool | None = None
    owner_renounced: bool | None = None

    # Liquidity
    lp_locked: bool | None = None
    lp_lock_days_remaining: int | None = None

    # Age
    contract_age_hours: float | None = None
    pair_age_hours: float | None = None

    # Deployer history
    deployer_address: str | None = None
    deployer_prior_tokens: int | None = None
    deployer_prior_rugs: int | None = None  # heuristic — past tokens that rugged

    # Honeypot indicators (from simulation)
    is_honeypot: bool | None = None

    # Composite: 0.0 (safe) .. 1.0 (definitely rugging)
    rug_risk: float = 0.0

    notes: list[str] = Field(default_factory=list)


class ScoreComponents(BaseModel):
    """The three sub-scores that compose the final token score."""

    volume_quality: float  # 0..1
    volatility_character: float  # 0..1
    rug_resistance: float  # 0..1 (1.0 = safest)

    @property
    def composite(self) -> float:
        """Weighted composite. Tunable weights — see scoring/weights.py."""
        # Defaults chosen so a "good" candidate lands ~0.65-0.80
        return (
            0.30 * self.volume_quality
            + 0.30 * self.volatility_character
            + 0.40 * self.rug_resistance
        )


class Score(BaseModel):
    """A complete score for a token, with full explainability."""

    token: Token
    pair: Pair | None = None
    components: ScoreComponents
    rug_signals: RugSignals
    composite: float
    scored_at: datetime
    explanation: list[str] = Field(default_factory=list)


__all__ = [
    "Chain",
    "DexSource",
    "Pair",
    "RugSignals",
    "Score",
    "ScoreComponents",
    "Token",
]
