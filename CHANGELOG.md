# Changelog

All notable changes to Bonnet.

## [0.4.0] — Sprint 4

### Added
- **Mint authority (AccessControl) probing** (`bonnet/onchain/enricher.py`): probes `getRoleMemberCount(MINTER_ROLE)` and `DEFAULT_ADMIN_ROLE` (canonical OZ role hashes). When AccessControl is detected, mint-authority state is derived from minter count rather than owner().
- **Trend detection** (`bonnet/trend.py`): `compute_trend()` compares current composite to N samples back, returns direction (rising/falling/flat), delta, pct_change, and detects breakout/breakdown across the alert threshold.
- **Trend-aware alerts**: `should_alert_with_trend()` fires on three reasons: `above_threshold` (static), `breakout` (just crossed up), `rising_fast` (delta > 0.10 + 25%). The pipeline now alerts on breakout/rising_fast in addition to static threshold.
- **`bonnet show <address>`**: drill into one token — composite, components, rug signals, full explanation, history, trend. Supports `--with-history` for trend context.
- **`bonnet watchlist score`**: score just watchlist entries (skips DexScreener discovery). Fast for high-frequency tracking.
- **GitHub Actions CI** (`.github/workflows/ci.yml`): matrix test on Python 3.11/3.12/3.13, ruff lint, import smoke test, type check.
- **Deployment verified on this VPS**: `bonnet-scan.service` + `bonnet-scan.timer` installed via systemd, `--user` mode, scheduled 5-min scans.

### Changed
- **Pipeline alerts now use trend**: instead of static threshold alone, alerts include the trend reason (`above_threshold`, `breakout`, `rising_fast`). Telegram messages for breakout/rising_fast include a trend header line.
- **`score_history()` storage method**: new method to fetch chronological score history for a token (used by trend detection).

### Tests
- 19 new tests (trend directions, breakout detection, alert decision logic, role selector encoding, score history retrieval).

## [0.3.0] — Sprint 3

### Added
- **Holder concentration analysis** (`bonnet/onchain/holders.py`): scans up to 50k blocks of Transfer logs per token, replays a balance ledger from zero, reads `totalSupply()` for the percentage denominator, and returns top-1 / top-10 holder percentages. New `--holders` flag on `scan` and `watch`.
- **LP lock detection** (`bonnet/onchain/lp_lock.py`): reads pair's `totalSupply()`, `balanceOf(0x..dead)`, and `owner()`. If ≥50% LP is held by the dead address (a common "permanent lock" pattern), flags `lp_locked=True`. New `--lp-check` flag on `scan` and `watch`.
- **Backtest mode** (`bonnet/backtest.py`): `bonnet backtest data/labels.json` scores a JSON array of labeled tokens (good / moon / rug) and reports a confusion matrix, mean score per label, rug recall, good precision. Use this to tune `DEFAULT_WEIGHTS`.
- **Sample labels** (`data/sample-labels.json`): one labeled Robinhood Names token to bootstrap backtest usage.
- **Systemd timer** (`contrib/bonnet-scan.{service,timer}`): cron-style scheduling that runs `bonnet scan` every 5 min via systemd rather than a long-running process. Cleaner to monitor, no orphans.
- **12 new tests** (holder helpers, LP helpers, backtest loader + formatter).

### Changed
- **`_decode_uint_from_word` bug fix**: previously multiplied offset by `2` (bytes) instead of `64` (hex chars per word), making all holder-balance calculations wrong. **Caught by tests.** Holder concentration numbers from sprint 2 were incorrect; this is now correct.
- **v4 pool IDs handled gracefully** in all on-chain enrichers: `eth_call` to 64-hex addresses used to throw RPC errors; now skipped silently with a log line.
- **Backtest fallback**: uses DexScreener search when per-address lookup returns 404.

## [0.2.0] — Sprint 2

### Added
- **SQLite storage** (`bonnet/storage/`): scores history, watchlist, alert-dedup cooldown
- **On-chain enrichment** (`bonnet/onchain/enricher.py`): `eth_getCode`, `owner()`, EIP-1967 proxy detection
- **Honeypot simulator** (`bonnet/onchain/honeypot.py`): simulates a token transfer to detect non-standard tax or reverts
- **On-chain factory scanner** (`bonnet/discovery/factory_scanner.py`): config-driven scanner that listens for `PoolCreated`/`PairCreated` events from one or more factory contracts. Includes `bonnet detect-factories <pool>` helper for discovering the right factory + event signature for a chain.
- **Telegram notifier** (`bonnet/notify/telegram.py`): raw HTTP to Bot API (no extra deps). `bonnet scan` / `bonnet watch` send alerts on candidates above threshold with cooldown dedup.
- **Pipeline orchestrator** (`bonnet/pipeline.py`): ties discovery → enrichment → scoring → alerts together. Single entry point `run_scan()` and `watch_loop()`.
- **CLI commands**: `scan`, `watch`, `history`, `watchlist add/rm/list`, `detect-factories`
- **`eth_getStorageAt`** added to RpcClient for EIP-1967 slot reads
- **Settings**: `factories_json`, `telegram_bot_token`, `telegram_chat_id`
- **Systemd unit template**: `contrib/bonnet.service`
- **12 new tests** (storage roundtrip, alert dedup, telegram formatter, honeypot simulator)
- **Comprehensive README**: architecture, setup, CLI reference, configuration, deployment, development, roadmap

### Changed
- **CLI rewritten** to use the pipeline orchestrator instead of inline scanning logic
- **Settings**: added `factories_json`, `telegram_bot_token`, `telegram_chat_id`
- **`Pair.pair_address` validator** now accepts both 40-hex (Uniswap v2/v3) and 64-hex (Uniswap v4 pool ID) addresses
- **`score_pair`**: unknown rug signals now default to `rug_resistance=0.5` (pessimistic), not `~0.95` (the prior bug)

### Removed
- `web3` dependency — dropped in favor of raw `httpx` for JSON-RPC calls. Saves ~30MB of native deps.

## [0.1.0] — Sprint 1

### Added
- Initial package structure: `discovery/`, `metrics/`, `rug/`, `scoring/`
- DexScreener client with rate limiting and retry
- Multi-endpoint JSON-RPC client with per-endpoint circuit breaker
- Volume quality scorer (log-scale baseline, sustainedness, liquidity ratio)
- Volatility character scorer (bell curve, direction penalty)
- Rug heuristics aggregator (holder concentration, age, authority, LP lock, honeypot, deployer, proxy)
- Composite scorer with explainable components
- 22 unit tests covering metrics, scorer, rug aggregation, settings
- `bonnet scan` and `bonnet watch` CLI