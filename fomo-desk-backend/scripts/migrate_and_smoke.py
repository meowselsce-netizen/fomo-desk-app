#!/usr/bin/env python3
"""Apply migrations and smoke-test insert/read on every table. Never prints secrets."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
# Schema smoke uses its own file so it cannot rewrite the paper book.
os.environ["FOMO_DESK_DB"] = str(ROOT / "var" / "smoke" / "step4.sqlite")

from desk.db import connect, migrate, journal_mode, table_names, all_settings, utc_now, DB_PATH
from desk.plan_b_shapes import write_plan_b_snapshot, shape_coin, shape_audit, shape_agent_decisions
from desk.settings_defaults import DEFAULTS

SMOKE_MINT = "So11111111111111111111111111111111111111112"


def main() -> int:
    applied = migrate()
    con = connect()
    mode = journal_mode(con)
    tables = table_names(con)
    now = utc_now()
    results = {}

    # coins
    con.execute(
        "INSERT OR REPLACE INTO coins(mint,name,symbol,pool,migrated_at,creator,creator_balance_at_migration,holders) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (SMOKE_MINT, "Smoke", "SMK", "PoolSmoke", now, "Creator111", 0.0, 42),
    )
    row = con.execute("SELECT symbol, holders FROM coins WHERE mint=?", (SMOKE_MINT,)).fetchone()
    results["coins"] = {"ok": row["symbol"] == "SMK" and row["holders"] == 42}

    # scans
    cur = con.execute(
        "INSERT INTO scans(mint,ts,liquidity_usd,top10_pct,h1_change_pct,age_min,verdict,reason) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (SMOKE_MINT, now, 55000.0, 12.5, 9.0, 15.0, "pass", None),
    )
    sid = cur.lastrowid
    results["scans"] = {
        "ok": con.execute("SELECT verdict FROM scans WHERE id=?", (sid,)).fetchone()["verdict"] == "pass"
    }

    # audits
    cur = con.execute(
        "INSERT INTO audits(mint,ts,layer,name,value_text,pass,source) VALUES (?,?,?,?,?,?,?)",
        (SMOKE_MINT, now, 1, "Mint authority", "None", 1, "rugcheck"),
    )
    aid = cur.lastrowid
    results["audits"] = {
        "ok": con.execute("SELECT pass FROM audits WHERE id=?", (aid,)).fetchone()["pass"] == 1
    }

    # verdicts
    cur = con.execute(
        "INSERT INTO verdicts(mint,ts,alive_6h,organic,flush,chase,score,pass,reason,input_tokens) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (SMOKE_MINT, now, 0.8, 0.7, 0.5, 0.2, 80, 1, None, 0),
    )
    vid = cur.lastrowid
    results["verdicts"] = {
        "ok": con.execute("SELECT score FROM verdicts WHERE id=?", (vid,)).fetchone()["score"] == 80
    }

    # signals
    cur = con.execute(
        "INSERT INTO signals(mint,ts,price,size_usd,stack_pct,link,mode,status) VALUES (?,?,?,?,?,?,?,?)",
        (SMOKE_MINT, now, 1.23, 47.5, 100.0, "https://fomo.family/r/savipww", "PAPER", "pushed"),
    )
    gid = cur.lastrowid
    results["signals"] = {
        "ok": con.execute("SELECT status FROM signals WHERE id=?", (gid,)).fetchone()["status"] == "pushed"
    }

    # positions
    cur = con.execute(
        "INSERT INTO positions(mint,opened_at,entry_price,size_usd,peak_price,stop_price,trail_active,"
        "closed_at,exit_price,exit_reason,pnl_usd,fees_usd,mode,stop_pct) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (SMOKE_MINT, now, 1.0, 50.0, 1.2, 0.94, 0, None, None, None, None, None, "PAPER", 6.0),
    )
    pid = cur.lastrowid
    results["positions"] = {
        "ok": con.execute("SELECT stop_pct FROM positions WHERE id=?", (pid,)).fetchone()["stop_pct"] == 6.0
    }

    # activity
    cur = con.execute(
        "INSERT INTO activity(ts,kind,mint,agent,title,subtitle,value) VALUES (?,?,?,?,?,?,?)",
        (now, "rejected", SMOKE_MINT, "auditor", "Rejected SMK", "Auditor · freeze", "Skipped"),
    )
    acid = cur.lastrowid
    results["activity"] = {
        "ok": con.execute("SELECT kind FROM activity WHERE id=?", (acid,)).fetchone()["kind"] == "rejected"
    }

    # prices
    con.execute(
        "INSERT OR REPLACE INTO prices(mint,ts,price,volume_m5) VALUES (?,?,?,?)",
        (SMOKE_MINT, now, 1.23, 1000.0),
    )
    results["prices"] = {
        "ok": con.execute("SELECT price FROM prices WHERE mint=? AND ts=?", (SMOKE_MINT, now)).fetchone()["price"]
        == 1.23
    }

    # balance
    con.execute("INSERT OR REPLACE INTO balance(ts, value) VALUES (?, ?)", (now, 1000.0))
    results["balance"] = {
        "ok": con.execute("SELECT value FROM balance WHERE ts=?", (now,)).fetchone()["value"] == 1000.0
    }

    # settings (already seeded)
    settings = all_settings(con)
    results["settings"] = {
        "ok": settings.get("risk.stop_pct") == "6"
        and settings.get("mode") == "PAPER"
        and settings.get("analyst_mode") == "rules"
        and settings.get("fomo_handle") == "AsgerJepsen"
    }

    # change_log
    nlog = con.execute("SELECT COUNT(*) AS c FROM change_log").fetchone()["c"]
    results["change_log"] = {"ok": nlog >= len(DEFAULTS)}

    # skips
    con.execute("INSERT OR REPLACE INTO skips(mint, until) VALUES (?, ?)", (SMOKE_MINT, now))
    results["skips"] = {
        "ok": con.execute("SELECT until FROM skips WHERE mint=?", (SMOKE_MINT,)).fetchone()["until"] == now
    }

    # heartbeats
    con.execute("INSERT OR REPLACE INTO heartbeats(agent, ts) VALUES (?, ?)", ("scanner", now))
    results["heartbeats"] = {
        "ok": con.execute("SELECT ts FROM heartbeats WHERE agent='scanner'").fetchone()["ts"] == now
    }

    # push_subs
    endpoint = "https://example.push/smoke"
    con.execute(
        "INSERT OR REPLACE INTO push_subs(endpoint, p256dh, auth, created_at) VALUES (?,?,?,?)",
        (endpoint, "p256", "auth", now),
    )
    results["push_subs"] = {
        "ok": con.execute("SELECT auth FROM push_subs WHERE endpoint=?", (endpoint,)).fetchone()["auth"] == "auth"
    }

    # schema_migrations
    results["schema_migrations"] = {
        "ok": con.execute(
            "SELECT 1 FROM schema_migrations WHERE version='001_initial.sql'"
        ).fetchone()
        is not None
    }

    con.commit()

    # plan B sample JSON (no secrets)
    sample_dir = ROOT / "data" / "plan_b_sample"
    written = write_plan_b_snapshot(con, sample_dir)
    # also per-mint shapes for docs
    (sample_dir / "coin_smoke.json").write_text(json.dumps(shape_coin(con, SMOKE_MINT), indent=2) + "\n")
    (sample_dir / "audit_smoke.json").write_text(json.dumps(shape_audit(con, SMOKE_MINT), indent=2) + "\n")
    (sample_dir / "agents_auditor_decisions.json").write_text(
        json.dumps(shape_agent_decisions(con, "auditor"), indent=2) + "\n"
    )

    # scrub smoke rows? keep them — useful as fixtures; mark mint clearly
    report = {
        "db_path": str(DB_PATH),
        "wal": mode.lower() == "wal",
        "journal_mode": mode,
        "migrations_applied_this_run": applied,
        "tables": tables,
        "settings_count": len(settings),
        "settings_highlights": {
            k: settings[k]
            for k in (
                "mode",
                "fomo_handle",
                "analyst_mode",
                "risk.stop_pct",
                "risk.trail_start_pct",
                "risk.trail_pct",
                "risk.timeout_h",
                "hosting_plan",
            )
            if k in settings
        },
        "smoke": results,
        "plan_b_sample_dir": str(sample_dir),
        "plan_b_files_written": [Path(p).name for p in written]
        + ["coin_smoke.json", "audit_smoke.json", "agents_auditor_decisions.json"],
        "all_smoke_pass": all(v.get("ok") for v in results.values()),
    }
    out = ROOT / "logs" / "step4_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    con.close()
    return 0 if report["all_smoke_pass"] and report["wal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
