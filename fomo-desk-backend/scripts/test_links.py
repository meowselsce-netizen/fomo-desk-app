#!/usr/bin/env python3
"""STEP 6 proofs: links.py shapes + grep that every FOMO URL carries r=savipww."""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from desk.links import CHAIN_ID, SIGNUP_URL, coin_url, profile_url
from desk.notifier import buy_push, invite_push, profile_push, sell_push
from desk.db import connect, migrate
from desk.plan_b_shapes import shape_account, shape_coin, write_plan_b_snapshot

FOMO_URL_RE = re.compile(r"https://fomo\.family[^\s\"'<>\\]*", re.I)
REF = "savipww"


def has_referral(url: str) -> bool:
    if "/r/savipww" in url:
        return True
    q = parse_qs(urlparse(url).query)
    return REF in q.get("r", [])


def ok(name: str, passed: bool, evidence: str) -> dict:
    return {"check": name, "pass": bool(passed), "evidence": evidence}


def grep_fomo_urls() -> tuple[list[str], list[tuple[str, str]]]:
    """Scan code + plan B JSON + DB activity/signals for FOMO URLs missing r=savipww."""
    skip_dirs = {".venv", "__pycache__", ".git", "docs"}
    # Allow listed historical reports / intentionally documenting the rule
    skip_files = {
        "logs/step5_report.json",
        "logs/step6_report.json",
        "scripts/test_links.py",  # this file contains the pattern in comments/strings carefully
    }
    found: list[str] = []
    bad: list[tuple[str, str]] = []

    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in skip_dirs for part in p.parts):
            continue
        rel = str(p.relative_to(ROOT))
        if rel in skip_files:
            continue
        if p.suffix.lower() in {".png", ".pyc", ".db", ".sqlite", ".zip", ".woff"}:
            continue
        if p.name.endswith(("-wal", "-shm")):
            continue
        # skip deprecated shim source that only raises
        try:
            text = p.read_text(errors="ignore")
        except Exception:
            continue
        for m in FOMO_URL_RE.finditer(text):
            url = m.group(0).rstrip(").,;]")
            # Ignore template/format fragments that aren't full URLs with host path only
            # Skip unresolved f-string / format templates in source
            if "{" in url or "}" in url:
                continue
            found.append(url)
            if not has_referral(url):
                bad.append((rel, url))

    # DB rows
    con = connect()
    try:
        for table, col in (("signals", "link"), ("activity", "value"), ("activity", "subtitle"), ("activity", "title")):
            try:
                rows = con.execute(f"SELECT {col} FROM {table} WHERE {col} LIKE '%fomo.family%'").fetchall()
            except Exception:
                continue
            for r in rows:
                val = r[0] or ""
                for m in FOMO_URL_RE.finditer(val):
                    url = m.group(0)
                    if "{" in url or "}" in url:
                        continue
                    found.append(url)
                    if not has_referral(url):
                        bad.append((f"db:{table}.{col}", url))
    finally:
        con.close()
    return found, bad


def main() -> int:
    migrate()
    proofs = []
    samples = {chain: coin_url("ExampleMint1111111111111111111111111111111", chain) for chain in CHAIN_ID}
    samples["unknown_chain"] = coin_url("x", "ethereum")
    samples["signup"] = SIGNUP_URL
    samples["profile"] = profile_url("@AsgerJepsen")

    # shape checks
    for chain, url in samples.items():
        if chain == "unknown_chain":
            proofs.append(ok(f"sample_{chain}", url == SIGNUP_URL and has_referral(url), url))
        elif chain == "signup":
            proofs.append(ok("sample_signup", url == "https://fomo.family/r/savipww", url))
        elif chain == "profile":
            proofs.append(ok("sample_profile", has_referral(url) and "AsgerJepsen" in url and "source=share_link" in url, url))
        else:
            cid = str(CHAIN_ID[chain])
            good = (
                has_referral(url)
                and f"chainId={cid}" in url
                and "source=share_link" in url
                and url.startswith("https://fomo.family/coin?")
            )
            proofs.append(ok(f"sample_{chain}", good, url))

    # notifier uses links.py output exactly
    buy = buy_push("MintABC", "PAPER · buy")
    sell = sell_push("MintABC", "PAPER · sell")
    proofs.append(ok("notifier_buy_url", buy["button"]["url"] == coin_url("MintABC"), buy["button"]["url"]))
    proofs.append(ok("notifier_sell_url", sell["button"]["url"] == coin_url("MintABC"), sell["button"]["url"]))
    proofs.append(ok("notifier_invite", invite_push()["button"]["url"] == SIGNUP_URL, invite_push()["button"]["url"]))
    proofs.append(ok(
        "notifier_profile",
        profile_push("AsgerJepsen")["button"]["url"] == profile_url("AsgerJepsen"),
        profile_push("AsgerJepsen")["button"]["url"],
    ))

    # plan B shapes include referral URLs
    con = connect()
    try:
        write_plan_b_snapshot(con, ROOT / "data" / "plan_b_sample")
        coin = shape_coin(con, "So11111111111111111111111111111111111111112")
        acct = shape_account(con)
    finally:
        con.close()
    proofs.append(ok("plan_b_coin_url", has_referral(coin.get("coin_url") or ""), coin.get("coin_url")))
    proofs.append(ok("plan_b_signup", has_referral(acct.get("signup_url") or ""), acct.get("signup_url")))
    proofs.append(ok("plan_b_profile", has_referral(acct.get("profile_url") or ""), acct.get("profile_url")))

    # no _fomo_url imports left in agents
    import_hits = []
    for p in (ROOT / "desk").rglob("*.py"):
        if p.name == "_fomo_url.py":
            continue
        if "from desk._fomo_url" in p.read_text() or "import desk._fomo_url" in p.read_text():
            import_hits.append(str(p.relative_to(ROOT)))
    proofs.append(ok("no_stub_imports", import_hits == [], f"hits={import_hits}"))

    found, bad = grep_fomo_urls()
    # Filter this test script's own SIGNUP_URL constant appearances already skipped;
    # also allow links.py itself (all have referral by construction) — still check them.
    proofs.append(ok(
        "grep_all_fomo_have_r",
        len(bad) == 0,
        f"scanned_urls={len(found)} bad={len(bad)}" + (f" first={bad[0]}" if bad else ""),
    ))

    # HTTP resolve one real-ish URL (status only). Use signup — always valid path.
    http_status = None
    http_err = None
    try:
        req = urllib.request.Request(
            SIGNUP_URL,
            method="GET",
            headers={"User-Agent": "fomo-desk-step6/0.1"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            http_status = resp.status
    except urllib.error.HTTPError as e:
        http_status = e.code
    except Exception as e:
        http_err = type(e).__name__
    # Also try a coin_url (may 200/302/404 depending on mint — any HTTP response counts as resolves)
    coin_status = None
    coin_err = None
    sample_coin = coin_url("So11111111111111111111111111111111111111112", "solana")
    try:
        req = urllib.request.Request(sample_coin, headers={"User-Agent": "fomo-desk-step6/0.1"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            coin_status = resp.status
    except urllib.error.HTTPError as e:
        coin_status = e.code
    except Exception as e:
        coin_err = type(e).__name__

    proofs.append(ok(
        "http_signup",
        http_status is not None and int(http_status) < 500,
        f"status={http_status} err={http_err}",
    ))
    proofs.append(ok(
        "http_coin_url",
        coin_status is not None and int(coin_status) < 500,
        f"status={coin_status} err={coin_err}",
    ))

    report = {
        "step": 6,
        "samples": samples,
        "proofs": proofs,
        "all_pass": all(p["pass"] for p in proofs),
        "files_changed": [
            "desk/links.py",
            "desk/notifier.py",
            "desk/_fomo_url.py (now ImportError shim)",
            "desk/agents/executor.py",
            "desk/agents/risk.py",
            "desk/plan_b_shapes.py",
            "scripts/test_links.py",
            "data/plan_b_sample/*.json (regenerated)",
        ],
        "grep_bad": bad[:10],
    }
    out = ROOT / "logs" / "step6_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
