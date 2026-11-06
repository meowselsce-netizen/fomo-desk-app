"""Map public DexScreener / RugCheck payloads into agent inputs.

Missing safety fields are omitted. Callers must fail closed. This module does
not invent authorities, holder counts, LP locks, or flow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


def _has(obj: Mapping[str, Any] | None, key: str) -> bool:
    return isinstance(obj, Mapping) and key in obj


def _dig(obj: Any, path: str) -> Any:
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, Mapping) or part not in cur:
            return None
        cur = cur[part]
    return cur


def pool_age_min(pair: Mapping[str, Any], now: datetime | None = None) -> float | None:
    created = pair.get("pairCreatedAt")
    if created is None:
        return None
    try:
        created_ms = float(created)
    except (TypeError, ValueError):
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now.timestamp() * 1000 - created_ms) / 60_000)


def market_from_pair(pair: Mapping[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Scanner/analyst fields that are actually present on a DexScreener pair."""
    now = now or datetime.now(timezone.utc)
    base = pair.get("baseToken") if isinstance(pair.get("baseToken"), Mapping) else {}
    out: dict[str, Any] = {
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "mint": base.get("address"),
        "pair": pair.get("pairAddress"),
    }
    liq = _dig(pair, "liquidity.usd")
    if liq is not None:
        out["liquidity_usd"] = float(liq)
    h1 = _dig(pair, "priceChange.h1")
    if h1 is not None:
        out["price_change_h1"] = float(h1)
    m5 = _dig(pair, "priceChange.m5")
    if m5 is not None:
        out["price_change_m5"] = float(m5)
    age = pool_age_min(pair, now)
    if age is not None:
        out["age_min"] = age
    if pair.get("priceUsd") not in (None, ""):
        out["price_usd"] = float(pair["priceUsd"])
    mc = pair.get("marketCap")
    if mc is None:
        mc = pair.get("fdv")
    if mc is not None:
        out["market_cap"] = float(mc)
    for src, dest in (
        ("txns.m5.buys", "buys_m5"),
        ("txns.m5.sells", "sells_m5"),
        ("txns.h1.buys", "buys_h1"),
        ("txns.h1.sells", "sells_h1"),
        ("volume.m5", "volume_m5"),
        ("volume.h1", "volume_h1"),
    ):
        val = _dig(pair, src)
        if val is not None:
            out[dest] = float(val)
    chain = str(pair.get("chainId") or "")
    if chain:
        out["chain"] = chain
    return out


def _holder_pct_sum(holders: list[Any]) -> float | None:
    pcts: list[float] = []
    for holder in holders[:10]:
        if not isinstance(holder, Mapping) or "pct" not in holder or holder.get("pct") is None:
            return None
        pcts.append(float(holder["pct"]))
    if not pcts:
        return None
    total = sum(pcts)
    # RugCheck publishes percent points (5.2 = 5.2%). A sum that cannot exceed 1
    # is a fraction and is scaled. Mixed or ambiguous values are not guessed.
    if total <= 1.0:
        return total * 100.0
    return total


def _lp_locked_pct(report: Mapping[str, Any]) -> float | None:
    markets = report.get("markets") if _has(report, "markets") else None
    if not isinstance(markets, list) or not markets:
        return None
    found: list[float] = []
    for market in markets:
        if not isinstance(market, Mapping):
            continue
        lp = market.get("lp") if isinstance(market.get("lp"), Mapping) else None
        if lp is not None and "lpLockedPct" in lp and lp.get("lpLockedPct") is not None:
            found.append(float(lp["lpLockedPct"]))
    if not found:
        return None
    return min(found)


def audit_facts_from_rug(report: Mapping[str, Any] | None, market: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build auditor facts. Absent RugCheck keys are omitted so the auditor fails closed."""
    facts: dict[str, Any] = {}
    market = market or {}
    if "age_min" in market and market.get("age_min") is not None:
        facts["age_min"] = float(market["age_min"])
        facts["source_age"] = "dex"
    if not isinstance(report, Mapping):
        facts["delegations_unavailable"] = True
        return facts
    if _has(report, "mintAuthority"):
        facts["mint_authority"] = report.get("mintAuthority")
        facts["source_auth"] = "rugcheck"
    if _has(report, "freezeAuthority"):
        facts["freeze_authority"] = report.get("freezeAuthority")
        facts["source_auth"] = "rugcheck"
    supply = None
    for key in ("totalSupply", "supply"):
        if _has(report, key) and report.get(key) not in (None, ""):
            supply = report.get(key)
            break
    token = report.get("token") if isinstance(report.get("token"), Mapping) else None
    if supply is None and token is not None and _has(token, "supply"):
        supply = token.get("supply")
    if supply is not None:
        facts["supply"] = supply
        facts["source_dev"] = "rugcheck"
    if _has(report, "creatorBalance") and report.get("creatorBalance") is not None and supply not in (None, 0, "0"):
        try:
            facts["creator_balance_pct"] = float(report["creatorBalance"]) / float(supply) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if _has(report, "devSold0"):
        facts["dev_sold_0"] = bool(report.get("devSold0"))
    elif _has(report, "creatorSold"):
        facts["dev_sold_0"] = not bool(report.get("creatorSold"))
    if _has(report, "topHolders") and isinstance(report.get("topHolders"), list):
        pct = _holder_pct_sum(report["topHolders"])
        if pct is not None:
            facts["top10_pct"] = pct
            facts["source_top10"] = "rugcheck"
        facts["holders"] = len(report["topHolders"])
    lp = _lp_locked_pct(report)
    if lp is not None:
        facts["lp_locked_pct"] = lp
        facts["source_lp"] = "rugcheck"
    if _has(report, "risks") and isinstance(report.get("risks"), list):
        bundled = 0
        for risk in report["risks"]:
            name = str((risk or {}).get("name") if isinstance(risk, Mapping) else risk).lower()
            if "bundl" in name:
                bundled += 1
        facts["bundled_count"] = bundled
        facts["source_bundle"] = "rugcheck"
    if _has(report, "maxDelegateAccounts"):
        facts["max_delegate_accounts"] = int(report["maxDelegateAccounts"] or 0)
        facts["source_deleg"] = "rugcheck"
    else:
        facts["delegations_unavailable"] = True
        facts["source_deleg"] = "rugcheck"
    return facts


def analyst_ctx(market: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    """Analyst state from real fields only. Flow keys are included only when Dex sent them."""
    ctx: dict[str, Any] = {
        "name": market.get("name"),
        "symbol": market.get("symbol"),
        "age_min": market.get("age_min"),
        "liq": market.get("liquidity_usd") or 0,
        "mcap": market.get("market_cap"),
        "price_usd": market.get("price_usd"),
        "top10": facts.get("top10_pct", market.get("top10_pct")),
        "dev": facts.get("creator_balance_pct"),
        "lp": None if "lp_locked_pct" not in facts else f"{facts['lp_locked_pct']}%",
        "bund": None if "bundled_count" not in facts else facts.get("bundled_count"),
        "del": None if facts.get("delegations_unavailable") else facts.get("max_delegate_accounts"),
    }
    needed = ("buys_m5", "sells_m5", "buys_h1", "sells_h1", "price_change_m5", "price_change_h1")
    if all(k in market and market.get(k) is not None for k in needed):
        ctx.update({
            "b5": market["buys_m5"],
            "s5": market["sells_m5"],
            "v5": market.get("volume_m5") or 0,
            "p5": market["price_change_m5"],
            "b1": market["buys_h1"],
            "s1": market["sells_h1"],
            "v1": market.get("volume_h1") or 0,
            "p1": market["price_change_h1"],
        })
    return ctx
