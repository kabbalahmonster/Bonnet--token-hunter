# Bonnet — Token Hunter for Robinhood Chain

Bonnet is a coin discovery and scoring engine for **Robinhood Chain** (chain ID 4663). It hunts new and existing tokens, scores each on three explainable axes (volume quality, volatility character, rug resistance), and alerts you when something crosses your threshold.

The engine is independent of every surface — Telegram alerts, a future web dashboard, cron jobs, or your own scripts can all consume the same scoring pipeline.

---

## What it does

- **Discovers** tokens via two sources:
  - **DexScreener** (free, 300 req/min) — pairs by search query and per-token enrichment (volume, liquidity, txns, price changes).
  - **On-chain factory scanner** — listens for `PoolCreated` / `PairCreated` events from one or more factory contracts. Configurable per-chain via `.env`.
- **Enriches** each candidate on-chain (when reachable):
  - Contract code presence (`eth_getCode`)
  - `owner()` / `getOwner()` calls to detect renounced ownership
  - EIP-1967 proxy detection via storage slot probe
  - **Holder concentration** (optional, `--holders`): scans up to 50k blocks of Transfer logs per token, replays a balance ledger, computes top-1 and top-10 holder percentages against `totalSupply()`
  - **LP lock detection** (optional, `--lp-check`): reads pair's `totalSupply()` and `balanceOf(0x..dead)`, plus `owner()` to detect known locker contracts or dead-address renouncement
  - Honeypot simulation: simulate a token transfer and detect abnormal tax (or reverts)
- **Scores** each token with an explainable composite:
  - **Volume quality** — sustained volume, liquidity ratio penalty (log scale)
  - **Volatility character** — bell-curve preference for 20–40% daily moves; direction penalty for rugs
  - **Rug resistance** — holder concentration, contract verification, ownership, LP lock status, age, honeypot signals
  - Weighted: 30% volume + 30% volatility + 40% rug resistance
- **Persists** every score to SQLite for history, dedup, and watchlists
- **Alerts** via Telegram when a candidate crosses your threshold (with cooldown dedup)

---

## Architecture

```
bonnet/
├── discovery/        # data sources (DexScreener, on-chain factory scanner)
├── metrics/          # volume / volatility scoring functions
├── rug/              # rug-resistance heuristics aggregator
├── scoring/          # composite scorer with explainable components
├── onchain/          # contract enrichment + honeypot simulator
├── notify/           # alert channels (Telegram today, pluggable)
├── storage/          # SQLite for scores, watchlists, alert dedup
├── pipeline.py       # orchestrator (discover → enrich → score → alert)
├── settings.py       # pydantic-settings, .env loader
├── logging.py        # structlog config
└── cli.py            # `bonnet scan`, `bonnet watch`, etc.
```

Each layer is independently usable. Want to score a single token you found manually? `from bonnet.scoring import score_pair; from bonnet.onchain import ContractEnricher`. Want to send alerts via Discord later? Implement a notifier in `notify/` and wire it into `pipeline.py`. The engine never assumes the surface.

---

## Quickstart

### Prerequisites

- **Python 3.11+**
- **Robinhood Chain RPC access** — the public RPC `https://rpc.mainnet.chain.robinhood.com` works but is rate-limited. For production scanning add a paid endpoint (Alchemy, QuickNode, or another provider) to `BONNET_RPC_ENDPOINTS` (comma-separated, tried in order with automatic failover).
- *(Optional)* A **Telegram bot token** for alerts. Create via [@BotFather](https://t.me/BotFather). Do NOT reuse your hermes trading bot token — keep them separate.

### Install

```bash
git clone https://github.com/kabbalahmonster/Bonnet--token-hunter.git
cd Bonnet--token-hunter

python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"           # core + dev tools (pytest, ruff, mypy)
pip install -e ".[dev,telegram]"  # also install python-telegram-bot (currently not needed; kept for future async bot UI)
```

Copy the env template and edit:

```bash
cp .env.example .env
$EDITOR .env
```

Required edits:
- `BONNET_RPC_ENDPOINTS` — at minimum the canonical Robinhood Chain RPC. Add a paid provider for production.
- `BONNET_TELEGRAM_BOT_TOKEN` and `BONNET_TELEGRAM_CHAT_ID` — for alerts. If left empty, scans still work but alerts go to logs only.

Verify the install:

```bash
bonnet --help
pytest -q
```

You should see the help text and all 34 tests passing.

### First scan

```bash
bonnet scan --top 10
```

This fetches the most active Robinhood Chain pairs from DexScreener, scores them with on-chain enrichment (owner renounced, honeypot check), records every score to `./state/bonnet.db`, and prints a table. The threshold flag (🚩) marks tokens below your alert threshold.

Output looks like:

```
=== scan ===
   SYMBOL       SCORE    VOL24h    CHG24h  ADDR
----------------------------------------------------------------------
   ROBINHOOD    0.655    $8,479    -29.3%  0x90a7…e2f9
🚩 ROBINHOOD    0.569    $1,663     -8.7%  0x3529…40cc
🚩 Robinhood    0.520   $12,158    +91.5%  0xf56d…8b07
🚩 HOOD         0.513      $332     +3.5%  0x45c8…0962
🚩 ROBINHOOD    0.465      $193   -11.9%  0x5d8c…1961
```

To skip on-chain enrichment (faster, less accurate):

```bash
bonnet scan --no-enrich --top 10
```

### Continuous watching + alerts

```bash
bonnet watch --interval 15   # scan every 15 minutes
```

Telegram alerts fire automatically when a token's composite score crosses `BONNET_SCORE_ALERT_THRESHOLD` (default 0.65). Each token alerts at most once per cooldown window (default 6 hours) to avoid spam.

Test alerts without spamming your chat:

```bash
bonnet watch --dry-run-notify --interval 5
```

---

## CLI reference

```
bonnet scan [--threshold F] [--top N] [--no-enrich] [--holders] [--lp-check] [--dry-run-notify]
bonnet watch [--interval MIN] [--threshold F] [--no-enrich] [--holders] [--lp-check] [--dry-run-notify]
bonnet history [--limit N] [--since-hours H]
bonnet show <address> [--chain C] [--with-history] [--limit N]
bonnet backtest <labels.json> [--threshold F] [--weight-volume V] [--weight-volatility V] [--weight-rug R]
bonnet watchlist add <address> [--symbol S] [--notes "..."]
bonnet watchlist rm <address>
bonnet watchlist list
bonnet watchlist score [--threshold F] [--dry-run-notify]
bonnet detect-factories <known_pool_address> [--kind-hint v3]
bonnet --help
```

### `detect-factories` — adding a new DEX

If you want Bonnet to discover new pairs directly from a factory contract (instead of relying on DexScreener's search index), and you don't already know the factory address + event signature:

```bash
bonnet detect-factories 0x8aac0c4c9236096aa79262b0a53a683979ed8c7a
```

Output is a JSON snippet you paste into `.env` as `BONNET_FACTORIES`:

```json
[
  {
    "address": "0x...",
    "chain": "robinhood",
    "kind": "v3",
    "event_signature": "0x...",
    "event_name": "PoolCreated",
    "pool_topic_index": 3
  }
]
```

The factory scanner then queries `eth_getLogs` filtered on that factory + topic signature across the block range you specify. **Note**: this scanner relies on a fast RPC. Public Robinhood RPC is rate-limited and may be too slow for production use — pair it with a paid endpoint.

---

## Scoring in detail

The composite score is `0.30 * volume_quality + 0.30 * volatility_character + 0.40 * rug_resistance`, clamped to `[0, 1]`. Every component is independently inspectable in `Score.explanation`:

```python
from bonnet.scoring import score_pair
from bonnet.discovery.dexscreener import DexScreenerClient

async def my_score():
    async with DexScreenerClient() as dex:
        pairs = await dex.token_pairs("0x...")
        for p in pairs:
            s = score_pair(p, mint_renounced=True, lp_locked=True, contract_age_hours=24*30)
            print(s.composite, s.explanation)
```

**Tunable knobs** (in `scoring/scorer.py` and `rug/__init__.py`):
- `DEFAULT_WEIGHTS` — change the volume/volatility/rug weight split
- `volume_quality` — log-scale baseline ($1k→0.0, $1M→0.5, $10M→1.0); sustainedness penalty; thin-book penalty
- `volatility_character` — bell curve peaks at 20–40% daily move, smaller bell at 3–8% 1h; one-way downside penalty
- `rug.aggregate` — each heuristic capped, summed, clamped

When **no rug signals are known at all**, the rug-resistance defaults to **0.5** (pessimistic mid). Once you feed in real signals (`mint_renounced=True`, `lp_locked=True`, etc.) it moves up to ~0.85–1.0. **Unknown ≠ safe.**

---

## Configuration reference

All settings are loaded from environment variables prefixed `BONNET_`. See `.env.example` for the full list. Key ones:

| Variable | Default | Purpose |
|---|---|---|
| `BONNET_RPC_ENDPOINTS` | `https://mainnet.rpc.robinhood.com` | Comma-separated. Tried in order, automatic failover with cooldown. |
| `BONNET_DEXSCREENER_BASE_URL` | `https://api.dexscreener.com/latest` | DexScreener base URL |
| `BONNET_FACTORIES` | (empty) | JSON array of factory contracts to scan |
| `BONNET_SCORE_ALERT_THRESHOLD` | `0.65` | Composite score above which an alert fires |
| `BONNET_VOLUME_MIN_USD_24H` | `5000` | Pairs below this volume are skipped in `volume_quality` |
| `BONNET_RUG_MAX_TOP10_HOLDER_PCT` | `40.0` | Threshold for holder-concentration penalty |
| `BONNET_TELEGRAM_BOT_TOKEN` | (empty) | Create via @BotFather |
| `BONNET_TELEGRAM_CHAT_ID` | (empty) | Numeric chat ID, or `@channel_name` |
| `BONNET_DB_PATH` | `./state/bonnet.db` | SQLite file location |
| `BONNET_LOG_LEVEL` | `INFO` | DEBUG / INFO / WARNING / ERROR |

---

## Backtesting

Once you have a labeled set of tokens (good / moon / rug), validate your scoring weights against history:

1. Create `data/labels.json`:
   ```json
   [
     {"address": "0x...", "symbol": "GOOD", "label": "good", "notes": "sustained"},
     {"address": "0x...", "symbol": "MOON", "label": "moon", "notes": "10x winner"},
     {"address": "0x...", "symbol": "RUG",  "label": "rug",  "notes": "scammed"}
   ]
   ```
2. Run:
   ```bash
   bonnet backtest data/labels.json --threshold 0.65
   ```
3. Output is a confusion matrix (good/moon vs rug), mean score per label, rug recall, good precision, and per-token scores.

Useful for tuning `DEFAULT_WEIGHTS` in `scoring/scorer.py`. With at least 5 good/moon and 5 rug examples, you can start seeing whether your weights separate them well.

---

## Deploying as a service

The repo ships with a `bonnet.service` template you can drop into `~/.config/systemd/user/` for a user-mode systemd unit. After editing the paths:

```bash
systemctl --user daemon-reload
systemctl --user enable --now bonnet.service
systemctl --user status bonnet.service
journalctl --user -u bonnet.service -f   # follow logs
```

This runs `bonnet watch --interval 15` as a managed service that restarts on crash. To survive reboots, enable lingering once: `sudo loginctl enable-linger $USER`.

> **Isolation note**: Bonnet lives at `/home/fuzzbox/projects/coin-analyzer/` with its own venv, its own DB, its own systemd unit. It does not touch any other agent's files, ports, or services. The Telegram bot token is separate from any other bot's token.

### Scheduled scans (timer-based, recommended for production)

Long-running `bonnet watch` is convenient but harder to monitor. A systemd timer is cleaner:

```bash
# Edit contrib/bonnet-scan.service and .timer to point at your install dir
cp contrib/bonnet-scan.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now bonnet-scan.timer
systemctl --user list-timers bonnet-scan.timer   # check schedule
journalctl --user -u bonnet-scan.service -f     # follow logs
```

The timer runs `bonnet scan` every 5 minutes with a 30s random delay (so multiple instances don't thunder the RPC). Each run is a fresh process — no orphans, easy to monitor, easy to roll back.

---

## Development

```bash
source .venv/bin/activate
pytest -q                  # 34 tests, ~0.3s
ruff check src tests       # lint
mypy src/bonnet            # type check
```

The scorer is the most-tested piece — if you change the weights or heuristics, add a fixture in `tests/fixtures.py` and an assertion in `tests/test_scorer.py`. The point is to make behavior changes visible.

**Adding a new rug signal**:
1. Add the field to `models.RugSignals`
2. Add the penalty function to `rug/__init__.py`
3. Wire it into `aggregate()`
4. Add a test in `tests/test_rug.py`

**Adding a new alert channel**:
1. Implement `Notifier` protocol in `notify/<channel>.py` (look at `TelegramNotifier`)
2. Add settings to `settings.py`
3. Wire into `pipeline.run_scan`

**Adding a new discovery source**:
1. Implement an async iterator/factory in `discovery/<source>.py`
2. Update `pipeline.run_scan` to merge its pairs with DexScreener's

---

## Known limitations & roadmap

- **Mint authority (AccessControl)** ✅ shipped in v0.4.0 — `getRoleMemberCount(MINTER_ROLE)` probe; if 0, mint authority is effectively renounced.
- **Trend detection + breakout alerts** ✅ shipped in v0.4.0 — `compute_trend()` compares current score to last 5 samples; `should_alert_with_trend()` fires on `above_threshold`, `breakout`, or `rising_fast` (delta > 0.10 + 25%).
- **`bonnet show <addr>`** ✅ shipped in v0.4.0 — drill into one token's full score breakdown + history + trend.
- **`bonnet watchlist score`** ✅ shipped in v0.4.0 — score just watchlist entries (faster than full scan, useful for high-frequency tracking).
- **GitHub Actions CI** ✅ shipped in v0.4.0 — matrix test on Python 3.11/3.12/3.13, ruff, import smoke test.
- **Holder concentration** ✅ shipped in v0.3.0 (`--holders` flag, opt-in due to RPC cost)
- **LP lock detection** ✅ shipped in v0.3.0 (`--lp-check` flag)
- **Backtest mode** ✅ shipped in v0.3.0 (`bonnet backtest data/labels.json`)
- **Systemd timer** ✅ shipped in v0.3.0 (`contrib/bonnet-scan.{service,timer}`)
- **On-chain factory discovery is best-effort** for non-canonical chains. Public Robinhood RPC is rate-limited and slow for log scanning; production deployments should use a paid RPC.
- **Web dashboard** — deliberately deferred. The CLI + Telegram path is sufficient for v1.
- **Labeled dataset growth** — backtest is only as good as the labels. Recommend labeling 20+ tokens (mix of good/moon/rug) and re-running to validate weight choices.
- **Multi-source factory config** — once you've identified Robinhood Chain's specific factory contracts, populate `BONNET_FACTORIES` in `.env` to discover new pairs without rate-limited search APIs.

---

## License

TBD (MIT recommended for open-sourcing once v1.0 is tagged).

---

## Repository

https://github.com/kabbalahmonster/Bonnet--token-hunter