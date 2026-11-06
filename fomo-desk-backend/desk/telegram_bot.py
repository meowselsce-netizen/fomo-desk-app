"""STEP 9 · Telegram long-polling, owner-only commands and pushes."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from desk.db import all_settings, connect, get_setting, set_setting, utc_now
from desk.links import SIGNUP_URL, coin_url
from desk.notifier import buy_push, invite_push, sell_push

log = logging.getLogger("telegram")
ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "secrets" / ".env"
HELP = (
    "Commands:\n"
    "buy X · sell X · why X\n"
    "pause Scanner · (resume Scanner)\n"
    "set stop to -8% · stack 1240\n"
    "status · paper · invite · report\n"
    "LIVE is rejected. This build stays PAPER.\n"
    "X = name, symbol or mint."
)


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    for k, v in os.environ.items():
        if k.startswith("TG_") or k in ("APP_TOKEN", "DATA_KEY", "PUBLIC_BASE_URL"):
            env.setdefault(k, v)
    return env


def tg_api(token: str, method: str, params: dict | None = None, timeout: int = 35) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers: dict[str, str] = {}
    if params is not None:
        data = urllib.parse.urlencode(params, doseq=True).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def paper_prefix(text: str, mode: str) -> str:
    if (mode or "PAPER").upper() == "PAPER" and not text.startswith("PAPER"):
        return "PAPER · " + text
    return text


class TelegramBot:
    def __init__(self) -> None:
        self.env = load_env()
        self.token = self.env.get("TG_TOKEN") or ""
        self.owner_chat = str(self.env.get("TG_OWNER_CHAT_ID") or "")
        self.offset = 0
        self._paused: set[str] = set()
        self._awaiting_live = False
        self._running = False

    def refresh_env(self) -> None:
        self.env = load_env()
        self.token = self.env.get("TG_TOKEN") or self.token
        self.owner_chat = str(self.env.get("TG_OWNER_CHAT_ID") or self.owner_chat)

    def is_owner(self, chat_id: Any, user_id: Any = None) -> bool:
        if not self.owner_chat:
            return False
        return str(chat_id) == str(self.owner_chat)

    def send(self, chat_id: str | int, text: str, button: dict | None = None) -> dict:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
        if button and button.get("url") and button.get("label"):
            params["reply_markup"] = json.dumps({
                "inline_keyboard": [[{"text": button["label"], "url": button["url"]}]]
            })
        return tg_api(self.token, "sendMessage", params)

    def push_owner(self, text: str, button: dict | None = None) -> dict:
        con = connect()
        try:
            mode = get_setting(con, "mode", "PAPER") or "PAPER"
        finally:
            con.close()
        return self.send(self.owner_chat, paper_prefix(text, mode), button)

    # ---- resolution helpers ----
    def find_coins(self, q: str) -> list[dict]:
        q = (q or "").strip()
        if not q:
            return []
        con = connect()
        try:
            rows = con.execute(
                "SELECT mint, name, symbol FROM coins WHERE mint = ? OR lower(symbol) = lower(?) OR lower(name) = lower(?)",
                (q, q, q),
            ).fetchall()
            if rows:
                return [dict(r) for r in rows]
            rows = con.execute(
                "SELECT mint, name, symbol FROM coins WHERE lower(symbol) LIKE lower(?) OR lower(name) LIKE lower(?) LIMIT 5",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()

    def resolve_one(self, q: str) -> tuple[dict | None, str | None]:
        hits = self.find_coins(q)
        if not hits:
            return None, f"Unknown coin '{q}'. Send the mint address."
        if len(hits) > 1:
            lines = ["Which one?"]
            for h in hits:
                lines.append(f"• {h.get('symbol') or h.get('name')} — {coin_url(h['mint'])}")
            return None, "\n".join(lines)
        return hits[0], None

    # ---- commands ----
    def cmd_status(self) -> str:
        con = connect()
        try:
            mode = get_setting(con, "mode", "PAPER") or "PAPER"
            bal = con.execute("SELECT value FROM balance ORDER BY ts DESC LIMIT 1").fetchone()
            balance = float(bal["value"]) if bal else float(get_setting(con, "executor.stack_usd", "200") or 200)
            pos = con.execute(
                "SELECT mint, size_usd, entry_price FROM positions WHERE closed_at IS NULL"
            ).fetchall()
            lines = [f"{mode} · balance ${balance:,.2f}", f"Open positions: {len(pos)}"]
            for p in pos:
                coin = con.execute("SELECT symbol, name FROM coins WHERE mint=?", (p["mint"],)).fetchone()
                label = (coin["symbol"] if coin else p["mint"][:8])
                lines.append(f"• {label} ${float(p['size_usd']):.2f} — {coin_url(p['mint'])}")
            if not pos:
                lines.append("(none)")
            return "\n".join(lines)
        finally:
            con.close()

    def cmd_buy(self, q: str) -> tuple[str, dict | None]:
        coin, err = self.resolve_one(q)
        if err:
            return err, None
        assert coin
        mint = coin["mint"]
        con = connect()
        try:
            pushed = con.execute(
                "SELECT id FROM activity WHERE mint=? AND kind IN ('bought','signal','pushed') "
                "AND ts >= datetime('now','-1 day') LIMIT 1",
                (mint,),
            ).fetchone()
            # "pushed tonight" ≈ activity/signal today
            tonight = con.execute(
                "SELECT id FROM signals WHERE mint=? AND ts >= datetime('now','-16 hours') LIMIT 1",
                (mint,),
            ).fetchone()
        finally:
            con.close()
        label = coin.get("symbol") or coin.get("name") or mint[:8]
        if tonight or pushed:
            payload = buy_push(mint, f"Buy {label}")
            return paper_prefix(payload["text"] + f"\n{payload['fallback_line']}", "PAPER"), payload["button"]
        # never pushed: run auditor layers from db (or stub) — no order
        con = connect()
        try:
            layers = con.execute(
                "SELECT layer, name, value_text, pass FROM audits WHERE mint=? ORDER BY id DESC LIMIT 8",
                (mint,),
            ).fetchall()
        finally:
            con.close()
        if layers:
            # take latest unique layers
            by = {}
            for r in layers:
                by[r["layer"]] = r
            ordered = [by[k] for k in sorted(by)]
            body = "\n".join(
                f"L{r['layer']} {r['name']}: {r['value_text']} {'✓' if r['pass'] else '✕'}"
                for r in ordered
            )
        else:
            body = "No audit rows yet — run the desk Auditor first."
        payload = buy_push(mint, f"{label}\n{body}\nnot sized, not on the desk")
        text = paper_prefix(payload["text"] + f"\n{payload['fallback_line']}", "PAPER")
        return text, payload["button"]

    def cmd_sell(self, q: str) -> tuple[str, dict | None]:
        coin, err = self.resolve_one(q)
        if err:
            return err, None
        assert coin
        mint = coin["mint"]
        con = connect()
        try:
            pos = con.execute(
                "SELECT id FROM positions WHERE mint=? AND closed_at IS NULL", (mint,)
            ).fetchone()
        finally:
            con.close()
        if not pos:
            return f"No open position for {coin.get('symbol') or mint[:8]}.", None
        payload = sell_push(mint, f"Sell {coin.get('symbol') or mint[:8]}")
        return paper_prefix(payload["text"] + f"\n{payload['fallback_line']}", "PAPER"), payload["button"]

    def cmd_why(self, q: str) -> str:
        coin, err = self.resolve_one(q)
        if err:
            return err
        assert coin
        mint = coin["mint"]
        con = connect()
        try:
            act = con.execute(
                "SELECT ts, kind, agent, title, subtitle, value FROM activity WHERE mint=? ORDER BY id DESC LIMIT 1",
                (mint,),
            ).fetchone()
            rej = con.execute(
                "SELECT layer, name, value_text, ts FROM audits WHERE mint=? AND pass=0 ORDER BY id DESC LIMIT 1",
                (mint,),
            ).fetchone()
            closed = con.execute(
                "SELECT exit_reason, closed_at, pnl_usd FROM positions WHERE mint=? AND closed_at IS NOT NULL ORDER BY id DESC LIMIT 1",
                (mint,),
            ).fetchone()
        finally:
            con.close()
        parts = [f"{coin.get('symbol') or mint[:8]} — {coin_url(mint)}"]
        if rej:
            parts.append(f"Auditor layer {rej['layer']} {rej['name']}: {rej['value_text']} at {rej['ts']}")
        elif closed:
            parts.append(f"Risk/exit: {closed['exit_reason']} at {closed['closed_at']} pnl={closed['pnl_usd']}")
        elif act:
            parts.append(f"{act['agent'] or '?'} · {act['kind']}: {act['title']} {act['subtitle'] or ''} ({act['ts']})")
        else:
            parts.append("No stop/reject recorded.")
        return "\n".join(parts)

    def cmd_pause(self, text: str) -> str:
        m = re.match(r"(?i)\s*(pause|resume)\s+(\w+)", text.strip())
        if not m:
            return "Usage: pause Scanner | resume Scanner"
        action, name = m.group(1).lower(), m.group(2).lower()
        agents = {"scanner", "auditor", "analyst", "executor", "risk"}
        if name not in agents:
            return f"Unknown agent. One of: {', '.join(sorted(agents))}"
        if name == "risk" and action == "pause":
            con = connect()
            try:
                open_n = con.execute("SELECT COUNT(*) AS n FROM positions WHERE closed_at IS NULL").fetchone()["n"]
            finally:
                con.close()
            if open_n:
                return "Risk not while a position is open."
        if action == "pause":
            self._paused.add(name)
            set_setting_safe(f"agent.{name}.paused", "1", "telegram")
            return f"Paused {name}."
        self._paused.discard(name)
        set_setting_safe(f"agent.{name}.paused", "0", "telegram")
        return f"Resumed {name}."

    def cmd_set(self, text: str) -> str:
        # set stop to -8%  / set risk.stop_pct 8
        m = re.match(r"(?i)set\s+(.+?)\s+to\s+(.+)$", text.strip())
        if not m:
            m = re.match(r"(?i)set\s+(\S+)\s+(\S+)$", text.strip())
            if not m:
                return "Usage: set stop to -8%"
            key_raw, val_raw = m.group(1), m.group(2)
        else:
            key_raw, val_raw = m.group(1), m.group(2)
        aliases = {
            "stop": "risk.stop_pct",
            "stop_pct": "risk.stop_pct",
            "trail": "risk.trail_pct",
            "trail_pct": "risk.trail_pct",
            "stack": "executor.stack_usd",
            "timeout": "risk.timeout_h",
        }
        key = aliases.get(key_raw.strip().lower(), key_raw.strip())
        if key == "mode" or key.lower().endswith(".mode"):
            return "mode changes are rejected. This build is PAPER-only."
        if key == "risk.stop_pct":
            val = val_raw.strip().replace("%", "").replace("-", "")
        else:
            val = val_raw.strip().replace("%", "")
        con = connect()
        try:
            old = get_setting(con, key, "")
            set_setting(con, key, val, who="telegram")
            con.commit()
        finally:
            con.close()
        nice = key.split(".")[-1]
        return f"{nice} {old}% -> {val}%" if "pct" in key else f"{nice} {old} -> {val}"

    def cmd_stack(self, text: str) -> str:
        m = re.match(r"(?i)stack\s+([\d.]+)", text.strip())
        if not m:
            return "Usage: stack 1240"
        val = m.group(1)
        con = connect()
        try:
            old = get_setting(con, "executor.stack_usd", "200")
            set_setting(con, "executor.stack_usd", val, who="telegram")
            con.commit()
        finally:
            con.close()
        return f"stack {old} -> {val}"

    def cmd_mode(self, text: str) -> str:
        raw = text.strip()
        t = raw.lower()
        self._awaiting_live = False
        if raw == "LIVE" or t == "live":
            return "LIVE is rejected. This build is PAPER-only."
        if t == "paper":
            con = connect()
            try:
                old = get_setting(con, "mode", "PAPER") or "PAPER"
                set_setting(con, "mode", "PAPER", who="telegram")
                con.commit()
                return f"mode {old} -> PAPER"
            finally:
                con.close()
        return HELP

    def cmd_invite(self) -> tuple[str, dict]:
        payload = invite_push("Invite a friend to FOMO")
        return payload["text"] + "\n" + SIGNUP_URL, payload["button"]

    def cmd_report(self) -> str:
        return "This PAPER build does not publish a morning page and does not read private env values."

    def handle_text(self, text: str) -> tuple[str, dict | None]:
        raw = (text or "").strip()
        if raw.startswith("/"):
            raw = raw[1:]
            # /start@bot → start
            raw = raw.split("@", 1)[0]
        low = raw.lower()

        if low in ("start", "help", "?"):
            return HELP, None
        if low == "status":
            return self.cmd_status(), None
        if low == "invite":
            return self.cmd_invite()
        if low == "report":
            return self.cmd_report(), None
        if raw == "LIVE":
            return self.cmd_mode("LIVE"), None
        if low in ("paper", "live"):
            return self.cmd_mode(low), None
        if low.startswith("buy "):
            return self.cmd_buy(raw[4:].strip())
        if low.startswith("sell "):
            return self.cmd_sell(raw[5:].strip())
        if low.startswith("why "):
            return self.cmd_why(raw[4:].strip()), None
        if low.startswith("pause ") or low.startswith("resume "):
            return self.cmd_pause(raw), None
        if low.startswith("set "):
            return self.cmd_set(raw), None
        if low.startswith("stack "):
            return self.cmd_stack(raw), None
        return HELP, None

    async def poll_forever(self) -> None:
        self._running = True
        self.refresh_env()
        if not self.token or not self.owner_chat:
            log.error("TG_TOKEN or TG_OWNER_CHAT_ID missing")
            return
        log.info("telegram long-poll start")
        while self._running:
            try:
                data = await asyncio.to_thread(
                    tg_api, self.token, "getUpdates",
                    {"timeout": 25, "offset": self.offset, "allowed_updates": json.dumps(["message"])},
                    35,
                )
                for upd in data.get("result") or []:
                    self.offset = int(upd["update_id"]) + 1
                    msg = upd.get("message") or {}
                    chat = msg.get("chat") or {}
                    chat_id = chat.get("id")
                    user = msg.get("from") or {}
                    text = msg.get("text") or ""
                    if not text:
                        continue
                    if not self.is_owner(chat_id, user.get("id")):
                        try:
                            self.send(chat_id, "Owner only.")
                        except Exception:
                            pass
                        continue
                    reply, button = self.handle_text(text)
                    self.send(chat_id, reply, button)
            except urllib.error.HTTPError as e:
                log.warning("telegram HTTP %s", e.code)
                await asyncio.sleep(3)
            except Exception as e:
                log.warning("telegram poll error: %s", e)
                await asyncio.sleep(2)

    def stop(self) -> None:
        self._running = False


def set_setting_safe(key: str, value: str, who: str) -> None:
    con = connect()
    try:
        set_setting(con, key, value, who=who)
        con.commit()
    finally:
        con.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    bot = TelegramBot()
    asyncio.run(bot.poll_forever())


if __name__ == "__main__":
    main()
