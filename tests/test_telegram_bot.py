"""Tests for the Telegram bot command listener.

We mock httpx to avoid hitting the live Telegram API. These tests verify
the dispatch logic: parsing, validation, persistence, replies.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from bonnet.labels import LabelStore
from bonnet.notify.telegram_bot import TelegramBot


def _make_bot(labels_path: Path, chat_id: str = "123456") -> TelegramBot:
    return TelegramBot(
        bot_token="test-token",
        chat_id=chat_id,
        labels_path=str(labels_path),
        poll_timeout_s=1,
    )


def _update(text: str, *, chat_id: str = "123456", update_id: int = 1) -> dict:
    """Build a fake Telegram update payload."""
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": int(chat_id)},
            "text": text,
        },
    }


class TestCmdLabel:
    async def test_label_command_persists_label(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        addr = "0x" + "a" * 40
        await bot._cmd_label(
            "123456",
            f"{addr} moon ecosystem token",
        )
        # Check the file
        store = LabelStore.load(tmp_path / "labels.json")
        labels = store.all()
        assert len(labels) == 1
        assert labels[0].address == addr
        assert labels[0].label == "moon"
        assert labels[0].notes == "ecosystem token"
        assert labels[0].source == "telegram"
        assert labels[0].confidence == 1.0

    async def test_label_command_lowercases_address(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        await bot._cmd_label("123456", "0xABCDEF" + "0" * 34 + " good")
        store = LabelStore.load(tmp_path / "labels.json")
        assert store.all()[0].address == "0xabcdef" + "0" * 34

    async def test_label_command_overwrites_existing(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        addr = "0x" + "a" * 40
        await bot._cmd_label("123456", f"{addr} good first")
        await bot._cmd_label("123456", f"{addr} moon updated")
        store = LabelStore.load(tmp_path / "labels.json")
        # Only one label (overwritten), with the new value
        labels = store.all()
        assert len(labels) == 1
        assert labels[0].label == "moon"
        assert labels[0].notes == "updated"

    async def test_label_command_bad_address_replies_with_usage(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        # Bad address (not 42 chars)
        reply_text: list[str] = []
        bot._reply = AsyncMock(side_effect=lambda _cid, text: reply_text.append(text))  # type: ignore[method-assign]
        await bot._cmd_label("123456", "0xshort good")
        assert any("usage" in t for t in reply_text)
        # No label should be persisted
        store = LabelStore.load(tmp_path / "labels.json")
        assert store.all() == []

    async def test_label_command_bad_label_replies_with_usage(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        reply_text: list[str] = []
        bot._reply = AsyncMock(side_effect=lambda _cid, text: reply_text.append(text))  # type: ignore[method-assign]
        addr = "0x" + "a" * 40
        await bot._cmd_label("123456", f"{addr} INVALID")
        assert any("usage" in t for t in reply_text)
        store = LabelStore.load(tmp_path / "labels.json")
        assert store.all() == []


class TestCmdShow:
    async def test_show_returns_no_score_message(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        reply_text: list[str] = []
        bot._reply = AsyncMock(side_effect=lambda _cid, text: reply_text.append(text))  # type: ignore[method-assign]
        addr = "0x" + "a" * 40
        await bot._cmd_show("123456", addr)
        assert any("no score recorded" in t for t in reply_text)

    async def test_show_bad_address_replies_with_usage(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        reply_text: list[str] = []
        bot._reply = AsyncMock(side_effect=lambda _cid, text: reply_text.append(text))  # type: ignore[method-assign]
        await bot._cmd_show("123456", "garbage")
        assert any("usage" in t for t in reply_text)


class TestCmdPing:
    async def test_ping_returns_pong(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        reply_text: list[str] = []
        bot._reply = AsyncMock(side_effect=lambda _cid, text: reply_text.append(text))  # type: ignore[method-assign]
        await bot._cmd_ping("123456")
        assert any("pong" in t for t in reply_text)


class TestCmdHelp:
    async def test_help_lists_commands(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        reply_text: list[str] = []
        bot._reply = AsyncMock(side_effect=lambda _cid, text: reply_text.append(text))  # type: ignore[method-assign]
        await bot._cmd_help("123456")
        joined = " ".join(reply_text)
        assert "/label" in joined
        assert "/show" in joined
        assert "/ping" in joined
        assert "/help" in joined


class TestDispatch:
    async def test_dispatch_routes_label_to_handler(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        addr = "0x" + "a" * 40
        update = _update(f"/label {addr} moon test")
        await bot._dispatch(update)
        store = LabelStore.load(tmp_path / "labels.json")
        assert len(store.all()) == 1

    async def test_dispatch_routes_show_to_handler(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        addr = "0x" + "a" * 40
        reply_text: list[str] = []
        bot._reply = AsyncMock(side_effect=lambda _cid, text: reply_text.append(text))  # type: ignore[method-assign]
        await bot._dispatch(_update(f"/show {addr}"))
        # Either "no score" or actual score data — depends on storage state
        assert len(reply_text) == 1

    async def test_dispatch_unknown_command_replies(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        reply_text: list[str] = []
        bot._reply = AsyncMock(side_effect=lambda _cid, text: reply_text.append(text))  # type: ignore[method-assign]
        await bot._dispatch(_update("/garbage"))
        assert any("unknown" in t for t in reply_text)

    async def test_dispatch_non_command_message_ignored(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        reply_text: list[str] = []
        bot._reply = AsyncMock(side_effect=lambda _cid, text: reply_text.append(text))  # type: ignore[method-assign]
        await bot._dispatch(_update("hello there"))
        assert reply_text == []

    async def test_dispatch_strips_bot_username(self, tmp_path: Path) -> None:
        bot = _make_bot(tmp_path / "labels.json")
        addr = "0x" + "a" * 40
        await bot._dispatch(_update(f"/label@MyBonnetBot {addr} moon"))
        store = LabelStore.load(tmp_path / "labels.json")
        assert len(store.all()) == 1


class TestBotInit:
    def test_requires_token_and_chat_id(self) -> None:
        with pytest.raises(ValueError, match="bot_token and chat_id required"):
            TelegramBot(bot_token="", chat_id="123")
        with pytest.raises(ValueError, match="bot_token and chat_id required"):
            TelegramBot(bot_token="abc", chat_id="")
