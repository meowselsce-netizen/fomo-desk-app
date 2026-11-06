"""Offline PAPER runner tests. No network, no Telegram, no orders."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from desk.db import PaperOnlyError, connect, migrate, set_setting, use_db, reset_db
from desk.fees import round_trip_fees, side_fee
from desk.links import CHAIN_ID, coin_url, profile_url
from desk.market import HostLimiter, MarketOffline, Quote
from desk.paper_runner import PaperRunner, health, report
from desk.process_lock import DuplicateProcess, ProcessLock
from desk.telegram_bot import TelegramBot

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
MINT = "PaperMint111111111111111111111111111111111"


def _facts() -> dict:
    return {
        "mint_authority": None,
        "freeze_authority": None,
        "supply": 1_000_000,
        "creator_balance_pct": 1.0,
        "dev_sold_0": True,
        "top10_pct": 10.0,
        "lp_locked_pct": 100.0,
        "age_min": 40.0,
        "bundled_count": 0,
        "max_delegate_accounts": 0,
        "holders": 40,
    }


def _market() -> dict:
    return {
        "name": "Paper",
        "symbol": "PPR",
        "liquidity_usd": 80_000.0,
        "top10_pct": 10.0,
        "price_change_h1": 8.0,
        "price_change_m5": -3.0,
        "age_min": 40.0,
        "price_usd": 1.0,
        "market_cap": 200_000.0,
        "buys_m5": 30,
        "sells_m5": 10,
        "volume_m5": 2000,
        "buys_h1": 100,
        "sells_h1": 40,
        "volume_h1": 8000,
    }


def _ctx() -> dict:
    m = _market()
    return {
        "name": "Paper", "symbol": "PPR", "age_min": 40, "liq": 80_000, "mcap": 200_000,
        "b5": m["buys_m5"], "s5": m["sells_m5"], "v5": m["volume_m5"], "p5": m["price_change_m5"],
        "b1": m["buys_h1"], "s1": m["sells_h1"], "v1": m["volume_h1"], "p1": m["price_change_h1"],
        "top10": 10, "dev": 1, "lp": "100%", "bund": 0, "del": 0, "price_usd": 1.0,
    }


class FakeMarket:
    def __init__(self) -> None:
        self.fail = False
        self.cands: list = []
        self.quotes: dict[str, Quote | None] = {}

    async def candidates(self):
        if self.fail:
            raise MarketOffline("source down")
        return list(self.cands)

    async def quote(self, mint: str):
        if self.fail:
            raise MarketOffline("source down")
        return self.quotes.get(mint)


def _cand(fetched_at: datetime | None = None, price: float = 1.0, event_id: str = "dex:paper:1"):
    from desk.market import Candidate
    market = _market()
    market["price_usd"] = price
    return Candidate(
        event_id=event_id,
        source="fixture",
        mint=MINT,
        market=market,
        facts=_facts(),
        analyst_ctx=_ctx(),
        fetched_at=fetched_at or NOW,
        price_usd=price,
        liquidity_usd=80_000.0,
        name="Paper",
        symbol="PPR",
        pair="pair-1",
    )


def _hold_quote(price: float = 1.01, fetched_at: datetime | None = None) -> Quote:
    return Quote(MINT, price, 80_000.0, fetched_at or NOW)


def _positions(db: Path, run_id: str | None = None):
    tok = use_db(db)
    try:
        con = connect()
        try:
            if run_id:
                return con.execute(
                    "SELECT * FROM positions WHERE run_id = ? ORDER BY id", (run_id,)
                ).fetchall()
            return con.execute("SELECT * FROM positions ORDER BY id").fetchall()
        finally:
            con.close()
    finally:
        reset_db(tok)


def test_links_keep_referral_and_solana_chain():
    url = coin_url(MINT, "solana")
    assert "r=savipww" in url
    assert f"chainId={CHAIN_ID['solana']}" in url
    assert CHAIN_ID["solana"] == 1399811149
    assert "r=savipww" in profile_url("AsgerJepsen")
    assert "r=savipww" in url


def test_live_setting_and_telegram_are_rejected(tmp_path: Path):
    db = tmp_path / "desk.sqlite"
    tok = use_db(db)
    try:
        migrate()
        with pytest.raises(PaperOnlyError):
            con = connect()
            try:
                set_setting(con, "mode", "LIVE", who="test")
                con.commit()
            finally:
                con.close()
        con = connect()
        try:
            assert con.execute("SELECT value FROM settings WHERE key='mode'").fetchone()["value"] == "PAPER"
            con.execute("UPDATE settings SET value='LIVE' WHERE key='mode'")
            con.commit()
        finally:
            con.close()
    finally:
        reset_db(tok)
    runner = PaperRunner(db, FakeMarket())
    with pytest.raises(PaperOnlyError):
        asyncio.run(runner.once())
    tok = use_db(db)
    try:
        con = connect()
        try:
            assert con.execute("SELECT value FROM settings WHERE key='mode'").fetchone()["value"] == "LIVE"
            assert con.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"] == 0
        finally:
            con.close()
        bot = TelegramBot()
        assert "PAPER-only" in bot.cmd_mode("LIVE")
        assert "PAPER-only" in bot.cmd_mode("live")
        assert "rejected" in bot.cmd_set("set mode to LIVE").lower()
        assert con_mode_still_live(db)
    finally:
        reset_db(tok)


def con_mode_still_live(db: Path) -> bool:
    con = connect()
    try:
        return con.execute("SELECT value FROM settings WHERE key='mode'").fetchone()["value"] == "LIVE"
    finally:
        con.close()


def test_stale_price_does_not_fill_or_exit(tmp_path: Path):
    db = tmp_path / "desk.sqlite"
    fake = FakeMarket()
    fake.cands = [_cand(fetched_at=NOW - timedelta(seconds=120))]
    fake.quotes[MINT] = _hold_quote(fetched_at=NOW - timedelta(seconds=120))
    result = asyncio.run(PaperRunner(db, fake).once(fresh=True, now=NOW))
    assert result["opened"] == 0
    assert result["reasons"].get("stale price") == 1
    assert _positions(db) == []


def test_stop_trail_timeout_and_cash(tmp_path: Path):
    db = tmp_path / "desk.sqlite"
    fake = FakeMarket()
    fake.cands = [_cand()]
    fake.quotes[MINT] = _hold_quote(0.90)
    first = asyncio.run(PaperRunner(db, fake).once(fresh=True, now=NOW))
    assert first["opened"] == 1
    assert first["closed"] == 1
    rows = _positions(db, first["run_id"])
    assert len(rows) == 1
    assert rows[0]["exit_reason"] == "stop"
    assert rows[0]["mode"] == "PAPER"
    entry = float(rows[0]["entry_price"])
    exit_px = float(rows[0]["exit_price"])
    size = float(rows[0]["size_usd"])
    fees = float(rows[0]["fees_usd"])
    pnl = float(rows[0]["pnl_usd"])
    assert entry == pytest.approx(1.0 * 1.003)
    assert exit_px == pytest.approx(0.90 * 0.997)
    assert fees == pytest.approx(round_trip_fees(size, entry, exit_px, 30))
    assert pnl == pytest.approx(size * (exit_px / entry - 1) - fees)
    buy_fee = side_fee(size, 30)
    sell_fee = side_fee(size * (exit_px / entry), 30)
    assert first["cash_usd"] == pytest.approx(200 - size - buy_fee + size * (exit_px / entry) - sell_fee)

    # Trail on a second mint: arm, then give back more than 12% from the peak.
    mint2 = "PaperMint222222222222222222222222222222222"
    from desk.market import Candidate
    c2 = Candidate(
        event_id="dex:paper:2", source="fixture", mint=mint2, market={**_market(), "symbol": "PP2"},
        facts=_facts(), analyst_ctx={**_ctx(), "symbol": "PP2"}, fetched_at=NOW,
        price_usd=1.0, liquidity_usd=80_000, name="Paper", symbol="PP2", pair="pair-2",
    )
    fake.cands = [c2]
    fake.quotes[MINT] = _hold_quote(1.01)
    fake.quotes[mint2] = _hold_quote(1.30)
    fake.quotes[mint2].mint = mint2
    armed = asyncio.run(PaperRunner(db, fake).once(now=NOW))
    assert armed["opened"] == 1 and armed["closed"] == 0
    fake.cands = []
    fake.quotes[mint2] = Quote(mint2, 1.10, 80_000, NOW)
    trailed = asyncio.run(PaperRunner(db, fake).once(now=NOW))
    assert trailed["closed"] == 1
    trail_row = [r for r in _positions(db, armed["run_id"]) if r["mint"] == mint2][0]
    assert trail_row["exit_reason"] == "trail"

    mint3 = "PaperMint333333333333333333333333333333333"
    c3 = Candidate(
        event_id="dex:paper:3", source="fixture", mint=mint3, market={**_market(), "symbol": "PP3"},
        facts=_facts(), analyst_ctx={**_ctx(), "symbol": "PP3"}, fetched_at=NOW,
        price_usd=1.0, liquidity_usd=80_000, name="Paper", symbol="PP3", pair="pair-3",
    )
    fake.cands = [c3]
    fake.quotes[mint3] = Quote(mint3, 1.006, 80_000, NOW)
    opened = asyncio.run(PaperRunner(db, fake).once(now=NOW))
    assert opened["opened"] == 1
    tok = use_db(db)
    try:
        con = connect()
        try:
            old = (NOW - timedelta(hours=3, minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
            con.execute(
                "UPDATE positions SET opened_at=? WHERE mint=? AND run_id=?",
                (old, mint3, opened["run_id"]),
            )
            con.commit()
        finally:
            con.close()
    finally:
        reset_db(tok)
    fake.cands = []
    timed = asyncio.run(PaperRunner(db, fake).once(now=NOW))
    assert timed["closed"] >= 1
    timeout_row = [r for r in _positions(db, opened["run_id"]) if r["mint"] == mint3][0]
    assert timeout_row["exit_reason"] == "timeout"


def test_duplicate_event_restart_and_second_process(tmp_path: Path):
    db = tmp_path / "desk.sqlite"
    fake = FakeMarket()
    fake.cands = [_cand(), _cand()]
    fake.quotes[MINT] = _hold_quote(1.01)
    first = asyncio.run(PaperRunner(db, fake).once(fresh=True, now=NOW))
    assert first["opened"] == 1
    assert len(_positions(db, first["run_id"])) == 1
    again = asyncio.run(PaperRunner(db, fake).once(now=NOW))
    assert again["opened"] == 0
    assert again["run_id"] == first["run_id"]
    assert len(_positions(db, first["run_id"])) == 1
    held = ProcessLock(db.with_suffix(".lock"))
    held.acquire()
    try:
        with pytest.raises(DuplicateProcess):
            asyncio.run(PaperRunner(db, fake).once(now=NOW))
    finally:
        held.release()
    assert len(_positions(db, first["run_id"])) == 1


def test_source_down_leaves_position_open(tmp_path: Path):
    db = tmp_path / "desk.sqlite"
    fake = FakeMarket()
    fake.cands = [_cand()]
    fake.quotes[MINT] = _hold_quote(1.01)
    first = asyncio.run(PaperRunner(db, fake).once(fresh=True, now=NOW))
    assert first["opened"] == 1
    fake.fail = True
    fake.cands = [_cand(event_id="dex:paper:other")]
    down = asyncio.run(PaperRunner(db, fake).once(now=NOW))
    assert down["opened"] == 0
    assert down["source_errors"]
    rows = _positions(db, first["run_id"])
    assert rows[0]["closed_at"] is None


def test_missing_audit_fact_fails_closed_without_a_fill(tmp_path: Path):
    db = tmp_path / "desk.sqlite"
    bad = _cand()
    bad.facts.pop("mint_authority")
    fake = FakeMarket()
    fake.cands = [bad]
    fake.quotes[MINT] = _hold_quote()
    result = asyncio.run(PaperRunner(db, fake).once(fresh=True, now=NOW))
    assert result["opened"] == 0
    assert any("no data: mint authority" in k for k in result["reasons"])
    assert _positions(db) == []


def test_legacy_rows_are_not_counted_or_rewritten(tmp_path: Path):
    db = tmp_path / "desk.sqlite"
    tok = use_db(db)
    try:
        migrate()
        con = connect()
        try:
            con.execute(
                "INSERT INTO coins(mint, name, symbol) VALUES (?,?,?)",
                ("LegacyMint11111111111111111111111111111111", "Old", "OLD"),
            )
            con.execute(
                "INSERT INTO positions(mint, opened_at, entry_price, size_usd, peak_price, stop_price, mode, run_id) "
                "VALUES (?,?,?,?,?,?,?,NULL)",
                ("LegacyMint11111111111111111111111111111111", "2020-01-01T00:00:00Z", 1, 10, 1, 0.9, "PAPER"),
            )
            con.commit()
        finally:
            con.close()
    finally:
        reset_db(tok)
    fake = FakeMarket()
    summary = PaperRunner(db, fake).baseline()
    assert summary["legacy_or_demo_positions"] == 1
    assert summary["actual_entries"] == 0
    assert summary["profit_claim"] is False
    tok = use_db(db)
    try:
        con = connect()
        try:
            row = con.execute(
                "SELECT opened_at, closed_at, run_id FROM positions WHERE mint LIKE 'Legacy%'"
            ).fetchone()
            assert row["run_id"] is None
            assert row["opened_at"] == "2020-01-01T00:00:00Z"
            assert row["closed_at"] is None
        finally:
            con.close()
    finally:
        reset_db(tok)
    body = health(db)
    text = str(body)
    for banned in ("TG_TOKEN", "APP_TOKEN", "DATA_KEY", "VAPID_PRIVATE_KEY", "TYPESAFE_API_KEY"):
        assert banned not in text
    assert body["mode"] == "PAPER"
    assert body["journal_mode"].lower() == "wal"


def test_shutdown_is_graceful_and_limiter_backs_off():
    sleeps: list[float] = []

    async def record(delay: float) -> None:
        sleeps.append(delay)

    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    limiter = HostLimiter(min_interval_s=1.0, max_backoff_s=8.0, sleep=record, clock=now)
    asyncio.run(limiter.wait("dex"))
    limiter.penalize("dex")
    clock["t"] = 1.0
    asyncio.run(limiter.wait("dex"))
    assert sleeps[-1] >= 1.0


def test_run_forever_stops_without_trading(tmp_path: Path):
    db = tmp_path / "desk.sqlite"
    runner = PaperRunner(db, FakeMarket())
    runner.stop.set()
    asyncio.run(runner.run_forever())
    tok = use_db(db)
    try:
        con = connect()
        try:
            row = con.execute("SELECT ended_at FROM paper_runs").fetchone()
            assert row["ended_at"]
            assert con.execute("SELECT COUNT(*) AS n FROM positions").fetchone()["n"] == 0
        finally:
            con.close()
    finally:
        reset_db(tok)


def test_no_forbidden_identifier_in_backend_source():
    root = Path(__file__).resolve().parents[1]
    hits = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {".venv", "__pycache__", "var", "logs"} for part in path.parts):
            continue
        if path.suffix.lower() not in {".py", ".md", ".sql", ".txt", ".sh"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if ("DATA_" + "PATH") in text:
            hits.append(str(path))
    assert hits == []
