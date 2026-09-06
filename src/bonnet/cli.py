"""CLI entry point: `bonnet scan`, `bonnet watch`, etc."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone

import click

from . import __version__
from .discovery.dexscreener import DexScreenerClient
from .logging import configure as configure_logging
from .scoring.scorer import score_pair
from .settings import ensure_dirs, get_settings


async def _scan_once(threshold: float, top_n: int) -> int:
    settings = get_settings()
    async with DexScreenerClient() as dex:
        pairs = await dex.latest_pairs()

    scored = []
    for p in pairs:
        try:
            s = score_pair(p)
        except Exception:
            continue
        scored.append(s)

    scored.sort(key=lambda s: s.composite, reverse=True)
    top = scored[:top_n]

    click.echo(f"\n=== Bonnet scan @ {datetime.now(timezone.utc).isoformat()} ===")
    click.echo(f"scanned {len(pairs)} pairs on Robinhood Chain\n")
    click.echo(f"{'SYMBOL':<12} {'SCORE':>6} {'VOL24h':>10} {'CHG24h':>8}  ADDR")
    click.echo("-" * 70)
    for s in top:
        p = s.pair
        vol24 = f"${p.volume_usd_24h:,.0f}" if p else "?"
        chg = f"{p.price_change_pct_24h:+.1f}%" if p else "?"
        flag = "🚩" if s.composite < threshold else "  "
        sym = (s.token.symbol or "?")[:12]
        addr = s.token.short_address if s.token else ""
        click.echo(f"{flag} {sym:<10} {s.composite:>6.3f} {vol24:>10} {chg:>8}  {addr}")

    return 0


@click.group()
@click.version_option(version=__version__, prog_name="bonnet")
def cli() -> None:
    """Bonnet — token hunter for Robinhood Chain."""
    configure_logging()
    ensure_dirs()


@cli.command()
@click.option("--threshold", default=None, type=float, help="alert threshold (default from settings)")
@click.option("--top", "top_n", default=20, show_default=True, type=int, help="rows to print")
def scan(threshold: float | None, top_n: int) -> None:
    """One-shot scan of all Robinhood Chain pairs."""
    settings = get_settings()
    th = threshold if threshold is not None else settings.score_alert_threshold
    sys.exit(asyncio.run(_scan_once(th, top_n)))


@cli.command(name="watch")
@click.option("--interval", default=15, show_default=True, type=int, help="minutes between scans")
@click.option("--threshold", default=None, type=float)
def watch(interval: int, threshold: float | None) -> None:
    """Continuously scan and log candidates above threshold (Ctrl-C to stop)."""
    settings = get_settings()
    th = threshold if threshold is not None else settings.score_alert_threshold
    click.echo(f"Watching Robinhood Chain every {interval}m; alerting score >= {th}")

    async def loop() -> None:
        while True:
            try:
                await _scan_once(th, top_n=10)
            except Exception as e:
                click.echo(f"[error] {e}", err=True)
            await asyncio.sleep(interval * 60)

    try:
        asyncio.run(loop())
    except KeyboardInterrupt:
        click.echo("\nbye")


def main() -> None:
    cli()


if __name__ == "__main__":
    main()