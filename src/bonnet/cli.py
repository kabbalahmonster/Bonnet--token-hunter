"""CLI entry point for Bonnet.

Commands:
  scan         one-shot scan + score + optionally alert
  watch        continuous scan + alert (Ctrl-C to stop)
  history      show recent scores from the local DB
  watchlist    add / remove / list tracked tokens
  detect-factories  attempt to discover factory + event sig for a known pool

Run `bonnet --help` for full options.
"""
from __future__ import annotations

import asyncio
import json
import sys

import click

from . import __version__
from .logging import configure as configure_logging
from .pipeline import run_scan, watch_loop
from .settings import ensure_dirs, get_settings


def _print_score_table(scores: list, *, threshold: float | None = None) -> None:
    if not scores:
        click.echo("(no scores)")
        return
    click.echo(f"{'FLAG':<3} {'SYMBOL':<12} {'SCORE':>6} {'VOL24h':>10} {'CHG24h':>8}  ADDR")
    click.echo("-" * 70)
    for s in scores:
        p = s.pair
        vol24 = f"${p.volume_usd_24h:,.0f}" if p else "?"
        chg = f"{p.price_change_pct_24h:+.1f}%" if p else "?"
        flag = "🚩" if (threshold is not None and s.composite < threshold) else "  "
        sym = (s.token.symbol or "?")[:12]
        addr = s.token.short_address
        click.echo(f"{flag} {sym:<10} {s.composite:>6.3f} {vol24:>10} {chg:>8}  {addr}")


@click.group()
@click.version_option(version=__version__, prog_name="bonnet")
def cli() -> None:
    """Bonnet — token hunter for Robinhood Chain."""
    configure_logging()
    ensure_dirs()


@cli.command()
@click.option("--threshold", default=None, type=float, help="alert threshold (default from settings)")
@click.option("--top", "top_n", default=20, show_default=True, type=int)
@click.option("--no-enrich", is_flag=True, help="skip on-chain enrichment (faster, less accurate)")
@click.option("--dry-run-notify", is_flag=True, help="don't actually send telegram messages")
def scan(threshold: float | None, top_n: int, no_enrich: bool, dry_run_notify: bool) -> None:
    """One-shot scan of all Robinhood Chain pairs."""
    settings = get_settings()
    th = threshold if threshold is not None else settings.score_alert_threshold
    click.echo(f"Scanning Robinhood Chain... (threshold={th})")
    scores = asyncio.run(run_scan(
        settings=settings,
        top_n=top_n,
        enrich=not no_enrich,
        notify_threshold=th,
        dry_run_notify=dry_run_notify,
    ))
    _print_score_table(scores, threshold=th)


@cli.command(name="watch")
@click.option("--interval", default=15, show_default=True, type=int, help="minutes between scans")
@click.option("--threshold", default=None, type=float)
@click.option("--no-enrich", is_flag=True)
@click.option("--dry-run-notify", is_flag=True)
def watch(interval: int, threshold: float | None, no_enrich: bool, dry_run_notify: bool) -> None:
    """Continuously scan and alert (Ctrl-C to stop)."""
    settings = get_settings()
    th = threshold if threshold is not None else settings.score_alert_threshold
    click.echo(f"Watching every {interval}m; threshold={th}; Ctrl-C to stop")
    try:
        asyncio.run(watch_loop(
            interval_minutes=interval,
            notify_threshold=th,
            settings=settings,
            dry_run_notify=dry_run_notify,
        ))
    except KeyboardInterrupt:
        click.echo("\nbye")


@cli.command()
@click.option("--limit", default=20, show_default=True, type=int)
@click.option("--since-hours", default=None, type=float)
def history(limit: int, since_hours: float | None) -> None:
    """Show recent scores from local storage."""
    from .storage.sqlite import Storage
    settings = get_settings()
    click.echo(f"Reading {settings.db_path}")

    async def _go() -> list:
        async with Storage(settings.db_path) as s:
            return await s.top_scores(limit=limit, since_hours=since_hours)

    scores = asyncio.run(_go())
    click.echo(f"{len(scores)} scores")
    _print_score_table(scores)


@cli.group()
def watchlist() -> None:
    """Manage your manual watchlist."""


@watchlist.command(name="add")
@click.argument("address")
@click.option("--symbol", default="")
@click.option("--notes", default="")
def wl_add(address: str, symbol: str, notes: str) -> None:
    """Add a token to the watchlist."""
    from .models import Chain, Token
    from .storage.sqlite import Storage
    addr = address.lower()
    if not addr.startswith("0x") or len(addr) != 42:
        click.echo(f"invalid address: {address}", err=True)
        sys.exit(1)
    settings = get_settings()

    async def _go() -> None:
        async with Storage(settings.db_path) as s:
            t = Token(address=addr, chain=Chain.ROBINHOOD, symbol=symbol)
            await s.watchlist_add(t, notes=notes)

    asyncio.run(_go())
    click.echo(f"added {addr} ({symbol})")


@watchlist.command(name="rm")
@click.argument("address")
def wl_rm(address: str) -> None:
    """Remove a token from the watchlist."""
    from .storage.sqlite import Storage
    settings = get_settings()

    async def _go() -> None:
        async with Storage(settings.db_path) as s:
            await s.watchlist_remove(address.lower())

    asyncio.run(_go())
    click.echo(f"removed {address.lower()}")


@watchlist.command(name="list")
def wl_list() -> None:
    """List watchlist entries."""
    from .storage.sqlite import Storage
    settings = get_settings()

    async def _go() -> list:
        async with Storage(settings.db_path) as s:
            return await s.watchlist_list()

    rows = asyncio.run(_go())
    click.echo(f"{len(rows)} entries:")
    for addr, sym, added in rows:
        click.echo(f"  {added[:19]}  {addr}  {sym}")


@cli.command(name="detect-factories")
@click.argument("known_pool")
@click.option("--kind-hint", default="v3", show_default=True)
def detect_factories(known_pool: str, kind_hint: str) -> None:
    """Try to identify the factory + event sig for a known pool address.

    Useful when you want to add a new chain or DEX to Bonnet's factory scanner.
    Writes a JSON snippet to stdout that you can paste into BONNET_FACTORIES.
    """
    from .discovery.factory_scanner import FactoryScanner
    from .discovery.rpc_client import RpcClient
    settings = get_settings()
    rpc = RpcClient(settings)
    scanner = FactoryScanner(rpc, factories=[])

    async def _go():
        return await scanner.discover(known_pool, kind_hint=kind_hint)

    result = asyncio.run(_go())
    if result is None:
        click.echo("no factory detected (RPC may be rate-limited or pool not recent)", err=True)
        sys.exit(1)
    snippet = {
        "address": result.address,
        "chain": result.chain.value,
        "kind": result.kind,
        "event_signature": result.event_signature,
        "event_name": result.event_name,
        "pool_topic_index": result.pool_topic_index,
    }
    click.echo("# Paste this into BONNET_FACTORIES in your .env:")
    click.echo(json.dumps([snippet], indent=2))


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
