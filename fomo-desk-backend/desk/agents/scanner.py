"""AGENT 1 · Scanner — count and cut. Paper only."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from desk import bus as busmod
from desk.db import connect, current_run_id, get_setting, utc_now

log = logging.getLogger("scanner")


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Scanner:
    name = "scanner"

    def __init__(self, bus: busmod.Bus) -> None:
        self.bus = bus

    def settings(self) -> dict[str, float]:
        con = connect()
        try:
            return {
                "min_liquidity_usd": float(get_setting(con, "scanner.min_liquidity_usd", "40000")),
                "max_top10_pct": float(get_setting(con, "scanner.max_top10_pct", "25")),
                "max_h1_change_pct": float(get_setting(con, "scanner.max_h1_change_pct", "80")),
                "min_pool_age_min": float(get_setting(con, "scanner.min_pool_age_min", "10")),
                "poll_s": float(get_setting(con, "scanner.poll_s", "60")),
            }
        finally:
            con.close()

    def ingest_migration(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Save graduated coin to waiting room (coins table)."""
        mint = raw.get("mint") or raw.get("token") or raw.get("address")
        if not mint:
            raise ValueError("migration missing mint")
        pool = raw.get("pool") or raw.get("pair") 
        creator = raw.get("creator")
        name = raw.get("name")
        symbol = raw.get("symbol")
        migrated_at = raw.get("migrated_at") or raw.get("migratedAt") or _iso()
        creator_bal = raw.get("creator_balance")
        con = connect()
        try:
            con.execute(
                "INSERT OR REPLACE INTO coins(mint,name,symbol,pool,migrated_at,creator,"
                "creator_balance_at_migration,holders) VALUES (?,?,?,?,?,?,?,?)",
                (mint, name, symbol, pool, migrated_at, creator, creator_bal, None),
            )
            con.execute(
                "INSERT OR REPLACE INTO heartbeats(agent, ts) VALUES (?, ?)",
                (self.name, utc_now()),
            )
            con.commit()
        finally:
            con.close()
        return {"agent": self.name, "mint": mint, "pool": pool, "migrated_at": migrated_at, "creator": creator}

    def evaluate(self, mint: str, market: dict[str, Any], top10_pct: float | None = None,
                 ran_today: list[str] | None = None) -> dict[str, Any]:
        """Apply kill rules in order. Returns scanner JSON."""
        s = self.settings()
        name = (market.get("name") or "").strip().lower()
        symbol = (market.get("symbol") or "").strip().lower()
        kills: list[tuple[str, str]] = []

        if "liquidity_usd" in market and market.get("liquidity_usd") is not None:
            liq = float(market.get("liquidity_usd") or 0)
        elif isinstance(market.get("liquidity"), dict) and market["liquidity"].get("usd") is not None:
            liq = float(market["liquidity"]["usd"])
        else:
            liq = None
            kills.append(("liq", "no data: liquidity"))
        if liq is not None and liq < s["min_liquidity_usd"]:
            kills.append(("liq", f"liq ${liq/1000:.1f}K"))

        if top10_pct is not None:
            top10 = float(top10_pct)
        elif "top10_pct" in market and market.get("top10_pct") is not None:
            top10 = float(market["top10_pct"])
        else:
            top10 = None
            kills.append(("top10", "no data: top10"))
        if top10 is not None and top10 > s["max_top10_pct"]:
            kills.append(("top10", f"top 10 hold {top10:.0f}%"))

        if "price_change_h1" in market and market.get("price_change_h1") is not None:
            h1 = float(market["price_change_h1"])
        elif isinstance(market.get("priceChange"), dict) and market["priceChange"].get("h1") is not None:
            h1 = float(market["priceChange"]["h1"])
        else:
            h1 = None
            kills.append(("h1", "no data: h1 change"))
        if h1 is not None and h1 > s["max_h1_change_pct"]:
            kills.append(("h1", f"+{h1:.0f}% in 1h"))
        # copycat
        for other in ran_today or []:
            o = other.strip().lower()
            if o and (o == name or o == symbol):
                kills.append(("copycat", f"copycat of {other}"))
                break
        # pool age (scanner setting; auditor also checks). Missing age fails closed.
        if "age_min" not in market or market.get("age_min") is None:
            age = None
            kills.append(("age", "no data: pool age"))
        else:
            age = float(market["age_min"])
            if age < s["min_pool_age_min"]:
                kills.append(("age", "pool too young"))

        reason = kills[0][1] if kills else None
        passed = reason is None
        verdict = "pass" if passed else "kill"
        con = connect()
        try:
            con.execute(
                "INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason,run_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (mint, utc_now(), liq, top10, h1, age, verdict, reason, current_run_id()),
            )
            if not passed:
                con.execute(
                    "INSERT INTO activity(ts,kind,mint,agent,title,subtitle,value,run_id) VALUES (?,?,?,?,?,?,?,?)",
                    (utc_now(), "rejected", mint, self.name,
                     f"Rejected {market.get('symbol') or mint[:6]}",
                     f"Scanner · {reason}", "Skipped", current_run_id()),
                )
            con.execute("INSERT OR REPLACE INTO heartbeats(agent, ts) VALUES (?, ?)", (self.name, utc_now()))
            con.commit()
        finally:
            con.close()

        out = {
            "agent": self.name,
            "mint": mint,
            "ts": _iso(),
            "passed": passed,
            "reason": reason,
            "liquidity_usd": liq,
            "top10_pct": top10,
            "h1_change_pct": h1,
            "age_min": age,
            "price_usd": market.get("price_usd") or market.get("priceUsd"),
            "name": market.get("name"),
            "symbol": market.get("symbol"),
        }
        return out

    async def emit(self, result: dict[str, Any]) -> None:
        if self.bus.is_paused(self.name):
            return
        if result.get("passed"):
            await self.bus.publish(busmod.SCANNER_PASS, result)
        else:
            await self.bus.publish(busmod.NOTIFY, {
                "kind": "rejected", "agent": self.name, "mint": result.get("mint"),
                "text": f"PAPER · Rejected · {result.get('reason')}",
            })
