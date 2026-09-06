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
from datetime import UTC
from pathlib import Path

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
@click.option("--holders", is_flag=True, help="run holder-concentration analysis (expensive)")
@click.option("--lp-check", is_flag=True, help="probe LP lock status per pair")
@click.option("--dry-run-notify", is_flag=True, help="don't actually send telegram messages")
def scan(
    threshold: float | None,
    top_n: int,
    no_enrich: bool,
    holders: bool,
    lp_check: bool,
    dry_run_notify: bool,
) -> None:
    """One-shot scan of all Robinhood Chain pairs."""
    settings = get_settings()
    th = threshold if threshold is not None else settings.score_alert_threshold
    click.echo(f"Scanning Robinhood Chain... (threshold={th})")
    if holders:
        click.echo("  holder concentration: ENABLED (will be slow)")
    if lp_check:
        click.echo("  LP lock check: ENABLED")
    scores = asyncio.run(run_scan(
        settings=settings,
        top_n=top_n,
        enrich=not no_enrich,
        holders=holders,
        lp_check=lp_check,
        notify_threshold=th,
        dry_run_notify=dry_run_notify,
    ))
    _print_score_table(scores, threshold=th)


@cli.command(name="watch")
@click.option("--interval", default=15, show_default=True, type=int, help="minutes between scans")
@click.option("--threshold", default=None, type=float)
@click.option("--no-enrich", is_flag=True)
@click.option("--holders", is_flag=True)
@click.option("--lp-check", is_flag=True)
@click.option("--dry-run-notify", is_flag=True)
def watch(
    interval: int,
    threshold: float | None,
    no_enrich: bool,
    holders: bool,
    lp_check: bool,
    dry_run_notify: bool,
) -> None:
    """Continuously scan and alert (Ctrl-C to stop)."""
    settings = get_settings()
    th = threshold if threshold is not None else settings.score_alert_threshold
    click.echo(f"Watching every {interval}m; threshold={th}; Ctrl-C to stop")
    if holders:
        click.echo("  holder concentration: ENABLED (slow)")
    if lp_check:
        click.echo("  LP lock check: ENABLED")
    try:
        asyncio.run(watch_loop(
            interval_minutes=interval,
            notify_threshold=th,
            settings=settings,
            dry_run_notify=dry_run_notify,
            holders=holders,
            lp_check=lp_check,
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


@watchlist.command(name="score")
@click.option("--threshold", default=None, type=float)
@click.option("--dry-run-notify", is_flag=True)
def wl_score(threshold: float | None, dry_run_notify: bool) -> None:
    """Score every watchlist entry (faster than full scan).

    Useful when you want frequent updates on tokens you're already tracking.
    """
    from .pipeline import run_scan_for_addresses
    from .storage.sqlite import Storage
    settings = get_settings()
    th = threshold if threshold is not None else settings.score_alert_threshold

    async def _go() -> list:
        async with Storage(settings.db_path) as s:
            rows = await s.watchlist_list()
        return [addr for addr, _, _ in rows]

    addrs = asyncio.run(_go())
    if not addrs:
        click.echo("watchlist is empty — add tokens with `bonnet watchlist add`")
        return

    click.echo(f"Scoring {len(addrs)} watchlist tokens…")
    scores = asyncio.run(run_scan_for_addresses(
        addrs,
        settings=settings,
        notify_threshold=th,
        dry_run_notify=dry_run_notify,
    ))
    if not scores:
        click.echo("(no scores)")
        return
    click.echo(f"{'FLAG':<3} {'SYMBOL':<12} {'SCORE':>6}  ADDR")
    click.echo("-" * 50)
    for s in scores:
        flag = "🚩" if s.composite < th else "  "
        sym = (s.token.symbol or "?")[:12]
        click.echo(f"{flag} {sym:<10} {s.composite:>6.3f}  {s.token.short_address}")


@cli.command(name="show")
@click.argument("address")
@click.option("--chain", default="robinhood", show_default=True)
@click.option("--with-history", is_flag=True, help="show last 10 scores and trend")
@click.option("--limit", default=10, show_default=True, type=int)
def show_cmd(address: str, chain: str, with_history: bool, limit: int) -> None:
    """Show detailed scoring breakdown for a single token."""
    from .storage.sqlite import Storage
    from .trend import compute_trend

    addr = address.lower()
    settings = get_settings()

    async def _go() -> tuple:
        async with Storage(settings.db_path) as s:
            latest = await s.latest_score(addr, chain)
            history = await s.score_history(addr, chain, limit=limit) if with_history else []
        return latest, history

    latest, history = asyncio.run(_go())

    if latest is None:
        click.echo(f"no score recorded for {addr[:10]}… — run `bonnet scan` first")
        sys.exit(1)

    click.echo(f"=== {latest.token.symbol or '?'} ({latest.token.short_address}) ===")
    click.echo(f"chain:        {chain}")
    click.echo(f"composite:    {latest.composite:.3f}")
    click.echo(f"  volume:     {latest.components.volume_quality:.3f}")
    click.echo(f"  volatility: {latest.components.volatility_character:.3f}")
    click.echo(f"  rug_resist: {latest.components.rug_resistance:.3f}")
    click.echo(f"rug_risk:     {latest.rug_signals.rug_risk:.3f}")
    click.echo(f"scored_at:    {latest.scored_at.isoformat()}")
    if latest.rug_signals.notes:
        click.echo("rug notes:")
        for note in latest.rug_signals.notes:
            click.echo(f"  • {note}")
    click.echo("explanation:")
    for line in latest.explanation:
        click.echo(f"  {line}")
    if with_history and len(history) > 1:
        trend = compute_trend(history)
        click.echo(
            f"\ntrend: {trend.direction} "
            f"(Δ={trend.delta:+.3f}, {trend.pct_change:+.1f}%) "
            f"over {trend.samples_used} samples"
        )
        if trend.is_breakout:
            click.echo("  ⚡ breakout — just crossed the alert threshold upward")
        if trend.is_breakdown:
            click.echo("  ⚠ breakdown — just crossed the alert threshold downward")


@cli.command(name="backtest")
@click.option(
    "--labels-file",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Path to labels.json (default: data/labels.json)",
)
@click.option("--threshold", default=0.65, show_default=True, type=float)
@click.option("--weight-volume", default=0.30, show_default=True, type=float)
@click.option("--weight-volatility", default=0.30, show_default=True, type=float)
@click.option("--weight-rug", default=0.40, show_default=True, type=float)
def backtest_cmd(
    labels_file: Path | None,
    threshold: float,
    weight_volume: float,
    weight_volatility: float,
    weight_rug: float,
) -> None:
    """Score labeled tokens and report separation between good/moon/rug.

    Labels are loaded from data/labels.json (or --labels-file). Populate
    via `bonnet label`, `bonnet label-import`, or `bonnet label-auto`.
    """
    from .backtest import format_summary, run_backtest
    from .settings import get_settings

    settings = get_settings()
    weights = {"volume": weight_volume, "volatility": weight_volatility, "rug": weight_rug}
    labels_path = labels_file or Path("data/labels.json")
    if not labels_path.exists():
        click.echo(
            f"labels file not found: {labels_path}\n"
            "Run `bonnet label <addr> good|moon|rug`, `bonnet label-import <file>`, "
            "or `bonnet label-auto` to populate.",
            err=True,
        )
        sys.exit(1)
    summary = asyncio.run(run_backtest(
        labels_path,
        settings=settings,
        threshold=threshold,
        weights=weights,
    ))
    click.echo(format_summary(summary))


@cli.command(name="label")
@click.argument("address")
@click.argument("label_value", metavar="LABEL")
@click.option("--symbol", default="")
@click.option("--notes", default="")
def label_cmd(address: str, label_value: str, symbol: str, notes: str) -> None:
    """Manually label a token (good/moon/rug). Persisted to data/labels.json."""
    from datetime import datetime

    from .labels import VALID_LABELS, Label, LabelStore

    addr = address.lower()
    if not addr.startswith("0x") or len(addr) != 42:
        click.echo(f"invalid address: {address}", err=True)
        sys.exit(1)
    if label_value not in VALID_LABELS:
        click.echo(f"label must be one of {VALID_LABELS}, got {label_value!r}", err=True)
        sys.exit(1)

    label = Label(
        address=addr,
        label=label_value,
        symbol=symbol,
        notes=notes,
        source="manual",
        confidence=1.0,
        labeled_at=datetime.now(UTC).isoformat(),
    )
    store = LabelStore.load(Path("data/labels.json"))
    is_new = store.add(label)
    store.save()
    verb = "added" if is_new else "updated"
    click.echo(f"{verb} {addr} ({symbol}) = {label_value}")


@cli.command(name="label-list")
@click.option("--filter-label", default=None, metavar="LABEL", help="filter by good/moon/rug")
def label_list(filter_label: str | None) -> None:
    """List all labeled tokens."""
    from .labels import VALID_LABELS, LabelStore

    if filter_label and filter_label not in VALID_LABELS:
        click.echo(f"filter must be one of {VALID_LABELS}", err=True)
        sys.exit(1)

    store = LabelStore.load(Path("data/labels.json"))
    rows = store.filter(filter_label) if filter_label else store.all()
    click.echo(f"{len(rows)} labels:")
    for lbl in sorted(rows, key=lambda x: x.label):
        click.echo(
            f"  [{lbl.label:<4}] {lbl.address}  {lbl.symbol:<10} "
            f"({lbl.source}, conf={lbl.confidence:.1f}) {lbl.notes}"
        )


@cli.command(name="label-rm")
@click.argument("address")
def label_rm(address: str) -> None:
    """Remove a label."""
    from .labels import LabelStore

    store = LabelStore.load(Path("data/labels.json"))
    if store.remove(address):
        store.save()
        click.echo(f"removed {address.lower()}")
    else:
        click.echo(f"no label for {address.lower()}", err=True)
        sys.exit(1)


@cli.command(name="label-import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
def label_import_cmd(file: Path) -> None:
    """Import labels from CSV or JSON file."""
    from .labels import LabelStore, import_labels_from_csv, import_labels_from_json

    suffix = file.suffix.lower()
    if suffix == ".csv":
        imported = import_labels_from_csv(file)
    elif suffix == ".json":
        imported = import_labels_from_json(file)
    else:
        click.echo(f"unsupported file format: {suffix} (need .csv or .json)", err=True)
        sys.exit(1)

    store = LabelStore.load(Path("data/labels.json"))
    added = 0
    for lbl in imported:
        if store.add(lbl):
            added += 1
    store.save()
    click.echo(
        f"imported {added} new labels from {file.name} "
        f"({len(imported) - added} already existed)"
    )


@cli.command(name="label-auto")
@click.option("--include-watchlist/--no-watchlist", default=True, help="label watchlist as 'good'")
@click.option("--include-discovered/--no-discovered", default=True, help="heuristic-label discovered tokens")
def label_auto_cmd(include_watchlist: bool, include_discovered: bool) -> None:
    """Auto-label tokens by heuristic + watchlist.

    Heuristics:
      - watchlist entries → labeled 'good' (you've curated these as interesting)
      - 24h price drop ≤ -80% → labeled 'rug'
      - 24h volume ≥ $50k AND change in [-20%, +200%] → labeled 'good'

    Auto-labels have confidence < 1.0; manual labels always win.
    """
    import asyncio as _asyncio
    from datetime import datetime

    from .discovery.dexscreener import DexScreenerClient
    from .labels import Label, LabelStore, auto_label_from_pair

    store = LabelStore.load(Path("data/labels.json"))
    added = 0
    now = datetime.now(UTC).isoformat()

    async def _go() -> int:
        nonlocal added

        # 1. Watchlist → good
        if include_watchlist:
            watchlist_rows: list[tuple[str, str, str]] = []
            try:
                from .storage.sqlite import Storage
                async with Storage(Path("state/bonnet.db")) as s:
                    watchlist_rows = await s.watchlist_list()
            except Exception as e:
                click.echo(f"  (watchlist read failed: {e})")
            for addr, sym, _added in watchlist_rows:
                lbl = Label(
                    address=addr,
                    label="good",
                    symbol=sym,
                    notes="auto: watchlist entry",
                    source="auto",
                    confidence=0.8,
                    labeled_at=now,
                )
                if store.add(lbl):
                    added += 1
                    click.echo(f"  + {addr[:10]}… ({sym}) = good [watchlist]")

        # 2. Discovered tokens → heuristic
        if include_discovered:
            async with DexScreenerClient() as dex:
                pairs = await dex.latest_pairs()
            for p in pairs:
                lbl = auto_label_from_pair(
                    symbol=p.token.symbol or "?",
                    address=p.token.address,
                    change_24h=p.price_change_pct_24h,
                    vol_24h=p.volume_usd_24h,
                )
                if lbl is None:
                    continue
                if store.add(lbl):
                    added += 1
                    click.echo(f"  + {lbl.address[:10]}… ({lbl.symbol}) = {lbl.label} [{lbl.notes}]")
        return added

    added = _asyncio.run(_go())
    store.save()
    click.echo(f"\n{added} new auto-labels written to data/labels.json")
    counts = {lbl: len(store.filter(lbl)) for lbl in ("good", "moon", "rug")}
    click.echo(
        f"Total labels: {sum(counts.values())} "
        f"(good={counts['good']}, moon={counts['moon']}, rug={counts['rug']})"
    )


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
