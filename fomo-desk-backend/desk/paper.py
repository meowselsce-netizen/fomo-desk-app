"""PAPER desk commands. Nothing here sends an order or a Telegram message.

    python -m desk.paper --db var/paper/desk.sqlite baseline
    python -m desk.paper --db var/paper/desk.sqlite health
    python -m desk.paper --db var/paper/desk.sqlite smoke-public
    python -m desk.paper --db var/paper/desk.sqlite once
    python -m desk.paper --db var/paper/desk.sqlite report
    python -m desk.paper --db var/paper/desk.sqlite run
"""
from __future__ import annotations

import argparse
import asyncio
import json
import signal
import sys
import time
from pathlib import Path

from desk.db import default_db_path
from desk.links import coin_url
from desk.market import (
    DEX_BOOSTS,
    DEX_TOKENS,
    RUG_REPORT,
    MarketOffline,
    aiohttp_fetch,
    pairs_from_tokens,
    probe_pump_migration,
    solana_mints_from_boosts,
)
from desk.paper_runner import PaperRunner, build_http_client, health, report


def _db_from(args: argparse.Namespace) -> Path:
    if args.db:
        return Path(args.db)
    return default_db_path()


def _print(payload: dict) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


async def smoke_public() -> dict:
    """Bounded read-only check of public market sources. Writes no database."""
    started = time.monotonic()
    out: dict = {
        "simulated": False,
        "orders_sent": False,
        "positions_written": False,
        "telegram_sent": False,
        "sources": {},
    }
    status, body = await aiohttp_fetch(DEX_BOOSTS)
    mints = solana_mints_from_boosts(body, limit=3) if status == 200 else []
    out["sources"]["dexscreener_boosts"] = {"http": status, "solana_mints": len(mints)}
    pair_fields: list[str] = []
    mint = mints[0] if mints else None
    if mint:
        pair_status, pair_body = await aiohttp_fetch(DEX_TOKENS.format(mints=mint))
        pairs = pairs_from_tokens(pair_body) if pair_status == 200 else []
        if pairs:
            pair_fields = sorted(k for k in ("priceUsd", "liquidity", "pairCreatedAt", "txns", "priceChange", "baseToken") if k in pairs[0])
            chain = pairs[0].get("chainId")
        else:
            chain = None
        out["sources"]["dexscreener_token"] = {
            "http": pair_status,
            "chain": chain,
            "fields": pair_fields,
        }
        rug_status, rug_body = await aiohttp_fetch(RUG_REPORT.format(mint=mint))
        keys = []
        if isinstance(rug_body, dict):
            keys = sorted(
                k for k in (
                    "mintAuthority", "freezeAuthority", "topHolders", "markets",
                    "creatorBalance", "devSold0", "maxDelegateAccounts", "risks",
                ) if k in rug_body
            )
        out["sources"]["rugcheck"] = {"http": rug_status, "keys_present": keys}
        url = coin_url(mint, "solana")
        out["fomo_url_ok"] = "r=savipww" in url and "chainId=1399811149" in url
    else:
        out["fomo_url_ok"] = False
    out["sources"]["pumpportal"] = await probe_pump_migration(3.0)
    out["elapsed_s"] = round(time.monotonic() - started, 3)
    dex_ok = out["sources"]["dexscreener_boosts"]["http"] == 200
    out["ok"] = bool(dex_ok)
    if not dex_ok:
        out["blocker"] = "DexScreener did not return HTTP 200. Do not start the continuous runner."
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PAPER-only FOMO desk. No live orders.")
    parser.add_argument("--db", help="SQLite file. Default: var/paper/desk.sqlite inside this package. Do not use the static Pages data directory.")
    parser.add_argument(
        "command",
        choices=("baseline", "health", "report", "once", "run", "smoke-public"),
        help="baseline creates an empty run; once is one cycle; run loops until SIGINT",
    )
    args = parser.parse_args(argv)
    db = _db_from(args)
    if args.command == "baseline":
        _print(PaperRunner(db).baseline())
        return 0
    if args.command == "health":
        _print(health(db))
        return 0
    if args.command == "report":
        _print(report(db))
        return 0
    if args.command == "smoke-public":
        payload = asyncio.run(smoke_public())
        _print(payload)
        return 0 if payload.get("ok") else 2
    if args.command == "once":
        runner = PaperRunner(db, build_http_client())
        _print(asyncio.run(runner.once()))
        return 0
    if args.command == "run":
        runner = PaperRunner(db, build_http_client())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, runner.stop.set)
            except NotImplementedError:
                signal.signal(sig, lambda *_: runner.stop.set())
        try:
            loop.run_until_complete(runner.run_forever())
        finally:
            loop.close()
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except MarketOffline as exc:
        print(json.dumps({"ok": False, "blocker": str(exc), "orders_sent": False}), file=sys.stderr)
        raise SystemExit(2)
