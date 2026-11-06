"""AGENT 5 · Risk — stop / trail / timeout. PAPER closes only. Never places orders.

GAP: Risk OWN/INPUT header truncated; exit rules recovered from PART 5 tail + test_risk.
trail_start_pct: arm trail after +N% from entry; trail_pct: exit when off peak by N%.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from desk import bus as busmod
from desk.links import coin_url
from desk.db import connect, current_run_id, get_setting, utc_now
from desk.fees import BPS, round_trip_fees

log = logging.getLogger("risk")


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


class Risk:
    name = "risk"

    def __init__(self, bus: busmod.Bus) -> None:
        self.bus = bus
        self._no_price: dict[int, int] = {}

    def settings(self) -> dict[str, float]:
        con = connect()
        try:
            return {
                "stop_pct": float(get_setting(con, "risk.stop_pct", "6")),
                "trail_start_pct": float(get_setting(con, "risk.trail_start_pct", "20")),
                "trail_pct": float(get_setting(con, "risk.trail_pct", "12")),
                "timeout_h": float(get_setting(con, "risk.timeout_h", "3")),
                "no_price_polls": float(get_setting(con, "risk.no_price_polls", "4")),
            }
        finally:
            con.close()

    def poll_position(self, position_id: int, price: float | None,
                      liquidity_usd: float | None = None, now: datetime | None = None) -> dict[str, Any]:
        s = self.settings()
        now = now or datetime.now(timezone.utc)
        con = connect()
        try:
            pos = con.execute("SELECT * FROM positions WHERE id = ?", (position_id,)).fetchone()
            if not pos:
                return {"ok": False, "error": "missing position"}
            if pos["closed_at"]:
                return {"ok": True, "status": "already_closed", "position_id": position_id}
            if str(pos["mode"] or "PAPER").upper() != "PAPER":
                return {
                    "ok": False, "status": "blocked", "position_id": position_id,
                    "reason": "non-PAPER position",
                }

            entry = float(pos["entry_price"])
            peak = float(pos["peak_price"] or entry)
            stop_price = float(pos["stop_price"])
            stop_pct = float(pos["stop_pct"] if pos["stop_pct"] is not None else s["stop_pct"])
            size = float(pos["size_usd"])
            trail_active = bool(pos["trail_active"])
            opened = _parse_ts(pos["opened_at"])

            # tighter stop setting may raise stop_price only
            new_stop = entry * (1 - s["stop_pct"] / 100.0)
            if new_stop > stop_price:  # tighter (higher floor for long)
                stop_price = new_stop
                stop_pct = s["stop_pct"]
                con.execute(
                    "UPDATE positions SET stop_price = ?, stop_pct = ? WHERE id = ?",
                    (stop_price, stop_pct, position_id),
                )

            if price is None:
                n = self._no_price.get(position_id, 0) + 1
                self._no_price[position_id] = n
                con.execute("INSERT OR REPLACE INTO heartbeats(agent, ts) VALUES (?, ?)", (self.name, utc_now()))
                con.commit()
                out = {"ok": True, "status": "no_price", "polls": n, "position_id": position_id}
                if n >= s["no_price_polls"]:
                    out["alert"] = "no price for 1 min"
                return out
            self._no_price[position_id] = 0

            if price > peak:
                peak = price
                con.execute("UPDATE positions SET peak_price = ? WHERE id = ?", (peak, position_id))

            gain_pct = (price / entry - 1) * 100
            if not trail_active and gain_pct >= s["trail_start_pct"]:
                trail_active = True
                con.execute("UPDATE positions SET trail_active = 1 WHERE id = ?", (position_id,))

            trail_line = peak * (1 - s["trail_pct"] / 100.0) if trail_active else None
            reason = None
            if liquidity_usd is not None and liquidity_usd <= 0:
                reason = "pulled"
            elif price <= stop_price:
                reason = "stop"
            elif trail_active and trail_line is not None and price <= trail_line:
                reason = "trail"
            else:
                hours = (now - opened).total_seconds() / 3600.0
                if hours >= s["timeout_h"]:
                    reason = "timeout"

            action = {"price": price, "peak": peak, "stop_line": stop_price,
                      "trail_line": trail_line, "trail_active": trail_active, "action": reason or "hold"}

            if reason is None:
                con.execute("INSERT OR REPLACE INTO heartbeats(agent, ts) VALUES (?, ?)", (self.name, utc_now()))
                con.commit()
                return {"ok": True, "status": "open", "position_id": position_id, **action}

            # PAPER exit. Fee bps follows the paper accounting setting when present.
            fee_bps = float(get_setting(con, "paper.fee_bps", str(BPS)) or BPS)
            fees = round_trip_fees(size, entry, price, fee_bps)
            pnl = size * (price / entry - 1) - fees
            pct = (price / entry - 1) * 100
            link = coin_url(pos["mint"], "solana")
            con.execute(
                "UPDATE positions SET closed_at=?, exit_price=?, exit_reason=?, pnl_usd=?, fees_usd=?, "
                "peak_price=?, stop_price=?, trail_active=? WHERE id=?",
                (utc_now(), price, reason, pnl, fees, peak, stop_price,
                 1 if trail_active else 0, position_id),
            )
            title = {
                "trail": f"Sold {pos['mint'][:6]}",
                "stop": f"Stopped {pos['mint'][:6]}",
                "timeout": f"Timeout {pos['mint'][:6]}",
                "pulled": f"Pulled {pos['mint'][:6]}",
            }[reason]
            subtitle = {
                "trail": "Risk · trailing stop",
                "stop": f"Risk · -{stop_pct:g}% stop",
                "timeout": "Risk · timeout",
                "pulled": "Risk · liquidity pulled",
            }[reason]
            con.execute(
                "INSERT INTO activity(ts,kind,mint,agent,title,subtitle,value,run_id) VALUES (?,?,?,?,?,?,?,?)",
                (utc_now(), "sold" if reason == "trail" else ("stopped" if reason == "stop" else "sold"),
                 pos["mint"], self.name, title, subtitle, f"{pnl:+.2f}",
                 pos["run_id"] if "run_id" in pos.keys() else current_run_id()),
            )
            con.execute("INSERT OR REPLACE INTO heartbeats(agent, ts) VALUES (?, ?)", (self.name, utc_now()))
            con.commit()
            return {
                "ok": True,
                "agent": self.name,
                "mint": pos["mint"],
                "ts": _iso(),
                "reason": reason,
                "entryPrice": entry,
                "exitPrice": price,
                "peakPrice": peak,
                "pnlUsd": round(pnl, 4),
                "pct": round(pct, 4),
                "feesUsd": round(fees, 4),
                "stopPct": stop_pct,
                "mode": pos["mode"] or "PAPER",
                "status": "closed",
                "link": link,
                "position_id": position_id,
                **action,
            }
        finally:
            con.close()

    async def emit(self, result: dict[str, Any]) -> None:
        if result.get("status") == "closed":
            await self.bus.publish(busmod.RISK_EXIT, result)
            await self.bus.publish(busmod.NOTIFY, {
                "kind": "exit", "agent": self.name, "mint": result.get("mint"),
                "text": f"PAPER · {result.get('reason')} · {result.get('pct')}%",
                "link": result.get("link"),
            })
