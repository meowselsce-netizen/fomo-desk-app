"""Plan B JSON file shapes (same payloads as STEP 7 API).

Publisher writes these JSON shapes for the static snapshot. No secrets in the JSON.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from desk.db import all_settings
from desk.links import SIGNUP_URL, coin_url, profile_url

CHART_RANGES = ("LIVE", "1H", "4H", "1D", "1W", "ALL")
AGENTS = ("scanner", "auditor", "analyst", "executor", "risk")


def _parse_ts(ts: str) -> datetime:
    ts = (ts or "").replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return datetime.now(timezone.utc)


def _range_start(range_: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    if range_ == "ALL":
        return None
    if range_ == "LIVE":
        return now - timedelta(minutes=15)
    if range_ == "1H":
        return now - timedelta(hours=1)
    if range_ == "4H":
        return now - timedelta(hours=4)
    if range_ == "1D":
        return now - timedelta(days=1)
    if range_ == "1W":
        return now - timedelta(days=7)
    return now - timedelta(days=1)


def shape_state(con: sqlite3.Connection) -> dict[str, Any]:
    settings = all_settings(con)
    bal = con.execute("SELECT value FROM balance ORDER BY ts DESC LIMIT 1").fetchone()
    balance = float(bal["value"]) if bal else float(settings.get("executor.stack_usd", "200"))
    beats = {
        r["agent"]: r["ts"]
        for r in con.execute("SELECT agent, ts FROM heartbeats").fetchall()
    }
    agents = []
    for name in AGENTS:
        label = "waiting"
        if name == "analyst" and settings.get("analyst_mode") == "rules":
            label = "rules mode"
        status = "idle"
        if name in beats:
            status = "alive"
        agents.append({"name": name, "status": status, "label": label, "heartbeat": beats.get(name)})
    return {
        "balance": balance,
        "today_pct": 0.0,
        "multiplier": 1.0,
        "mode": settings.get("mode", "PAPER"),
        "agents": agents,
        "push": "telegram_only",
    }


def shape_chart(con: sqlite3.Connection, range_: str = "1D") -> dict[str, Any]:
    range_ = range_.upper() if range_ else "1D"
    if range_ not in CHART_RANGES:
        range_ = "1D"
    start = _range_start(range_)
    rows = con.execute("SELECT ts, value FROM balance ORDER BY ts").fetchall()
    points = []
    for r in rows:
        if start is not None and _parse_ts(r["ts"]) < start:
            continue
        points.append({"t": r["ts"], "v": r["value"]})
    marks = []
    for a in con.execute(
        "SELECT ts, kind, mint, title, value FROM activity ORDER BY ts DESC LIMIT 100"
    ).fetchall():
        if start is not None and _parse_ts(a["ts"]) < start:
            continue
        marks.append(
            {"t": a["ts"], "kind": a["kind"], "mint": a["mint"], "title": a["title"], "value": a["value"]}
        )
    return {"range": range_, "points": points, "marks": marks}


def shape_activity(con: sqlite3.Connection, before: str | None = None, limit: int = 30) -> dict[str, Any]:
    limit = max(1, min(int(limit or 30), 100))
    if before:
        rows = con.execute(
            "SELECT id, ts, kind, mint, agent, title, subtitle, value FROM activity "
            "WHERE ts < ? ORDER BY ts DESC LIMIT ?",
            (before, limit),
        ).fetchall()
    else:
        rows = con.execute(
            "SELECT id, ts, kind, mint, agent, title, subtitle, value FROM activity "
            "ORDER BY ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return {
        "rows": [dict(r) for r in rows],
        "next_before": rows[-1]["ts"] if rows else None,
    }


def shape_coin(con: sqlite3.Connection, mint: str) -> dict[str, Any]:
    coin = con.execute("SELECT * FROM coins WHERE mint = ?", (mint,)).fetchone()
    prices = con.execute(
        "SELECT ts, price, volume_m5 FROM prices WHERE mint = ? ORDER BY ts", (mint,)
    ).fetchall()
    pos = con.execute(
        "SELECT * FROM positions WHERE mint = ? AND closed_at IS NULL ORDER BY opened_at DESC LIMIT 1",
        (mint,),
    ).fetchone()
    if pos is None:
        pos = con.execute(
            "SELECT * FROM positions WHERE mint = ? ORDER BY opened_at DESC LIMIT 1", (mint,)
        ).fetchone()
    last = prices[-1] if prices else None
    settings = all_settings(con)
    return {
        "mint": mint,
        "coin": dict(coin) if coin else None,
        "price": last["price"] if last else None,
        "candles": [
            {
                "t": p["ts"],
                "o": p["price"],
                "h": p["price"],
                "l": p["price"],
                "c": p["price"],
                "volume_m5": p["volume_m5"],
            }
            for p in prices
        ],
        "stats": {
            "holders": coin["holders"] if coin else None,
            "liquidity_usd": None,
            "market_cap": None,
            "volume_h24": None,
        },
        "position": dict(pos) if pos else None,
        "exit_lines": {
            "stop_pct": float(settings.get("risk.stop_pct", "6")),
            "trail_start_pct": float(settings.get("risk.trail_start_pct", "20")),
            "trail_pct": float(settings.get("risk.trail_pct", "12")),
            "timeout_h": float(settings.get("risk.timeout_h", "3")),
        },
        "coin_url": coin_url(mint, "solana"),
    }


def shape_audit(con: sqlite3.Connection, mint: str) -> dict[str, Any]:
    layers = con.execute(
        "SELECT layer, name, value_text, pass, source, ts FROM audits WHERE mint = ? ORDER BY layer, id",
        (mint,),
    ).fetchall()
    # collapse to latest per layer number
    by_n: dict[int, dict[str, Any]] = {}
    for r in layers:
        by_n[r["layer"]] = {
            "n": r["layer"],
            "name": r["name"],
            "value": r["value_text"],
            "pass": None if r["pass"] is None else bool(r["pass"]),
            "source": r["source"],
            "ts": r["ts"],
        }
    ordered = [by_n[k] for k in sorted(by_n)]
    return {"mint": mint, "layers": ordered, "layersChecked": len(ordered)}


def shape_agent_decisions(con: sqlite3.Connection, name: str, limit: int = 20) -> dict[str, Any]:
    limit = max(1, min(int(limit or 20), 50))
    rows = con.execute(
        "SELECT id, ts, kind, mint, title, subtitle, value FROM activity "
        "WHERE agent = ? ORDER BY ts DESC LIMIT ?",
        (name, limit),
    ).fetchall()
    return {"agent": name, "decisions": [dict(r) for r in rows]}


def shape_account(con: sqlite3.Connection) -> dict[str, Any]:
    settings = all_settings(con)
    # group settings by agent prefix for the Account tab
    grouped: dict[str, dict[str, str]] = {"desk": {}, "scanner": {}, "auditor": {}, "analyst": {}, "executor": {}, "risk": {}, "api": {}}
    for k, v in settings.items():
        if "." in k:
            prefix, rest = k.split(".", 1)
            grouped.setdefault(prefix, {})[rest] = v
        else:
            grouped["desk"][k] = v
    return {
        "mode": settings.get("mode", "PAPER"),
        "stack_usd": float(settings.get("executor.stack_usd", "200")),
        "fomo_handle": settings.get("fomo_handle"),
        "profile_url": profile_url(settings.get("fomo_handle") or "AsgerJepsen"),
        "signup_url": SIGNUP_URL,
        "analyst_mode": settings.get("analyst_mode"),
        "hosting_plan": settings.get("hosting_plan", "B"),
        "telegram": {
            "connected": True,
            "push": "telegram_only",
            "web_push": False,
            "note": "Plan B: Telegram only (no /api/push on static host)",
        },
        "settings": settings,
        "settings_grouped": grouped,
    }


def write_plan_b_snapshot(con: sqlite3.Connection, out_dir: Path) -> list[str]:
    """Write full plan B JSON set matching STEP 7 API shapes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payloads: dict[str, Any] = {
        "state.json": shape_state(con),
        "activity.json": shape_activity(con),
        "account.json": shape_account(con),
    }
    for r in CHART_RANGES:
        payloads[f"chart_{r}.json"] = shape_chart(con, r)
    payloads["chart.json"] = payloads["chart_1D.json"]

    for name in AGENTS:
        payloads[f"agents_{name}_decisions.json"] = shape_agent_decisions(con, name)

    mints = [r["mint"] for r in con.execute("SELECT mint FROM coins").fetchall()]
    # always include a stable example if empty
    if not mints:
        mints = ["So11111111111111111111111111111111111111112"]
    for mint in mints:
        safe = mint  # base58 — filesystem-safe
        payloads[f"coin_{safe}.json"] = shape_coin(con, mint)
        payloads[f"audit_{safe}.json"] = shape_audit(con, mint)

    written = []
    for name, payload in payloads.items():
        path = out_dir / name
        path.write_text(json.dumps(payload, indent=2) + "\n")
        written.append(str(path))
    # index of available files (no secrets)
    index = {
        "files": sorted(payloads.keys()),
        "endpoints": {
            "state": "state.json",
            "chart": "chart_{RANGE}.json",
            "activity": "activity.json",
            "account": "account.json",
            "coin": "coin_{mint}.json",
            "audit": "audit_{mint}.json",
            "agents_decisions": "agents_{name}_decisions.json",
        },
        "push": "telegram_only",
    }
    (out_dir / "index.json").write_text(json.dumps(index, indent=2) + "\n")
    written.append(str(out_dir / "index.json"))
    return written
