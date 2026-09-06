# Changelog

All notable changes to Bonnet.

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