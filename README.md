# Bonnet — Token Hunter

Coin discovery and scoring engine for **Robinhood Chain**. Hunts new and existing tokens by volume, volatility, and rug-resistance, then alerts on candidates that match.

## What it does

- **Discover** new pairs and recently-active tokens on Robinhood Chain via DexScreener + on-chain RPC.
- **Score** each token on three explainable axes:
  - *Volume quality* — sustained liquidity, not one-day spikes
  - *Volatility character* — frequent swings, but bounded (no one-way rugs)
  - *Rug resistance* — contract verification, holder concentration, LP lock, mint/freeze authority, deployer history
- **Alert** via Telegram when a candidate crosses your threshold.

## Architecture

```
bonnet/
├── discovery/   # data sources: DexScreener + RPC pair events
├── metrics/     # volume / volatility / holder-concentration calculators
├── rug/         # rug-resistance heuristics
├── scoring/     # composite score with explainable components
├── notify/      # telegram alerts (pluggable)
├── storage/     # sqlite for watchlists + score history
└── cli.py       # `bonnet scan`, `bonnet watch`, `bonnet backfill`
```

Engine is independent of all surfaces. Telegram alerts, dashboards, and cron are thin wrappers around the core.

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev,telegram]

# copy and edit secrets
cp .env.example .env

# one-shot scan (top 50 by volume on Robinhood Chain)
bonnet scan

# continuous watch + alerts
bonnet watch --interval 15m
```

## Status

Early development. Scoring weights are calibrated on a small hand-labeled set and will iterate.

## License

TBD