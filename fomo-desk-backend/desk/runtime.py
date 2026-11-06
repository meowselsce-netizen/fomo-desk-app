"""Thin asyncio supervisor wiring the 5 agents on the bus (STEP 5). Paper only."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from desk.agents import Analyst, Auditor, Executor, Risk, Scanner
from desk.bus import ANALYST_PASS, AUDITOR_PASS, Bus, SCANNER_PASS
from desk.db import migrate

log = logging.getLogger("runtime")


class DeskRuntime:
    def __init__(self) -> None:
        self.bus = Bus()
        self.scanner = Scanner(self.bus)
        self.auditor = Auditor(self.bus)
        self.analyst = Analyst(self.bus)
        self.executor = Executor(self.bus)
        self.risk = Risk(self.bus)
        self._tasks: list[asyncio.Task] = []
        self._ctx: dict[str, dict[str, Any]] = {}

    async def start_pipeline(self) -> None:
        self._tasks.append(asyncio.create_task(self._auditor_loop()))
        self._tasks.append(asyncio.create_task(self._analyst_loop()))
        self._tasks.append(asyncio.create_task(self._executor_loop()))
        await asyncio.sleep(0)  # let subscribers attach

    async def _auditor_loop(self) -> None:
        async for msg in self.bus.subscribe(SCANNER_PASS):
            mint = msg["mint"]
            self._ctx[mint] = msg
            facts = msg.get("audit_facts") or {}
            result = self.auditor.audit(mint, facts)
            # carry market fields for downstream
            result["liquidity_usd"] = msg.get("liquidity_usd")
            result["price_usd"] = msg.get("price_usd")
            result["name"] = msg.get("name")
            result["symbol"] = msg.get("symbol")
            result["analyst_ctx"] = msg.get("analyst_ctx")
            await self.auditor.emit(result)

    async def _analyst_loop(self) -> None:
        async for msg in self.bus.subscribe(AUDITOR_PASS):
            mint = msg["mint"]
            prev = self._ctx.get(mint, {})
            ctx = msg.get("analyst_ctx") or prev.get("analyst_ctx")
            if not ctx:
                # Do not invent flow counts. Missing flow fails the rules gate.
                ctx = {
                    "name": msg.get("name") or prev.get("name"),
                    "symbol": msg.get("symbol") or prev.get("symbol"),
                    "age_min": prev.get("age_min"),
                    "liq": msg.get("liquidity_usd") or prev.get("liquidity_usd") or 0,
                    "mcap": prev.get("mcap"),
                    "top10": prev.get("top10_pct"),
                    "price_usd": msg.get("price_usd") or prev.get("price_usd"),
                }
            result = self.analyst.analyze(mint, ctx)
            result["liquidity_usd"] = ctx.get("liq") or msg.get("liquidity_usd")
            result["price_usd"] = ctx.get("price_usd") or msg.get("price_usd")
            result["name"] = ctx.get("name")
            result["symbol"] = ctx.get("symbol")
            await self.analyst.emit(result)

    async def _executor_loop(self) -> None:
        async for msg in self.bus.subscribe(ANALYST_PASS):
            price = float(msg.get("price_usd") or 0)
            liq = float(msg.get("liquidity_usd") or 0)
            if price <= 0:
                continue
            result = self.executor.open_paper(
                msg["mint"], price, liq,
                name=msg.get("name"), symbol=msg.get("symbol"),
            )
            await self.executor.emit(result)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks.clear()


def ensure_db() -> None:
    migrate()
