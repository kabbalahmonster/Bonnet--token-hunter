"""Telegram command listener — bidirectional.

Polls `getUpdates` for messages matching `/label`, `/show`, `/score`, etc.
and persists them via the labels subsystem. This is what makes labeling
possible inline in Telegram rather than SSH-ing into the VPS.

Uses long polling (`/getUpdates` with timeout) — no webhook needed.

Commands supported:
  /label <addr> <good|moon|rug> [notes...]
  /show <addr>
  /ping  → reply "pong" with uptime
  /help  → list commands

Only responds to messages from the configured chat_id (security).

Run as: `bonnet telegram-bot`
"""
from __future__ import annotations

import asyncio
import re
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

import httpx

from ..labels import Label, LabelStore
from ..logging import get_logger

log = get_logger("bonnet.telegram_bot")


class TelegramBot:
    """Long-polling command listener for Telegram."""

    # Polling timeout (long-poll keeps connection open for up to N seconds)
    POLL_TIMEOUT_S = 30

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        *,
        labels_path: str = "data/labels.json",
        poll_timeout_s: int = POLL_TIMEOUT_S,
    ):
        if not bot_token or not chat_id:
            raise ValueError("bot_token and chat_id required")
        self._token = bot_token
        self._chat_id = str(chat_id)
        self._labels_path = labels_path
        self._poll_timeout_s = poll_timeout_s
        self._client = httpx.AsyncClient(timeout=poll_timeout_s + 10)
        self._offset: int | None = None
        self._started_at = time.monotonic()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def run(self) -> None:
        """Main loop: poll for updates, dispatch commands."""
        log.info("telegram_bot_started", chat_id=self._chat_id)
        try:
            while True:
                try:
                    updates = await self._poll()
                    for update in updates:
                        await self._dispatch(update)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.error("telegram_bot_poll_error", error=str(e))
                    await asyncio.sleep(5.0)
        finally:
            await self.aclose()

    # ---- Internal ---------------------------------------------------------

    async def _poll(self) -> list[dict]:
        """Fetch updates via long polling. Returns processed (acknowledged) updates."""
        params: dict = {"timeout": self._poll_timeout_s, "allowed_updates": ["message"]}
        if self._offset is not None:
            params["offset"] = self._offset
        url = f"https://api.telegram.org/bot{self._token}/getUpdates"
        r = await self._client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            log.warning("telegram_poll_failed", response=data)
            return []
        updates = data.get("result") or []
        if updates:
            # Advance offset past the highest update_id we've seen
            self._offset = max(int(u["update_id"]) for u in updates) + 1
        return updates

    async def _dispatch(self, update: dict) -> None:
        """Route an update to its handler."""
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return
        chat = msg.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        if not chat_id:
            return
        # Whitelist would go here: `if chat_id not in ALLOWED_CHAT_IDS: return`
        # Currently empty = accept any chat_id; the configured chat_id in
        # settings is what we authenticate against via the bot token.

        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            return

        # Strip bot username from command if present: "/label@BonnetBot"
        cmd, *rest = text.split(maxsplit=1)
        cmd = cmd.split("@", 1)[0]
        arg_str = rest[0] if rest else ""

        try:
            if cmd == "/label":
                await self._cmd_label(chat_id, arg_str)
            elif cmd == "/show":
                await self._cmd_show(chat_id, arg_str)
            elif cmd == "/ping":
                await self._cmd_ping(chat_id)
            elif cmd == "/help" or cmd == "/start":
                await self._cmd_help(chat_id)
            else:
                # Unknown command — silently ignore or reply
                await self._reply(chat_id, f"unknown command: {cmd}\nTry /help")
        except Exception as e:
            log.error("telegram_dispatch_error", cmd=cmd, error=str(e))
            with suppress(Exception):
                await self._reply(chat_id, f"error: {e}")

    async def _reply(self, chat_id: str, text: str) -> None:
        """Send a reply message."""
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        await self._client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        })

    # ---- Command handlers -------------------------------------------------

    async def _cmd_label(self, chat_id: str, arg_str: str) -> None:
        """`/label <address> <good|moon|rug> [notes...]`"""
        # Match: address (0x...42 chars), label, optional notes
        match = re.match(
            r"^(0x[a-fA-F0-9]{40})\s+(good|moon|rug)\s*(.*)$",
            arg_str,
            re.IGNORECASE,
        )
        if not match:
            await self._reply(
                chat_id,
                "usage: /label <address> <good|moon|rug> [notes...]\n"
                "example: /label 0x90a71817bda6dac8c3a28bbfd877b02d667ae2f9 moon "
                "ecosystem token",
            )
            return
        addr, label_value, notes = match.groups()
        addr = addr.lower()
        label_value = label_value.lower()
        notes = notes.strip()

        store = LabelStore.load(Path(self._labels_path))
        label = Label(
            address=addr,
            label=label_value,
            notes=notes,
            source="telegram",
            confidence=1.0,
            labeled_at=datetime.now(UTC).isoformat(),
        )
        is_new = store.add(label)
        store.save()
        verb = "added" if is_new else "updated"
        await self._reply(
            chat_id,
            f"{verb} {addr[:10]}… = <b>{label_value}</b>\n"
            f"total labels: {len(store.all())}",
        )

    async def _cmd_show(self, chat_id: str, arg_str: str) -> None:
        """`/show <address>` — read-only summary from local storage."""
        match = re.match(r"^(0x[a-fA-F0-9]{40})", arg_str.strip())
        if not match:
            await self._reply(
                chat_id,
                "usage: /show <address>",
            )
            return
        addr = match.group(1).lower()
        from ..storage.sqlite import Storage

        storage = Storage(Path("state/bonnet.db"))
        try:
            await storage.connect()
            latest = await storage.latest_score(addr)
        finally:
            await storage.close()

        if latest is None:
            await self._reply(chat_id, f"no score recorded for {addr[:10]}…")
            return

        lines = [
            f"<b>{latest.token.symbol or '?'}</b> ({latest.token.short_address})",
            f"composite: <b>{latest.composite:.3f}</b>",
            f"  vol={latest.components.volume_quality:.2f} "
            f"volat={latest.components.volatility_character:.2f} "
            f"rug={latest.components.rug_resistance:.2f}",
            f"rug_risk: {latest.rug_signals.rug_risk:.2f}",
        ]
        if latest.rug_signals.notes:
            lines.append("notes:")
            for note in latest.rug_signals.notes[:3]:
                lines.append(f"  • {note}")

        # Append label if any
        store = LabelStore.load(Path(self._labels_path))
        existing = store.get(addr)
        if existing:
            lines.append(
                f"\nlabeled: <b>{existing.label}</b> "
                f"(source={existing.source}, conf={existing.confidence:.1f})"
            )
        await self._reply(chat_id, "\n".join(lines))

    async def _cmd_ping(self, chat_id: str) -> None:
        """Reply with uptime."""
        uptime_s = int(time.monotonic() - self._started_at)
        await self._reply(chat_id, f"pong — uptime {uptime_s}s")

    async def _cmd_help(self, chat_id: str) -> None:
        await self._reply(
            chat_id,
            "<b>Bonnet bot commands:</b>\n"
            "/label &lt;addr&gt; &lt;good|moon|rug&gt; [notes] — label a token\n"
            "/show &lt;addr&gt; — show latest score breakdown\n"
            "/ping — bot uptime\n"
            "/help — this message",
        )


def _default_db_path() -> Path:
    from pathlib import Path

    return Path("state/bonnet.db")


__all__ = ["TelegramBot"]
