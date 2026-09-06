"""Settings — single source of truth for runtime config.

Loaded from environment / .env via pydantic-settings. Validated at startup
so a misconfigured deployment fails fast with a useful message.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Bonnet runtime configuration."""

    model_config = SettingsConfigDict(
        env_prefix="BONNET_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # RPC — list of endpoints tried in order with failover.
    # NoDecode tells pydantic-settings to skip its built-in JSON parsing so our
    # field_validator can split the raw env string on commas.
    rpc_endpoints: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "https://mainnet.rpc.robinhood.com",
        ]
    )

    # Discovery
    dexscreener_base_url: str = "https://api.dexscreener.com/latest"

    # Scoring thresholds
    score_alert_threshold: float = 0.65
    volume_min_usd_24h: float = 5_000.0
    age_min_hours: float = 2.0

    # Rug heuristics
    rug_max_top10_holder_pct: float = 40.0
    rug_min_lp_lock_days: int = 7
    rug_min_contract_age_hours: float = 24.0

    # Telegram (optional)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Storage
    db_path: Path = Path("./state/bonnet.db")

    # Factories (JSON string). See factory_scanner.DEFAULT_FACTORIES for the
    # canonical examples. Empty list = rely on defaults.
    factories_json: str = ""

    # Logging
    log_level: str = "INFO"

    @field_validator("rpc_endpoints", mode="before")
    @classmethod
    def _split_endpoints(cls, v: object) -> object:
        """Allow comma-separated string from env (BONNET_RPC_ENDPOINTS=a,b,c)."""
        if isinstance(v, str):
            return [e.strip() for e in v.split(",") if e.strip()]
        return v

    @field_validator("db_path", mode="before")
    @classmethod
    def _expand_path(cls, v: object) -> object:
        if isinstance(v, str):
            return Path(v).expanduser()
        return v

    @field_validator("log_level")
    @classmethod
    def _upper_log_level(cls, v: str) -> str:
        return v.upper()


_cached: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide cached Settings instance."""
    global _cached
    if _cached is None:
        _cached = Settings()
    return _cached


def reset_cache() -> None:
    """Clear the settings cache. For tests."""
    global _cached
    _cached = None


def reload_from(env_file: str | Path) -> Settings:
    """Reload settings from a specific env file (e.g. for tests)."""
    global _cached
    _cached = Settings(_env_file=str(env_file))
    return _cached


# Convenience: assert at module import time that required dirs exist
def ensure_dirs(settings: Settings | None = None) -> None:
    """Create storage dirs if missing. Idempotent."""
    s = settings or get_settings()
    s.db_path.parent.mkdir(parents=True, exist_ok=True)
    Path("./logs").mkdir(parents=True, exist_ok=True)


__all__ = ["Settings", "ensure_dirs", "get_settings", "reload_from", "reset_cache"]
