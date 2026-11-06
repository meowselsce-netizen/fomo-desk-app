"""AGENT 3 · Analyst — Jev questions + code gate; rules mode when no key / analyst_mode=rules."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any

from desk import bus as busmod
from desk.db import connect, current_run_id, get_setting, utc_now

log = logging.getLogger("analyst")


def _iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Analyst:
    name = "analyst"

    def __init__(self, bus: busmod.Bus) -> None:
        self.bus = bus

    def settings(self) -> dict[str, Any]:
        con = connect()
        try:
            return {
                "mode": get_setting(con, "analyst_mode", "rules"),
                "alive_6h_min": float(get_setting(con, "analyst.alive_6h_min", "0.70")),
                "organic_min": float(get_setting(con, "analyst.organic_min", "0.65")),
                "chase_max": float(get_setting(con, "analyst.chase_max", "0.30")),
                "score_min": float(get_setting(con, "analyst.score_min", "70")),
                "state_max_chars": int(float(get_setting(con, "analyst.state_max_chars", "3000"))),
            }
        finally:
            con.close()

    def build_state(self, ctx: dict[str, Any]) -> str:
        s = (
            f"Coin {ctx.get('name')} on Solana, graduated {ctx.get('age_min')} min ago. "
            f"Liquidity ${ctx.get('liq')}. Market cap ${ctx.get('mcap')}.\n"
            f" Last 5 min: {ctx.get('b5')} buys, {ctx.get('s5')} sells, volume ${ctx.get('v5')}, price {ctx.get('p5')}%.\n"
            f" Last 1h: {ctx.get('b1')} buys, {ctx.get('s1')} sells, volume ${ctx.get('v1')}, price {ctx.get('p1')}%.\n"
            f" Top 10 hold {ctx.get('top10')}%. Dev holds {ctx.get('dev')}%, sold 0. "
            f"LP {ctx.get('lp')}. Bundled {ctx.get('bund')}. Delegations {ctx.get('del')}."
        )
        max_c = self.settings()["state_max_chars"]
        return s[:max_c]

    def rules_answers(self, ctx: dict[str, Any]) -> dict[str, float]:
        """Deterministic stand-in when analyst_mode=rules (no paid API).

        Missing flow is not filled in. The gate then fails closed.
        """
        needed = ("b5", "s5", "b1", "s1", "p5", "p1")
        if any(k not in ctx or ctx.get(k) is None for k in needed):
            return {"alive_6h": 0.0, "organic": 0.0, "flush": 0.0, "chase": 1.0}
        h1 = float(ctx.get("p1") or 0)
        m5 = float(ctx.get("p5") or 0)
        b5 = float(ctx.get("b5") or 0)
        s5 = float(ctx.get("s5") or 0)
        # alive: prefer active flow
        alive = 0.75 if (b5 + s5) >= 10 else 0.55
        # organic: buys not dwarfed by sells
        organic = 0.7 if b5 >= s5 else 0.5
        # flush: recent dip with buys
        flush = 0.7 if m5 < 0 and b5 > s5 else 0.4
        # chase: already ran hard
        chase = 0.6 if h1 > 50 else (0.2 if h1 < 20 else 0.35)
        return {
            "alive_6h": alive,
            "organic": organic,
            "flush": flush,
            "chase": chase,
        }

    def apply_gate(self, answers: dict[str, float]) -> tuple[bool, int, list[str]]:
        s = self.settings()
        failed: list[str] = []
        a = float(answers["alive_6h"])
        o = float(answers["organic"])
        f = float(answers["flush"])
        c = float(answers["chase"])
        if a < s["alive_6h_min"]:
            failed.append(f"alive_6h {a}")
        if o < s["organic_min"]:
            failed.append(f"organic {o}")
        if c > s["chase_max"]:
            failed.append(f"chase {c}")
        score = int(round(100 * (a + o + f + (1 - c)) / 4))
        if score < s["score_min"]:
            failed.append(f"score {score}")
        return (len(failed) == 0, score, failed)

    def analyze(self, mint: str, ctx: dict[str, Any], answers: dict[str, float] | None = None) -> dict[str, Any]:
        s = self.settings()
        state = self.build_state(ctx)
        model = "rules"
        input_tokens = 0
        if answers is None:
            if s["mode"] == "rules":
                answers = self.rules_answers(ctx)
                model = "rules"
            else:
                # Paid analyst APIs are not called. Unknown mode fails closed into rules.
                answers = self.rules_answers(ctx)
                model = "rules-fallback-no-paid-api"
        passed, score, failed = self.apply_gate(answers)
        con = connect()
        try:
            con.execute(
                "INSERT INTO verdicts(mint,ts,alive_6h,organic,flush,chase,score,pass,reason,input_tokens,run_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    mint, utc_now(), answers["alive_6h"], answers["organic"], answers["flush"],
                    answers["chase"], score, 1 if passed else 0,
                    failed[0] if failed else None, input_tokens, current_run_id(),
                ),
            )
            if not passed:
                con.execute(
                    "INSERT INTO activity(ts,kind,mint,agent,title,subtitle,value,run_id) VALUES (?,?,?,?,?,?,?,?)",
                    (utc_now(), "rejected", mint, self.name,
                     f"Rejected {ctx.get('symbol') or mint[:6]}",
                     f"Analyst · {failed[0]}", "Skipped", current_run_id()),
                )
            con.execute("INSERT OR REPLACE INTO heartbeats(agent, ts) VALUES (?, ?)", (self.name, utc_now()))
            con.commit()
        finally:
            con.close()
        return {
            "agent": self.name,
            "mint": mint,
            "ts": _iso(),
            "answers": answers,
            "score": score,
            "passed": passed,
            "failed": failed,
            "model": model,
            "inputTokens": input_tokens,
            "state": state,
            "price_usd": ctx.get("price_usd"),
            "liquidity_usd": ctx.get("liq"),
            "name": ctx.get("name"),
            "symbol": ctx.get("symbol"),
        }

    async def emit(self, result: dict[str, Any]) -> None:
        if self.bus.is_paused(self.name):
            return
        if result.get("passed"):
            await self.bus.publish(busmod.ANALYST_PASS, result)
        else:
            await self.bus.publish(busmod.NOTIFY, {
                "kind": "rejected", "agent": self.name, "mint": result.get("mint"),
                "text": f"PAPER · Rejected · Analyst · {(result.get('failed') or ['?'])[0]}",
            })
