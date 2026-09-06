"""Pipeline: orchestrate discovery → enrichment → scoring → alerts.

Single async entry point used by `bonnet scan` and `bonnet watch`.
"""
from __future__ import annotations

import asyncio

from .discovery.dexscreener import DexScreenerClient
from .logging import get_logger
from .models import Pair, Score
from .notify.telegram import TelegramNotifier
from .onchain.enricher import ContractEnricher
from .onchain.holders import HolderAnalyzer
from .onchain.honeypot import HoneypotSimulator
from .onchain.lp_lock import LPLockDetector
from .scoring.scorer import score_pair
from .settings import Settings, get_settings
from .storage.sqlite import Storage

log = get_logger("bonnet.pipeline")


async def score_one_pair(
    pair: Pair,
    *,
    enricher: ContractEnricher | None = None,
    honeypot: HoneypotSimulator | None = None,
    holder_analyzer: HolderAnalyzer | None = None,
    lp_detector: LPLockDetector | None = None,
) -> Score:
    """Score a single pair, with optional on-chain enrichment."""
    kwargs: dict = {}

    if enricher is not None:
        try:
            signals = await enricher.probe(pair.token.address)
            kwargs["owner_renounced"] = signals.owner_renounced
            kwargs["has_proxy"] = signals.has_proxy
            kwargs["is_contract_verified"] = True  # we got code back; treat as verified
        except Exception as e:
            log.warning("enrich_failed", token=pair.token.address, error=str(e))

    if honeypot is not None:
        # Use the pair address as a proxy holder for the honeypot test
        try:
            suspicious, tax_pct = await honeypot.simulate_sell_tax(
                pair.token.address, pair.pair_address, sell_amount_wei=10**18
            )
            kwargs["is_honeypot"] = suspicious
            if tax_pct > 0:
                log.info("honeypot_check", token=pair.token.symbol, tax_pct=round(tax_pct, 1))
        except Exception as e:
            log.warning("honeypot_check_failed", token=pair.token.address, error=str(e))

    if holder_analyzer is not None:
        try:
            stats = await holder_analyzer.analyze(pair.token.address)
            if stats.top10_pct is not None:
                kwargs["top10_holder_pct"] = stats.top10_pct
            if stats.top1_pct is not None:
                kwargs["top1_holder_pct"] = stats.top1_pct
            log.info(
                "holder_stats",
                token=pair.token.symbol,
                top10=stats.top10_pct,
                top1=stats.top1_pct,
                holders=stats.holder_count_estimate,
            )
        except Exception as e:
            log.warning("holder_analysis_failed", token=pair.token.address, error=str(e))

    if lp_detector is not None:
        try:
            lp_info = await lp_detector.probe(pair.pair_address)
            if lp_info.lp_locked is not None:
                kwargs["lp_locked"] = lp_info.lp_locked
            if lp_info.dead_address_share_pct is not None and lp_info.dead_address_share_pct > 50:
                # ≥50% LP in dead address → estimate lock duration as 'long'
                kwargs["lp_lock_days_remaining"] = 365 * 4  # ~indefinite
            if lp_info.notes:
                log.info("lp_lock_info", token=pair.token.symbol, notes="; ".join(lp_info.notes))
        except Exception as e:
            log.warning("lp_lock_check_failed", token=pair.token.address, error=str(e))

    return score_pair(pair, **kwargs)


async def run_scan(
    settings: Settings | None = None,
    *,
    top_n: int = 20,
    enrich: bool = True,
    holders: bool = False,
    lp_check: bool = False,
    notify_threshold: float | None = None,
    storage: Storage | None = None,
    dry_run_notify: bool = False,
) -> list[Score]:
    """One-shot scan: discover → enrich → score → record → maybe alert.

    Args:
      holders: if True, run holder-concentration analysis on each token
               (expensive — scans up to 50k blocks of Transfer logs per token).
      lp_check: if True, probe each pair for LP lock status.

    Returns the scored list, sorted by composite descending.
    """
    settings = settings or get_settings()
    notify_threshold = notify_threshold if notify_threshold is not None else settings.score_alert_threshold

    scores: list[Score] = []
    seen_addrs: set[str] = set()

    async with DexScreenerClient() as dex:
        pairs = await dex.latest_pairs()

    log.info("discovery_done", source="dexscreener", pairs=len(pairs))

    # Dedupe by token address (a token may have multiple pairs)
    unique_pairs: dict[str, Pair] = {}
    for p in pairs:
        key = p.token.address.lower()
        if key not in unique_pairs or p.liquidity_usd > unique_pairs[key].liquidity_usd:
            unique_pairs[key] = p
    pairs = list(unique_pairs.values())

    # Optionally enrich + score
    enricher: ContractEnricher | None = None
    holder_analyzer: HolderAnalyzer | None = None
    lp_detector: LPLockDetector | None = None
    honeypot: HoneypotSimulator | None = None

    if enrich or holders or lp_check:
        from .discovery.rpc_client import RpcClient
        rpc = RpcClient(settings)
        if enrich:
            enricher = ContractEnricher(rpc)
            honeypot = HoneypotSimulator(rpc)
        if holders:
            holder_analyzer = HolderAnalyzer(rpc)
        if lp_check:
            lp_detector = LPLockDetector(rpc)

    # Process in parallel but with a small concurrency cap to be polite to RPCs
    sem = asyncio.Semaphore(4)

    async def _process(p: Pair) -> Score | None:
        async with sem:
            try:
                return await score_one_pair(
                    p,
                    enricher=enricher,
                    honeypot=honeypot,
                    holder_analyzer=holder_analyzer,
                    lp_detector=lp_detector,
                )
            except Exception as e:
                log.warning("score_failed", token=p.token.symbol, error=str(e))
                return None

    results = await asyncio.gather(*[_process(p) for p in pairs])
    scores = [s for s in results if s is not None]
    scores.sort(key=lambda s: s.composite, reverse=True)

    # Persist + alert
    storage_obj = storage
    own_storage = False
    if storage_obj is None:
        storage_obj = Storage(settings.db_path)
        await storage_obj.connect()
        own_storage = True
    try:
        for s in scores:
            try:
                await storage_obj.record_score(s)
            except Exception as e:
                log.warning("record_failed", token=s.token.address, error=str(e))
            seen_addrs.add(s.token.address.lower())

        # Alerts: any above threshold, dedup by cooldown
        notifier = TelegramNotifier(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            dry_run=dry_run_notify or not settings.telegram_bot_token,
        )
        try:
            for s in scores:
                if s.composite < notify_threshold:
                    break
                if not await storage_obj.should_alert(s.token.address):
                    continue
                sent = await notifier.send(TelegramNotifier.format_alert(s))
                if sent:
                    await storage_obj.record_alert(s.token.address, s.composite)
        finally:
            await notifier.aclose()
    finally:
        if own_storage:
            await storage_obj.close()

    log.info("scan_done", scored=len(scores), top_composite=scores[0].composite if scores else 0.0)
    return scores[:top_n]


async def watch_loop(
    *,
    interval_minutes: int = 15,
    notify_threshold: float | None = None,
    settings: Settings | None = None,
    storage: Storage | None = None,
    dry_run_notify: bool = False,
    holders: bool = False,
    lp_check: bool = False,
) -> None:
    """Continuously scan + alert."""
    settings = settings or get_settings()
    log.info("watch_started", interval_min=interval_minutes)
    while True:
        try:
            await run_scan(
                settings=settings,
                notify_threshold=notify_threshold,
                storage=storage,
                dry_run_notify=dry_run_notify,
                holders=holders,
                lp_check=lp_check,
            )
        except Exception as e:
            log.error("watch_iteration_failed", error=str(e))
        await asyncio.sleep(interval_minutes * 60)


__all__ = ["run_scan", "score_one_pair", "watch_loop"]
