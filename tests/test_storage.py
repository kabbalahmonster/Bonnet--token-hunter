"""Tests for Storage layer (SQLite)."""
from __future__ import annotations

from pathlib import Path

import pytest_asyncio

from bonnet.models import Chain, Token
from bonnet.scoring.scorer import score_pair
from bonnet.storage.sqlite import Storage

from . import fixtures as fx


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.db")
    await s.connect()
    yield s
    await s.close()


class TestStorage:
    async def test_record_and_retrieve_latest(self, store: Storage) -> None:
        pair = fx.good_volume_steady()
        score = score_pair(pair, mint_renounced=True, lp_locked=True, contract_age_hours=14 * 24)
        await store.record_score(score)

        latest = await store.latest_score(pair.token.address)
        assert latest is not None
        assert latest.composite == score.composite
        assert latest.token.address == score.token.address

    async def test_top_scores_orders_by_composite(self, store: Storage) -> None:
        # Insert a high and low score for different tokens
        good = score_pair(fx.good_volume_steady(), mint_renounced=True, lp_locked=True)
        dead = score_pair(fx.dead_coin(), contract_age_hours=60 * 24)
        await store.record_score(good)
        await store.record_score(dead)

        top = await store.top_scores(limit=10)
        assert len(top) >= 2
        assert top[0].composite >= top[1].composite
        assert top[0].token.symbol == "GOOD"

    async def test_alert_dedup_cooldown(self, store: Storage) -> None:
        addr = fx.good_volume_steady().token.address
        # Initially: should alert
        assert await store.should_alert(addr) is True
        # Record an alert
        await store.record_alert(addr, 0.8)
        # Within cooldown: should NOT alert
        assert await store.should_alert(addr, cooldown_hours=6.0) is False

    async def test_watchlist_add_remove_list(self, store: Storage) -> None:
        t = Token(address="0x" + "f" * 40, chain=Chain.ROBINHOOD, symbol="WATCH")
        await store.watchlist_add(t, notes="manual add")
        rows = await store.watchlist_list()
        assert any(r[0] == t.address for r in rows)

        await store.watchlist_remove(t.address)
        rows = await store.watchlist_list()
        assert not any(r[0] == t.address for r in rows)
