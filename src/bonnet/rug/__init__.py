"""Rug-resistance heuristics.

Each function returns a partial RugSignals update. The aggregator combines
them. Heuristics degrade gracefully when data is missing (signal = None
rather than guess).
"""
from __future__ import annotations

from datetime import UTC, datetime

from ..models import Pair, RugSignals, Token  # noqa: F401 (re-exported for tests)

# Standard ERC20 function selectors we use to probe contracts
SELECTORS = {
    "name": "0x06fdde03",
    "symbol": "0x95d89b41",
    "decimals": "0x313ce567",
    "totalSupply": "0x18160ddd",
    "owner": "0x8da5cb5b",       # owner()
    "getOwner": "0x893d20e8",    # getOwner() (some forks)
    "renounceOwnership": "0x715018a6",  # selector for the function, not a view
}


def age_from_pair(pair: Pair, now: datetime | None = None) -> float | None:
    """Hours since the pair was created. None if unknown."""
    if not pair.created_at:
        return None
    now = now or datetime.now(UTC)
    return max(0.0, (now - pair.created_at).total_seconds() / 3600.0)


def holder_concentration_penalty(top10_pct: float | None, top1_pct: float | None) -> tuple[float, list[str]]:
    """Return (rug_risk_delta, notes). Higher = worse."""
    notes: list[str] = []
    if top10_pct is None and top1_pct is None:
        return 0.0, ["holder concentration unknown"]
    delta = 0.0
    if top10_pct is not None:
        if top10_pct >= 80:
            delta += 0.5
            notes.append(f"top10 holders own {top10_pct:.0f}%")
        elif top10_pct >= 60:
            delta += 0.3
            notes.append(f"top10 holders own {top10_pct:.0f}%")
        elif top10_pct >= 40:
            delta += 0.1
        # else: fine
    if top1_pct is not None and top1_pct >= 30:
        delta += 0.2
        notes.append(f"single wallet holds {top1_pct:.0f}%")
    return min(delta, 0.9), notes


def age_penalty(contract_age_h: float | None, pair_age_h: float | None) -> tuple[float, list[str]]:
    """Younger = riskier. But also: very young with massive volume = wash-trade risk."""
    notes: list[str] = []
    delta = 0.0
    if contract_age_h is None and pair_age_h is None:
        return 0.0, ["age unknown"]
    age = min((x for x in [contract_age_h, pair_age_h] if x is not None), default=None)
    if age is None:
        return 0.0, notes
    if age < 6:
        delta += 0.4
        notes.append(f"very young ({age:.1f}h)")
    elif age < 24:
        delta += 0.2
        notes.append(f"young ({age:.1f}h)")
    elif age < 72:
        delta += 0.05
    return min(delta, 0.9), notes


def authority_penalty(
    mint_renounced: bool | None,
    freeze_renounced: bool | None,
    owner_renounced: bool | None,
) -> tuple[float, list[str]]:
    """Mint or freeze authority still present = ruggable."""
    notes: list[str] = []
    delta = 0.0
    if mint_renounced is False:
        delta += 0.3
        notes.append("mint authority not renounced")
    if freeze_renounced is False:
        delta += 0.2
        notes.append("freeze authority not renounced")
    if owner_renounced is False:
        delta += 0.1
        notes.append("owner not renounced")
    return min(delta, 0.9), notes


def lp_lock_penalty(
    lp_locked: bool | None,
    lp_lock_days_remaining: int | None,
) -> tuple[float, list[str]]:
    notes: list[str] = []
    delta = 0.0
    if lp_locked is False:
        delta += 0.3
        notes.append("LP not locked")
    elif lp_locked is True and lp_lock_days_remaining is not None and lp_lock_days_remaining < 7:
        delta += 0.15
        notes.append(f"LP lock short ({lp_lock_days_remaining}d)")
    elif lp_locked is None:
        delta += 0.05  # mild uncertainty
        notes.append("LP lock status unknown")
    return min(delta, 0.9), notes


def honeypot_penalty(is_honeypot: bool | None) -> tuple[float, list[str]]:
    if is_honeypot is True:
        return 1.0, ["honeypot (simulated sell failed)"]
    return 0.0, []


def deployer_penalty(prior_tokens: int | None, prior_rugs: int | None) -> tuple[float, list[str]]:
    """Deployer with many past rugs is suspect."""
    if prior_rugs is None or prior_rugs == 0:
        return 0.0, []
    notes: list[str] = []
    delta = 0.0
    if prior_rugs >= 3:
        delta = 0.5
        notes.append(f"deployer has {prior_rugs} prior rugs")
    elif prior_rugs >= 1:
        delta = 0.25
        notes.append(f"deployer has {prior_rugs} prior rug(s)")
    if prior_tokens and prior_rugs and prior_rugs / max(prior_tokens, 1) > 0.3:
        delta = max(delta, 0.4)
        notes.append("deployer rug rate >30%")
    return min(delta, 0.9), notes


def aggregate(
    *,
    top10_pct: float | None = None,
    top1_pct: float | None = None,
    mint_renounced: bool | None = None,
    freeze_renounced: bool | None = None,
    owner_renounced: bool | None = None,
    lp_locked: bool | None = None,
    lp_lock_days_remaining: int | None = None,
    contract_age_hours: float | None = None,
    pair_age_hours: float | None = None,
    is_honeypot: bool | None = None,
    deployer_prior_tokens: int | None = None,
    deployer_prior_rugs: int | None = None,
    is_contract_verified: bool | None = None,
    has_proxy: bool = False,
) -> RugSignals:
    """Combine all rug heuristics into a single RugSignals bundle.

    Each penalty is capped, then summed and clamped to [0, 1].
    """
    notes: list[str] = []

    h_delta, h_notes = holder_concentration_penalty(top10_pct, top1_pct)
    notes.extend(h_notes)

    a_delta, a_notes = age_penalty(contract_age_hours, pair_age_hours)
    notes.extend(a_notes)

    au_delta, au_notes = authority_penalty(mint_renounced, freeze_renounced, owner_renounced)
    notes.extend(au_notes)

    lp_delta, lp_notes = lp_lock_penalty(lp_locked, lp_lock_days_remaining)
    notes.extend(lp_notes)

    hp_delta, hp_notes = honeypot_penalty(is_honeypot)
    notes.extend(hp_notes)

    d_delta, d_notes = deployer_penalty(deployer_prior_tokens, deployer_prior_rugs)
    notes.extend(d_notes)

    if has_proxy:
        # Proxies can change implementation — slight bump
        d_delta += 0.1
        notes.append("contract is a proxy (implementation can change)")

    if is_contract_verified is False:
        # Unverified = we can't audit the code at all
        au_delta += 0.1
        notes.append("contract not verified")

    rug_risk = max(0.0, min(1.0, h_delta + a_delta + au_delta + lp_delta + hp_delta + d_delta))

    return RugSignals(
        top10_holder_pct=top10_pct,
        top1_holder_pct=top1_pct,
        is_contract_verified=is_contract_verified,
        has_proxy=has_proxy,
        mint_authority_renounced=mint_renounced,
        freeze_authority_renounced=freeze_renounced,
        owner_renounced=owner_renounced,
        lp_locked=lp_locked,
        lp_lock_days_remaining=lp_lock_days_remaining,
        contract_age_hours=contract_age_hours,
        pair_age_hours=pair_age_hours,
        deployer_prior_tokens=deployer_prior_tokens,
        deployer_prior_rugs=deployer_prior_rugs,
        is_honeypot=is_honeypot,
        rug_risk=rug_risk,
        notes=notes,
    )


__all__ = [
    "SELECTORS",
    "age_from_pair",
    "age_penalty",
    "aggregate",
    "authority_penalty",
    "deployer_penalty",
    "holder_concentration_penalty",
    "honeypot_penalty",
    "lp_lock_penalty",
]
