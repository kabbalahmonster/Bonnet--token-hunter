"""Telegram notifier — uses HTTP API directly (no extra deps).

We use raw HTTP to Bot API rather than pulling in python-telegram-bot, which
would mean another big dependency. The interface is small enough.
"""
from __future__ import annotations

import json

import httpx

from ..logging import get_logger
from ..models import Score

log = get_logger("bonnet.telegram")


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, *, dry_run: bool = False):
        self._token = bot_token
        self._chat_id = chat_id
        self._dry_run = dry_run
        self._client = httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, text: str) -> bool:
        """Send a message. Returns True on success, False on failure (logged)."""
        if not self._token or not self._chat_id:
            log.warning("telegram_not_configured")
            return False
        if self._dry_run:
            log.info("telegram_dry_run", text=text[:200])
            return True
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            r = await self._client.post(
                url,
                json={"chat_id": self._chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
            )
            data = r.json()
            if not data.get("ok"):
                log.warning("telegram_send_failed", response=data)
                return False
            return True
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            log.warning("telegram_send_error", error=str(e))
            return False

    @staticmethod
    def format_alert(score: Score) -> str:
        """Format a Score into a Telegram-friendly alert message."""
        lines = [
            f"🚨 <b>Bonnet alert</b> — {score.token.symbol or '?'} ({score.token.short_address})",
            f"<b>Score:</b> {score.composite:.3f}",
        ]
        if score.pair:
            p = score.pair
            lines.append(f"<b>Vol 24h:</b> ${p.volume_usd_24h:,.0f}")
            lines.append(f"<b>Δ 24h:</b> {p.price_change_pct_24h:+.1f}%")
            lines.append(f"<b>Liquidity:</b> ${p.liquidity_usd:,.0f}")
        lines.append(
            f"<b>Components:</b> vol={score.components.volume_quality:.2f} "
            f"volat={score.components.volatility_character:.2f} "
            f"rug={score.components.rug_resistance:.2f}"
        )
        if score.rug_signals.notes:
            lines.append("<b>Rug notes:</b>")
            for note in score.rug_signals.notes[:5]:
                lines.append(f"  • {note}")
        return "\n".join(lines)


__all__ = ["TelegramNotifier"]
