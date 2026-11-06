"""AGENT 4 · Executor — PAPER size only. Never places FOMO/live orders.

GAP: full Executor prompt truncated in dump. Behaviour from acceptance test_executor:
stack $200; under $47.50 → bump (default) or skip; liq cap max_liq_pct of pool (2% → $800 on $40k).
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from desk import bus as busmod
from desk.links import coin_url
from desk.db import PaperOnlyError, assert_paper_state, connect, current_run_id, get_setting, utc_now

log = logging.getLogger("executor")


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Executor:
    name = "executor"

    def __init__(self, bus: busmod.Bus) -> None:
        self.bus = bus

    def settings(self) -> dict[str, Any]:
        con = connect()
        try:
            return {
                "mode": get_setting(con, "mode", "PAPER"),
                "stack_usd": float(get_setting(con, "executor.stack_usd", "200")),
                "min_size_usd": float(get_setting(con, "executor.min_size_usd", "47.50")),
                "under_min_policy": get_setting(con, "executor.under_min_policy", "bump"),
                "max_liq_pct": float(get_setting(con, "executor.max_liq_pct", "2")),
                "stop_pct": float(get_setting(con, "risk.stop_pct", "6")),
            }
        finally:
            con.close()

    def size_order(self, stack_usd: float | None, liquidity_usd: float, desired_pct: float = 100.0) -> dict[str, Any]:
        s = self.settings()
        stack = s["stack_usd"] if stack_usd is None else float(stack_usd)
        raw = stack * (desired_pct / 100.0)
        cap = liquidity_usd * (s["max_liq_pct"] / 100.0) if liquidity_usd else raw
        sized = min(raw, cap) if cap > 0 else raw
        skipped = False
        warning = None
        if sized < s["min_size_usd"]:
            if s["under_min_policy"] == "bump":
                sized = s["min_size_usd"]
                # still respect liq cap
                if cap > 0:
                    sized = min(sized, cap)
            else:
                skipped = True
                warning = "every coin will be skipped" if stack < s["min_size_usd"] else "size skipped"
                sized = 0.0
        return {
            "size_usd": round(sized, 2),
            "skipped": skipped,
            "warning": warning,
            "cap_usd": round(cap, 2) if cap else None,
            "stack_usd": stack,
        }

    def open_paper(self, mint: str, price: float, liquidity_usd: float,
                   name: str | None = None, symbol: str | None = None,
                   stack_usd: float | None = None, event_id: str | None = None) -> dict[str, Any]:
        s = self.settings()
        if str(s["mode"]).upper() != "PAPER":
            return {
                "agent": self.name, "mint": mint, "ts": _iso(), "passed": False,
                "reason": "LIVE blocked — paper only", "status": "blocked",
            }
        sizing = self.size_order(stack_usd, liquidity_usd)
        if sizing["skipped"] or sizing["size_usd"] <= 0:
            con = connect()
            try:
                con.execute(
                    "INSERT INTO activity(ts,kind,mint,agent,title,subtitle,value,run_id) VALUES (?,?,?,?,?,?,?,?)",
                    (utc_now(), "rejected", mint, self.name,
                     f"Rejected {symbol or mint[:6]}", "Executor · skip size", "Skipped", current_run_id()),
                )
                con.commit()
            finally:
                con.close()
            return {
                "agent": self.name, "mint": mint, "ts": _iso(), "passed": False,
                "reason": sizing.get("warning") or "skipped", "status": "skipped", **sizing,
            }

        stop_pct = s["stop_pct"]
        stop_price = price * (1 - stop_pct / 100.0)
        link = coin_url(mint, "solana")
        rid = current_run_id()
        con = connect()
        try:
            assert_paper_state(con)
            if event_id:
                seen = con.execute(
                    "SELECT event_id FROM opened_events WHERE event_id = ?", (event_id,)
                ).fetchone()
                if seen:
                    return {
                        "agent": self.name, "mint": mint, "ts": _iso(), "passed": False,
                        "reason": "duplicate event", "status": "duplicate", **sizing,
                    }
            if rid:
                open_row = con.execute(
                    "SELECT id FROM positions WHERE run_id = ? AND mint = ? AND closed_at IS NULL",
                    (rid, mint),
                ).fetchone()
                if open_row:
                    return {
                        "agent": self.name, "mint": mint, "ts": _iso(), "passed": False,
                        "reason": "already open", "status": "duplicate", "position_id": open_row["id"],
                        **sizing,
                    }
            cur = con.execute(
                "INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,"
                "trail_active,mode,stop_pct,run_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mint, utc_now(), price, sizing["size_usd"], price, stop_price, 0, "PAPER", stop_pct, rid),
            )
            pos_id = cur.lastrowid
            if event_id:
                con.execute(
                    "INSERT INTO opened_events(event_id, run_id, mint, ts) VALUES (?,?,?,?)",
                    (event_id, rid or "", mint, utc_now()),
                )
            con.execute(
                "INSERT INTO signals(mint,ts,price,size_usd,stack_pct,link,mode,status,run_id) VALUES (?,?,?,?,?,?,?,?,?)",
                (mint, utc_now(), price, sizing["size_usd"], 100.0, link, "PAPER", "opened", rid),
            )
            con.execute(
                "INSERT INTO activity(ts,kind,mint,agent,title,subtitle,value,run_id) VALUES (?,?,?,?,?,?,?,?)",
                (utc_now(), "bought", mint, self.name,
                 f"Bought {symbol or name or mint[:6]}", "Executor · PAPER", f"${sizing['size_usd']:.2f}", rid),
            )
            con.execute("INSERT OR REPLACE INTO heartbeats(agent, ts) VALUES (?, ?)", (self.name, utc_now()))
            con.commit()
        except PaperOnlyError:
            con.rollback()
            return {
                "agent": self.name, "mint": mint, "ts": _iso(), "passed": False,
                "reason": "non-PAPER database", "status": "blocked",
            }
        except sqlite3.IntegrityError:
            con.rollback()
            return {
                "agent": self.name, "mint": mint, "ts": _iso(), "passed": False,
                "reason": "duplicate fill", "status": "duplicate", **sizing,
            }
        finally:
            con.close()

        return {
            "agent": self.name,
            "mint": mint,
            "ts": _iso(),
            "passed": True,
            "status": "opened",
            "position_id": pos_id,
            "entry_price": price,
            "stop_price": stop_price,
            "stop_pct": stop_pct,
            "link": link,
            "mode": "PAPER",
            "name": name,
            "symbol": symbol,
            **sizing,
        }

    async def emit(self, result: dict[str, Any]) -> None:
        if self.bus.is_paused(self.name):
            return
        if result.get("passed"):
            await self.bus.publish(busmod.EXECUTOR_OPEN, result)
            await self.bus.publish(busmod.NOTIFY, {
                "kind": "bought", "agent": self.name, "mint": result.get("mint"),
                "text": f"PAPER · Bought · ${result.get('size_usd')}",
                "link": result.get("link"),
            })
