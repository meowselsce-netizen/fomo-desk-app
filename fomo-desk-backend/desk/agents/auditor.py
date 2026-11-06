"""AGENT 2 · Auditor — 8 layers, all-or-nothing. Paper only.

Layers (from prompt fragments + acceptance tests):
1 Mint authority  2 Freeze authority  3 Dev/supply (sold 0)
4 Top10           5 LP locked         6 Pool age
7 Bundled wallets 8 Delegations (+ holders count)
GAP: full layer wording/MAPPING for 3–6 truncated in dump; implemented from tests + Analyst STATE fields.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from desk import bus as busmod
from desk.db import connect, current_run_id, get_setting, utc_now

log = logging.getLogger("auditor")

LAYER_NAMES = {
    1: "Mint authority",
    2: "Freeze authority",
    3: "Dev / supply",
    4: "Top 10 holders",
    5: "LP locked",
    6: "Pool age",
    7: "Bundled wallets",
    8: "Delegations",
}


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Auditor:
    name = "auditor"

    def __init__(self, bus: busmod.Bus) -> None:
        self.bus = bus

    def settings(self) -> dict[str, Any]:
        con = connect()
        try:
            return {
                "enabled": get_setting(con, "auditor.enabled", "true") == "true",
                "max_dev_pct": float(get_setting(con, "auditor.max_dev_pct", "5")),
                "max_top10_pct": float(get_setting(con, "auditor.max_top10_pct", "25")),
                "min_lp_locked_pct": float(get_setting(con, "auditor.min_lp_locked_pct", "99")),
                "min_pool_age_min": float(get_setting(con, "auditor.min_pool_age_min", "10")),
                "max_bundled": int(float(get_setting(con, "auditor.max_bundled", "5"))),
                "max_delegated_accounts": int(float(get_setting(con, "auditor.max_delegated_accounts", "10"))),
            }
        finally:
            con.close()

    def _save_layer(self, mint: str, layer: dict[str, Any]) -> None:
        con = connect()
        try:
            con.execute(
                "INSERT INTO audits(mint,ts,layer,name,value_text,pass,source,run_id) VALUES (?,?,?,?,?,?,?,?)",
                (
                    mint, utc_now(), layer["n"], layer["name"], layer.get("value"),
                    None if layer.get("pass") is None else (1 if layer["pass"] else 0),
                    layer.get("source"), current_run_id(),
                ),
            )
            con.commit()
        finally:
            con.close()

    def audit(self, mint: str, facts: dict[str, Any]) -> dict[str, Any]:
        """facts: mint_authority, freeze_authority, supply, creator_balance_pct,
        top10_pct, lp_locked_pct, age_min, bundled_count, max_delegate_accounts,
        holders, sources...
        """
        s = self.settings()
        layers: list[dict[str, Any]] = []
        reason = None
        holders = facts.get("holders")

        def add(n: int, passed: bool | None, value: str, source: str) -> bool:
            nonlocal reason
            layer = {
                "n": n, "name": LAYER_NAMES[n], "pass": passed,
                "value": value, "source": source,
            }
            layers.append(layer)
            self._save_layer(mint, layer)
            if passed is False and reason is None:
                reason = value
                return False
            return True

        # If auditor disabled: pass-through empty layers? Prompt says toggle off asks twice.
        # When disabled, mark all skipped as pass for pipeline (documented gap).
        if not s["enabled"]:
            out = {
                "agent": self.name, "mint": mint, "ts": _iso(), "passed": True,
                "layersChecked": 0, "layers": [], "holders": holders,
                "reason": None, "disabled": True,
            }
            return out

        # Layer 1 — mint authority must be present and revoked. Missing fails closed.
        if "mint_authority" not in facts:
            add(1, False, "no data: mint authority", facts.get("source_auth", "rugcheck"))
            return self._finish(mint, layers, False, "no data: mint authority", holders, checked=1)
        mint_auth = facts.get("mint_authority")
        ok = mint_auth in (None, "", "None", "revoked")
        if not add(1, ok, "None" if ok else str(mint_auth), facts.get("source_auth", "rugcheck")):
            return self._finish(mint, layers, False, reason, holders, checked=1)

        # Layer 2 — freeze authority
        if "freeze_authority" not in facts:
            add(2, False, "no data: freeze authority", facts.get("source_auth", "rugcheck"))
            return self._finish(mint, layers, False, "no data: freeze authority", holders, checked=2)
        freeze = facts.get("freeze_authority")
        ok = freeze in (None, "", "None", "revoked")
        if not add(2, ok, "None" if ok else str(freeze), facts.get("source_auth", "rugcheck")):
            return self._finish(mint, layers, False, reason, holders, checked=2)

        # Layer 3 — supply / dev sold 0. Unknown sold-flag fails closed (do not assume sold 0).
        supply = facts.get("supply") if "supply" in facts else None
        if supply in (None, 0, "0"):
            label = "no data: supply" if "supply" not in facts or supply is None else "no supply"
            add(3, False, label, facts.get("source_dev", "rpc"))
            return self._finish(mint, layers, False, label, holders, checked=3)
        if "creator_balance_pct" not in facts and "dev_pct" not in facts:
            add(3, False, "no data: dev balance", facts.get("source_dev", "rugcheck"))
            return self._finish(mint, layers, False, "no data: dev balance", holders, checked=3)
        if "dev_sold_0" not in facts:
            add(3, False, "no data: dev sold", facts.get("source_dev", "rugcheck"))
            return self._finish(mint, layers, False, "no data: dev sold", holders, checked=3)
        dev_pct = float(facts.get("creator_balance_pct") if facts.get("creator_balance_pct") is not None else facts.get("dev_pct") or 0)
        sold0 = bool(facts.get("dev_sold_0"))
        ok = dev_pct <= s["max_dev_pct"] and sold0
        val = f"dev {dev_pct:.1f}%, sold 0" if sold0 else f"dev {dev_pct:.1f}%, sold"
        if not add(3, ok, val if ok else (f"dev holds {dev_pct:.0f}%" if not sold0 or dev_pct > s["max_dev_pct"] else val), facts.get("source_dev", "rugcheck")):
            return self._finish(mint, layers, False, reason, holders, checked=3)

        # Layer 4 — top 10
        if "top10_pct" not in facts or facts.get("top10_pct") is None:
            add(4, False, "no data: top10", facts.get("source_top10", "rpc"))
            return self._finish(mint, layers, False, "no data: top10", holders, checked=4)
        top10 = float(facts["top10_pct"])
        ok = top10 <= s["max_top10_pct"]
        if not add(4, ok, f"top10 {top10:.1f}%", facts.get("source_top10", "rpc")):
            return self._finish(mint, layers, False, reason, holders, checked=4)

        # Layer 5 — LP locked
        if "lp_locked_pct" not in facts or facts.get("lp_locked_pct") is None:
            add(5, False, "no data: lp locked", facts.get("source_lp", "rugcheck"))
            return self._finish(mint, layers, False, "no data: lp locked", holders, checked=5)
        lp = float(facts["lp_locked_pct"])
        ok = lp >= s["min_lp_locked_pct"]
        if not add(5, ok, f"LP {lp:.0f}% locked", facts.get("source_lp", "rugcheck")):
            return self._finish(mint, layers, False, reason, holders, checked=5)

        # Layer 6 — pool age
        if "age_min" not in facts or facts.get("age_min") is None:
            add(6, False, "no data: pool age", facts.get("source_age", "dex"))
            return self._finish(mint, layers, False, "no data: pool age", holders, checked=6)
        age = float(facts["age_min"])
        ok = age >= s["min_pool_age_min"]
        if not add(6, ok, f"{age:.0f} min" if ok else "pool too young", facts.get("source_age", "dex")):
            return self._finish(mint, layers, False, reason, holders, checked=6)

        # Layer 7 — bundled. A missing count is not treated as zero.
        if "bundled_count" not in facts or facts.get("bundled_count") is None:
            add(7, False, "no data: bundled wallets", facts.get("source_bundle", "rugcheck"))
            return self._finish(mint, layers, False, "no data: bundled wallets", holders, checked=7)
        bundled = int(facts["bundled_count"])
        ok = bundled < s["max_bundled"]
        if not add(7, ok, "None" if ok else f"{bundled} bundled wallets", facts.get("source_bundle", "rugcheck")):
            return self._finish(mint, layers, False, reason, holders, checked=7)

        # Layer 8 — delegations. Public RPC often cannot answer; that is a fail, not a pass.
        if facts.get("delegations_unavailable") or "max_delegate_accounts" not in facts or facts.get("max_delegate_accounts") is None:
            add(8, False, "no data: delegations", facts.get("source_deleg", "rpc"))
            return self._finish(mint, layers, False, "no data: delegations", holders, checked=8)
        max_del_i = int(facts["max_delegate_accounts"])
        ok = max_del_i <= s["max_delegated_accounts"]
        val = "None" if ok else f"{max_del_i} wallets → delegate"
        if not add(8, ok, val, facts.get("source_deleg", "rpc")):
            return self._finish(mint, layers, False, reason, holders, checked=8)

        if holders is not None:
            con = connect()
            try:
                con.execute("UPDATE coins SET holders = ? WHERE mint = ?", (int(holders), mint))
                con.commit()
            finally:
                con.close()

        return self._finish(mint, layers, True, None, holders, checked=8)

    def _finish(self, mint, layers, passed, reason, holders, checked) -> dict[str, Any]:
        out = {
            "agent": self.name, "mint": mint, "ts": _iso(), "passed": passed,
            "layersChecked": checked, "layers": layers, "holders": holders,
            "reason": reason,
        }
        con = connect()
        try:
            if not passed:
                con.execute(
                    "INSERT INTO activity(ts,kind,mint,agent,title,subtitle,value,run_id) VALUES (?,?,?,?,?,?,?,?)",
                    (utc_now(), "rejected", mint, self.name,
                     f"Rejected {mint[:6]}", f"Auditor · {reason}", "Skipped", current_run_id()),
                )
            con.execute("INSERT OR REPLACE INTO heartbeats(agent, ts) VALUES (?, ?)", (self.name, utc_now()))
            con.commit()
        finally:
            con.close()
        return out

    async def emit(self, result: dict[str, Any]) -> None:
        if self.bus.is_paused(self.name):
            return
        for layer in result.get("layers") or []:
            await self.bus.publish(busmod.AUDITOR_LAYER, {"mint": result["mint"], **layer})
        if result.get("passed"):
            await self.bus.publish(busmod.AUDITOR_PASS, result)
        else:
            await self.bus.publish(busmod.NOTIFY, {
                "kind": "rejected", "agent": self.name, "mint": result.get("mint"),
                "text": f"PAPER · Rejected · Auditor · {result.get('reason')}",
            })
