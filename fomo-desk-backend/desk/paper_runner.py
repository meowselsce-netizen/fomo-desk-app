"""One asyncio process: Scanner, Auditor, Analyst, Executor, Risk.

PAPER fills only. Public quotes only. No order API.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from desk.agents import Analyst, Auditor, Executor, Risk, Scanner
from desk.bus import Bus
from desk.db import (
    PaperOnlyError,
    assert_paper_state,
    connect,
    current_run_id,
    migrate,
    reset_db,
    reset_run,
    use_db,
    use_run,
    utc_now,
)
from desk.facts import analyst_ctx, audit_facts_from_rug, market_from_pair
from desk.fees import side_fee, slipped_price
from desk.market import Candidate, HttpMarket, MarketOffline, Quote, aiohttp_fetch
from desk.process_lock import ProcessLock
from desk.settings_defaults import DEFAULTS

log = logging.getLogger("paper")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ms() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_run_id(now: datetime | None = None) -> str:
    stamp = (now or _now()).strftime("%Y%m%dT%H%M%S%fZ")
    return f"paper-{stamp}"


class PaperRunner:
    def __init__(self, db_path: Path, client: Any | None = None, run_id: str | None = None) -> None:
        self.db_path = Path(db_path)
        self.client = client
        self.run_id = run_id
        self.stop = asyncio.Event()
        self.bus = Bus()
        self.scanner = Scanner(self.bus)
        self.auditor = Auditor(self.bus)
        self.analyst = Analyst(self.bus)
        self.executor = Executor(self.bus)
        self.risk = Risk(self.bus)
        self.lock = ProcessLock(self.db_path.with_suffix(".lock"))
        self._db_tok = None
        self._run_tok = None

    def _bind_db(self) -> None:
        self._db_tok = use_db(self.db_path)

    def _bind_run(self) -> None:
        self._run_tok = use_run(self.run_id)

    def _unbind(self) -> None:
        if self._run_tok is not None:
            reset_run(self._run_tok)
            self._run_tok = None
        if self._db_tok is not None:
            reset_db(self._db_tok)
            self._db_tok = None

    @property
    def run_file(self) -> Path:
        return self.db_path.with_suffix(".run")

    def prepare(self, fresh: bool = False) -> str:
        migrate()
        con = connect()
        try:
            assert_paper_state(con)
            if fresh:
                self.run_id = new_run_id()
            elif not self.run_id and self.run_file.exists():
                self.run_id = self.run_file.read_text(encoding="utf-8").strip() or None
            if self.run_id:
                row = con.execute("SELECT run_id FROM paper_runs WHERE run_id = ?", (self.run_id,)).fetchone()
                if row is None:
                    self.run_id = None
            if not self.run_id:
                self.run_id = new_run_id()
                con.execute(
                    "INSERT INTO paper_runs(run_id, started_at, kind, note) VALUES (?,?,?,?)",
                    (self.run_id, utc_now(), "paper", "simulated paper run"),
                )
            else:
                con.execute("UPDATE paper_runs SET ended_at = NULL WHERE run_id = ?", (self.run_id,))
            seeded = con.execute(
                "SELECT id FROM cash_ledger WHERE run_id = ? AND kind = 'seed'", (self.run_id,)
            ).fetchone()
            if seeded is None:
                stack = float(con.execute(
                    "SELECT value FROM settings WHERE key = 'executor.stack_usd'"
                ).fetchone()["value"])
                con.execute(
                    "INSERT INTO cash_ledger(run_id, ts, kind, amount_usd, note) VALUES (?,?,?,?,?)",
                    (self.run_id, _ms(), "seed", stack, "stack"),
                )
            self.run_file.write_text(self.run_id + "\n", encoding="utf-8")
            con.commit()
            return self.run_id
        finally:
            con.close()

    def _setting(self, key: str) -> str:
        con = connect()
        try:
            row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            if row:
                return row["value"]
            return DEFAULTS[key]
        finally:
            con.close()

    def _fee_bps(self) -> float:
        return float(self._setting("paper.fee_bps"))

    def _slip_bps(self) -> float:
        return float(self._setting("paper.slippage_bps"))

    def _max_age(self) -> float:
        return float(self._setting("paper.max_quote_age_s"))

    def _cash(self) -> float:
        con = connect()
        try:
            row = con.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) AS v FROM cash_ledger WHERE run_id = ?",
                (self.run_id,),
            ).fetchone()
            return float(row["v"])
        finally:
            con.close()

    def _exposure(self) -> float:
        con = connect()
        try:
            row = con.execute(
                "SELECT COALESCE(SUM(size_usd), 0) AS v FROM positions WHERE run_id = ? AND closed_at IS NULL",
                (self.run_id,),
            ).fetchone()
            return float(row["v"])
        finally:
            con.close()

    def _health(self, source: str, ok: bool, detail: str) -> None:
        con = connect()
        try:
            con.execute(
                "INSERT INTO source_health(run_id, ts, source, ok, detail) VALUES (?,?,?,?,?)",
                (self.run_id, utc_now(), source, 1 if ok else 0, detail[:500]),
            )
            con.commit()
        finally:
            con.close()

    def _activity(self, kind: str, mint: str | None, agent: str, title: str, subtitle: str) -> None:
        con = connect()
        try:
            con.execute(
                "INSERT INTO activity(ts, kind, mint, agent, title, subtitle, value, run_id) VALUES (?,?,?,?,?,?,?,?)",
                (utc_now(), kind, mint, agent, title, subtitle, "Skipped", self.run_id),
            )
            con.commit()
        finally:
            con.close()

    def _ledger(self, kind: str, amount: float, mint: str | None, position_id: int | None, note: str) -> None:
        con = connect()
        try:
            con.execute(
                "INSERT INTO cash_ledger(run_id, ts, mint, kind, amount_usd, position_id, note) VALUES (?,?,?,?,?,?,?)",
                (self.run_id, _ms(), mint, kind, amount, position_id, note),
            )
            con.execute(
                "INSERT INTO balance(ts, value, run_id) VALUES (?,?,?)",
                (_ms(), self._cash_in(con) , self.run_id),
            )
            con.commit()
        finally:
            con.close()

    def _cash_in(self, con) -> float:
        row = con.execute(
            "SELECT COALESCE(SUM(amount_usd), 0) AS v FROM cash_ledger WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        return float(row["v"])

    def _quote_fresh(self, fetched_at: datetime, now: datetime | None = None) -> bool:
        now = now or _now()
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        return (now - fetched_at).total_seconds() <= self._max_age()

    async def cycle(self, now: datetime | None = None) -> dict[str, Any]:
        con = connect()
        try:
            assert_paper_state(con)
        finally:
            con.close()
        now = now or _now()
        reasons: dict[str, int] = {}
        opened = 0
        closed = 0
        seen = 0
        errors: list[str] = []
        if current_run_id() != self.run_id:
            raise PaperOnlyError("paper run is not bound")
        try:
            candidates = await self._candidates(now)
            self._health("market", True, f"{len(candidates)} candidates")
        except MarketOffline as exc:
            candidates = []
            errors.append(str(exc))
            self._health("market", False, str(exc))
            log.warning("market source down: %s", exc)
        for cand in candidates:
            if self.stop.is_set():
                break
            seen += 1
            outcome = await self._consider(cand, now)
            if outcome == "opened":
                opened += 1
            elif outcome:
                reasons[outcome] = reasons.get(outcome, 0) + 1
        closed = await self._manage_open(now, errors)
        note = None
        if opened == 0:
            note = "No token qualified." if not reasons and not errors else "No new simulated entry."
        return {
            "run_id": self.run_id,
            "seen": seen,
            "opened": opened,
            "closed": closed,
            "reasons": reasons,
            "source_errors": errors,
            "note": note,
            "cash_usd": round(self._cash(), 4),
            "exposure_usd": round(self._exposure(), 4),
            "simulated": True,
        }

    async def _candidates(self, now: datetime) -> list[Candidate]:
        if self.client is None:
            raise MarketOffline("no market client")
        if hasattr(self.client, "candidates"):
            return await self.client.candidates()
        pairs = await self.client.list_pairs()
        built: list[Candidate] = []
        for pair in pairs:
            market = market_from_pair(pair, now)
            mint = market.get("mint")
            if not mint:
                continue
            pre_fail = _known_scanner_fail(market)
            facts: dict[str, Any] = {}
            if pre_fail is None:
                try:
                    rug = await self.client.rug_report(mint)
                    facts = audit_facts_from_rug(rug, market)
                except MarketOffline as exc:
                    self._health("rugcheck", False, f"{mint} {exc}")
                    facts = audit_facts_from_rug(None, market)
            else:
                facts = audit_facts_from_rug(None, market)
            if facts.get("top10_pct") is not None:
                market["top10_pct"] = facts["top10_pct"]
            ctx = analyst_ctx(market, facts)
            fetched = now
            built.append(Candidate(
                event_id=f"{pair.get('_desk_source') or 'dex'}:{mint}:{pair.get('pairAddress') or ''}",
                source=str(pair.get("_desk_source") or "dexscreener"),
                mint=str(mint),
                market=market,
                facts=facts,
                analyst_ctx=ctx,
                fetched_at=fetched,
                price_usd=market.get("price_usd"),
                liquidity_usd=market.get("liquidity_usd"),
                name=market.get("name"),
                symbol=market.get("symbol"),
                pair=pair.get("pairAddress"),
            ))
        return built

    async def _consider(self, cand: Candidate, now: datetime) -> str | None:
        if not self._quote_fresh(cand.fetched_at, now):
            self._activity("rejected", cand.mint, "scanner", f"Rejected {cand.symbol or cand.mint[:6]}", "stale price")
            return "stale price"
        if cand.price_usd is None or cand.price_usd <= 0:
            self._activity("rejected", cand.mint, "scanner", f"Rejected {cand.symbol or cand.mint[:6]}", "no data: price")
            return "no data: price"
        self.scanner.ingest_migration({
            "mint": cand.mint, "pool": cand.pair, "name": cand.name, "symbol": cand.symbol,
        })
        scan = self.scanner.evaluate(cand.mint, cand.market, ran_today=self._symbols_today())
        if not scan.get("passed"):
            return scan.get("reason") or "scanner rejected"
        audit = self.auditor.audit(cand.mint, cand.facts)
        if not audit.get("passed"):
            return audit.get("reason") or "auditor rejected"
        analysed = self.analyst.analyze(cand.mint, cand.analyst_ctx)
        if not analysed.get("passed"):
            failed = analysed.get("failed") or ["analyst rejected"]
            return f"Analyst · {failed[0]}"
        block = self._cash_block(float(cand.liquidity_usd or 0))
        if block:
            self._activity("rejected", cand.mint, "executor", f"Rejected {cand.symbol or cand.mint[:6]}", block)
            return block
        fill = slipped_price(float(cand.price_usd), "buy", self._slip_bps())
        opened = self.executor.open_paper(
            cand.mint, fill, float(cand.liquidity_usd or 0),
            name=cand.name, symbol=cand.symbol, event_id=cand.event_id,
            stack_usd=self._affordable_stack(),
        )
        if not opened.get("passed"):
            return opened.get("reason") or opened.get("status") or "not opened"
        size = float(opened["size_usd"])
        fee = side_fee(size, self._fee_bps())
        self._ledger("open", -(size + fee), cand.mint, opened.get("position_id"), "simulated entry")
        await self.executor.emit(opened)
        return "opened"

    def _symbols_today(self) -> list[str]:
        con = connect()
        try:
            rows = con.execute(
                "SELECT DISTINCT symbol FROM coins WHERE mint IN ("
                "SELECT mint FROM positions WHERE run_id = ?)",
                (self.run_id,),
            ).fetchall()
            return [r["symbol"] for r in rows if r["symbol"]]
        finally:
            con.close()

    def _affordable_stack(self) -> float:
        stack = float(self._setting("executor.stack_usd"))
        remaining = max(0.0, stack - self._exposure())
        fee_rate = self._fee_bps() / 10_000
        cash = self._cash()
        cash_room = cash / (1 + fee_rate) if fee_rate >= 0 else cash
        raw = max(0.0, min(stack, remaining, cash_room))
        cents = math.floor((raw + 1e-9) * 100) / 100
        while cents > 0 and cents + side_fee(cents, self._fee_bps()) > cash + 1e-9:
            cents = round(cents - 0.01, 2)
        return max(0.0, cents)

    def _cash_block(self, liquidity_usd: float) -> str | None:
        preview = self.executor.size_order(self._affordable_stack(), liquidity_usd)
        if preview["skipped"] or preview["size_usd"] <= 0:
            if self._exposure() + 1e-9 >= float(self._setting("executor.stack_usd")):
                return "exposure limit"
            if self._cash() <= 0:
                return "cash limit"
            return preview.get("warning") or "size skipped"
        fee = side_fee(preview["size_usd"], self._fee_bps())
        if preview["size_usd"] + fee > self._cash() + 1e-6:
            return "cash limit"
        return None

    async def _manage_open(self, now: datetime, errors: list[str]) -> int:
        con = connect()
        try:
            rows = con.execute(
                "SELECT id, mint FROM positions WHERE run_id = ? AND closed_at IS NULL AND mode = 'PAPER'",
                (self.run_id,),
            ).fetchall()
        finally:
            con.close()
        closed = 0
        for row in rows:
            if self.stop.is_set():
                break
            quote = await self._quote(row["mint"], now, errors)
            if quote is None or not self._quote_fresh(quote.fetched_at, now) or quote.price_usd is None:
                self._health("quote", False, f"stale or missing {row['mint']}")
                continue
            fill = slipped_price(float(quote.price_usd), "sell", self._slip_bps())
            result = self.risk.poll_position(row["id"], fill, quote.liquidity_usd, now=now)
            if result.get("status") == "closed":
                entry = float(result["entryPrice"])
                exit_px = float(result["exitPrice"])
                con = connect()
                try:
                    pos = con.execute("SELECT size_usd FROM positions WHERE id = ?", (row["id"],)).fetchone()
                    size = float(pos["size_usd"])
                finally:
                    con.close()
                proceeds = size * (exit_px / entry) if entry else 0.0
                sell_fee = side_fee(proceeds, self._fee_bps())
                self._ledger("close", proceeds - sell_fee, row["mint"], row["id"], result.get("reason") or "close")
                await self.risk.emit(result)
                closed += 1
        return closed

    async def _quote(self, mint: str, now: datetime, errors: list[str]) -> Quote | None:
        try:
            if hasattr(self.client, "quote"):
                return await self.client.quote(mint)
            pair = await self.client.quote_pair(mint)
        except MarketOffline as exc:
            errors.append(str(exc))
            return None
        if pair is None:
            return None
        market = market_from_pair(pair, now)
        return Quote(mint, market.get("price_usd"), market.get("liquidity_usd"), now, pair)

    async def once(self, fresh: bool = False, now: datetime | None = None) -> dict[str, Any]:
        self.lock.acquire()
        self._bind_db()
        try:
            self.prepare(fresh=fresh)
            self._bind_run()
            try:
                return await self.cycle(now)
            finally:
                if self._run_tok is not None:
                    reset_run(self._run_tok)
                    self._run_tok = None
        finally:
            self._unbind_db_only()
            self.lock.release()

    def _unbind_db_only(self) -> None:
        if self._db_tok is not None:
            reset_db(self._db_tok)
            self._db_tok = None

    async def run_forever(self) -> None:
        """Continuous PAPER loop for the operator's own machine.

        This process only simulates fills. It does not send orders. Stop with
        SIGINT or SIGTERM; open paper positions stay in the database.
        """
        self.lock.acquire()
        self._bind_db()
        try:
            self.prepare(fresh=False)
            self._bind_run()
            poll = float(self._setting("scanner.poll_s"))
            while not self.stop.is_set():
                try:
                    await self.cycle()
                except PaperOnlyError:
                    raise
                except Exception as exc:
                    log.warning("paper cycle error: %s", exc)
                    self._health("runner", False, type(exc).__name__)
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=poll)
                except asyncio.TimeoutError:
                    pass
            self._mark_stopped()
        finally:
            self._unbind()
            self.lock.release()

    def _mark_stopped(self) -> None:
        con = connect()
        try:
            con.execute("UPDATE paper_runs SET ended_at = ? WHERE run_id = ?", (utc_now(), self.run_id))
            con.commit()
        finally:
            con.close()

    def baseline(self) -> dict[str, Any]:
        """Create an empty paper run. Does not fetch markets or simulate fills."""
        self._bind_db()
        try:
            run_id = self.prepare(fresh=True)
            self._bind_run()
            try:
                return report(self.db_path, run_id)
            finally:
                if self._run_tok is not None:
                    reset_run(self._run_tok)
                    self._run_tok = None
        finally:
            self._unbind_db_only()


def _known_scanner_fail(market: dict[str, Any]) -> str | None:
    """Cheap reject before a RugCheck call. Does not pass a coin."""
    need = ("liquidity_usd", "price_change_h1", "age_min", "price_usd")
    if any(k not in market or market.get(k) is None for k in need):
        return "incomplete dex fields"
    return None


def report(db_path: Path, run_id: str | None = None) -> dict[str, Any]:
    tok = use_db(db_path)
    try:
        migrate()
        con = connect()
        try:
            if run_id is None and Path(db_path).with_suffix(".run").exists():
                run_id = Path(db_path).with_suffix(".run").read_text(encoding="utf-8").strip() or None
            legacy = con.execute("SELECT COUNT(*) AS n FROM positions WHERE run_id IS NULL").fetchone()["n"]
            body: dict[str, Any] = {
                "simulated": True,
                "profit_claim": False,
                "legacy_or_demo_positions": int(legacy),
                "legacy_note": "Rows with no run id are legacy or smoke data and are not actual paper results.",
                "run_id": run_id,
            }
            if not run_id:
                body["note"] = "No paper run yet."
                return body
            opens = con.execute(
                "SELECT COUNT(*) AS n FROM positions WHERE run_id = ?", (run_id,)
            ).fetchone()["n"]
            still = con.execute(
                "SELECT COUNT(*) AS n FROM positions WHERE run_id = ? AND closed_at IS NULL", (run_id,)
            ).fetchone()["n"]
            pnl = con.execute(
                "SELECT COALESCE(SUM(pnl_usd), 0) AS v, COALESCE(SUM(fees_usd), 0) AS f "
                "FROM positions WHERE run_id = ? AND closed_at IS NOT NULL",
                (run_id,),
            ).fetchone()
            cash = con.execute(
                "SELECT COALESCE(SUM(amount_usd), 0) AS v FROM cash_ledger WHERE run_id = ?",
                (run_id,),
            ).fetchone()["v"]
            rejects = con.execute(
                "SELECT subtitle, COUNT(*) AS n FROM activity WHERE run_id = ? AND kind = 'rejected' "
                "GROUP BY subtitle ORDER BY n DESC",
                (run_id,),
            ).fetchall()
            body.update({
                "actual_entries": int(opens),
                "open_positions": int(still),
                "simulated_realized_pnl_usd": round(float(pnl["v"]), 4),
                "simulated_fees_usd": round(float(pnl["f"]), 4),
                "cash_usd": round(float(cash), 4),
                "reject_reasons": {r["subtitle"]: r["n"] for r in rejects},
            })
            if int(opens) == 0:
                body["note"] = "No token qualified for a simulated entry in this run."
            return body
        finally:
            con.close()
    finally:
        reset_db(tok)


def health(db_path: Path) -> dict[str, Any]:
    path = Path(db_path)
    out: dict[str, Any] = {
        "ok": True,
        "mode": None,
        "journal_mode": None,
        "db_exists": path.exists(),
        "lock_path": path.with_suffix(".lock").name,
        "heartbeats": {},
        "sources": [],
    }
    if not path.exists():
        out["note"] = "No paper database yet. Run baseline."
        return out
    tok = use_db(path)
    try:
        con = connect()
        try:
            out["journal_mode"] = con.execute("PRAGMA journal_mode").fetchone()[0]
            row = con.execute("SELECT value FROM settings WHERE key = 'mode'").fetchone()
            out["mode"] = row["value"] if row else None
            out["heartbeats"] = {
                r["agent"]: r["ts"] for r in con.execute("SELECT agent, ts FROM heartbeats")
            }
            if path.with_suffix(".run").exists():
                run_id = path.with_suffix(".run").read_text(encoding="utf-8").strip()
                out["run_id"] = run_id
                out["sources"] = [
                    {"source": r["source"], "ok": bool(r["ok"]), "detail": r["detail"], "ts": r["ts"]}
                    for r in con.execute(
                        "SELECT source, ok, detail, ts FROM source_health WHERE run_id = ? ORDER BY id DESC LIMIT 8",
                        (run_id,),
                    )
                ]
            text = str(out)
            for banned in ("TG_TOKEN", "APP_TOKEN", "DATA_KEY", "VAPID_PRIVATE_KEY", "TYPESAFE_API_KEY"):
                if banned in text:
                    raise PaperOnlyError("health leaked a private env name")
            return out
        finally:
            con.close()
    finally:
        reset_db(tok)


def build_http_client() -> HttpMarket:
    limiter_s = float(DEFAULTS["paper.http_min_interval_s"])
    from desk.market import HostLimiter
    return HttpMarket(aiohttp_fetch, HostLimiter(min_interval_s=limiter_s), pair_limit=5)


def quote_is_stale(quote: Quote, max_age_s: float, now: datetime | None = None) -> bool:
    now = now or _now()
    return quote.fetched_at < now - timedelta(seconds=max_age_s)
