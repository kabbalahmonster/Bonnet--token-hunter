"""SQLite-backed storage for Bonnet.

Three tables:
  - scores: every scored token, with composite + components (history)
  - watchlist: tokens Doom wants to track explicitly
  - alerts: dedup record so we don't re-alert on the same coin too often

Single-file DB; created on first access. Schema migrations are inline
CREATE IF NOT EXISTS so the file can be regenerated without code changes.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from ..logging import get_logger
from ..models import Score, Token

log = get_logger("bonnet.storage")


SCHEMA = """
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    composite REAL NOT NULL,
    volume_quality REAL NOT NULL,
    volatility_character REAL NOT NULL,
    rug_resistance REAL NOT NULL,
    rug_risk REAL NOT NULL,
    score_json TEXT NOT NULL,
    scored_at TEXT NOT NULL,
    UNIQUE(token_address, chain, scored_at)
);
CREATE INDEX IF NOT EXISTS idx_scores_token ON scores(token_address, chain);
CREATE INDEX IF NOT EXISTS idx_scores_composite ON scores(composite DESC);
CREATE INDEX IF NOT EXISTS idx_scores_scored_at ON scores(scored_at DESC);

CREATE TABLE IF NOT EXISTS watchlist (
    token_address TEXT PRIMARY KEY,
    chain TEXT NOT NULL,
    symbol TEXT NOT NULL DEFAULT '',
    added_at TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    composite REAL NOT NULL,
    alerted_at TEXT NOT NULL,
    delivery TEXT NOT NULL DEFAULT 'sent'
);
CREATE INDEX IF NOT EXISTS idx_alerts_token ON alerts(token_address, alerted_at DESC);
"""


class Storage:
    """Async SQLite store. Single connection per instance — call from one task at a time,
    or wrap calls in a lock if you need concurrency.
    """

    def __init__(self, db_path: Path):
        self._path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        log.info("storage_connected", path=str(self._path))

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def __aenter__(self) -> Storage:
        await self.connect()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _require(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage.connect() not called")
        return self._db

    # ---- scores ----------------------------------------------------------------

    async def record_score(self, score: Score) -> None:
        """Insert a score row. Same (token, chain, scored_at) won't double-insert."""
        db = self._require()
        score_dict = score.model_dump(mode="json")
        await db.execute(
            """
            INSERT OR REPLACE INTO scores (
                token_address, chain, symbol, composite,
                volume_quality, volatility_character, rug_resistance, rug_risk,
                score_json, scored_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                score.token.address,
                score.token.chain.value,
                score.token.symbol,
                score.composite,
                score.components.volume_quality,
                score.components.volatility_character,
                score.components.rug_resistance,
                score.rug_signals.rug_risk,
                json.dumps(score_dict),
                score.scored_at.isoformat(),
            ),
        )
        await db.commit()

    async def latest_score(self, address: str, chain: str = "robinhood") -> Score | None:
        """Most recent score for a token, or None."""
        db = self._require()
        cur = await db.execute(
            """
            SELECT score_json FROM scores
            WHERE token_address = ? AND chain = ?
            ORDER BY scored_at DESC LIMIT 1
            """,
            (address.lower(), chain),
        )
        row = await cur.fetchone()
        await cur.close()
        if not row:
            return None
        return Score.model_validate(json.loads(row[0]))

    async def top_scores(self, chain: str = "robinhood", limit: int = 20, since_hours: float | None = None) -> list[Score]:
        """Highest composite scores in the recent window."""
        db = self._require()
        if since_hours is not None:
            cur = await db.execute(
                """
                SELECT score_json FROM scores
                WHERE chain = ? AND scored_at >= datetime('now', ?)
                ORDER BY composite DESC LIMIT ?
                """,
                (chain, f"-{since_hours} hours", limit),
            )
        else:
            cur = await db.execute(
                """
                SELECT score_json FROM scores
                WHERE chain = ?
                ORDER BY composite DESC LIMIT ?
                """,
                (chain, limit),
            )
        rows = await cur.fetchall()
        await cur.close()
        return [Score.model_validate(json.loads(r[0])) for r in rows]

    # ---- watchlist -------------------------------------------------------------

    async def watchlist_add(self, token: Token, notes: str = "") -> None:
        db = self._require()
        await db.execute(
            """
            INSERT INTO watchlist (token_address, chain, symbol, added_at, notes)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(token_address) DO UPDATE SET notes = excluded.notes
            """,
            (
                token.address.lower(),
                token.chain.value,
                token.symbol,
                datetime.now(UTC).isoformat(),
                notes,
            ),
        )
        await db.commit()

    async def watchlist_remove(self, address: str) -> None:
        db = self._require()
        await db.execute("DELETE FROM watchlist WHERE token_address = ?", (address.lower(),))
        await db.commit()

    async def watchlist_list(self) -> list[tuple[str, str, str]]:
        """Returns [(address, symbol, added_at)]."""
        db = self._require()
        cur = await db.execute("SELECT token_address, symbol, added_at FROM watchlist ORDER BY added_at DESC")
        rows = await cur.fetchall()
        await cur.close()
        return [(r[0], r[1], r[2]) for r in rows]

    # ---- alerts ----------------------------------------------------------------

    async def should_alert(self, address: str, cooldown_hours: float = 6.0) -> bool:
        """True if we haven't alerted about this token in `cooldown_hours`."""
        db = self._require()
        cur = await db.execute(
            """
            SELECT COUNT(*) FROM alerts
            WHERE token_address = ?
              AND alerted_at >= datetime('now', ?)
            """,
            (address.lower(), f"-{cooldown_hours} hours"),
        )
        row = await cur.fetchone()
        await cur.close()
        return (row[0] if row else 0) == 0

    async def record_alert(self, address: str, composite: float) -> None:
        db = self._require()
        await db.execute(
            """
            INSERT INTO alerts (token_address, chain, composite, alerted_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                address.lower(),
                "robinhood",
                composite,
                datetime.now(UTC).isoformat(),
            ),
        )
        await db.commit()


__all__ = ["Storage"]
