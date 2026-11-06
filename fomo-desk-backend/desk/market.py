"""Read-only public market data. No order endpoints, no API keys."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote

DEX_BOOSTS = "https://api.dexscreener.com/token-boosts/latest/v1"
DEX_SEARCH = "https://api.dexscreener.com/latest/dex/search?q=solana"
DEX_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/{mints}"
RUG_REPORT = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
PUMP_WS = "wss://pumpportal.fun/api/data"

Fetch = Callable[[str], Awaitable[tuple[int, Any]]]


class MarketOffline(RuntimeError):
    """A public source failed. Callers record the reason and do not invent a pass."""


@dataclass
class Quote:
    mint: str
    price_usd: float | None
    liquidity_usd: float | None
    fetched_at: datetime
    pair: dict[str, Any] | None = None

    def age_s(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.fetched_at).total_seconds()


@dataclass
class Candidate:
    event_id: str
    source: str
    mint: str
    market: dict[str, Any]
    facts: dict[str, Any]
    analyst_ctx: dict[str, Any]
    fetched_at: datetime
    price_usd: float | None
    liquidity_usd: float | None
    name: str | None = None
    symbol: str | None = None
    pair: str | None = None

    def quote(self) -> Quote:
        return Quote(self.mint, self.price_usd, self.liquidity_usd, self.fetched_at, None)


@dataclass
class HostLimiter:
    min_interval_s: float = 1.1
    max_backoff_s: float = 60.0
    _next: dict[str, float] = field(default_factory=dict)
    _backoff: dict[str, float] = field(default_factory=dict)
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic

    async def wait(self, host: str) -> None:
        now = self.clock()
        nxt = self._next.get(host, now)
        delay = max(0.0, nxt - now, self._backoff.get(host, 0.0))
        self._backoff[host] = 0.0
        if delay:
            await self.sleep(delay)
        self._next[host] = self.clock() + self.min_interval_s

    def penalize(self, host: str) -> None:
        cur = self._backoff.get(host, self.min_interval_s)
        self._backoff[host] = min(self.max_backoff_s, max(self.min_interval_s, cur * 2))


def _host(url: str) -> str:
    return url.split("/")[2] if "://" in url else url


def solana_mints_from_boosts(payload: Any, limit: int = 5) -> list[str]:
    rows = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        rows = payload.get("pairs") or payload.get("data") or []
    found: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        chain = str(row.get("chainId") or "").lower()
        if chain not in ("solana", "1399811149"):
            continue
        mint = row.get("tokenAddress") or (row.get("baseToken") or {}).get("address")
        if mint and mint not in found:
            found.append(str(mint))
        if len(found) >= limit:
            break
    return found


def pairs_from_tokens(payload: Any) -> list[dict[str, Any]]:
    rows = payload if isinstance(payload, list) else []
    if isinstance(payload, dict):
        rows = payload.get("pairs") or []
    out: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, dict) and str(row.get("chainId") or "").lower() == "solana":
            base = row.get("baseToken") or {}
            if base.get("address"):
                out.append(row)
    return out


class HttpMarket:
    """Documented public GETs only. Retries 429/5xx with backoff. Never posts an order."""

    def __init__(self, fetch: Fetch, limiter: HostLimiter | None = None, pair_limit: int = 5) -> None:
        self.fetch = fetch
        self.limiter = limiter or HostLimiter()
        self.pair_limit = pair_limit

    async def _get(self, url: str) -> Any:
        host = _host(url)
        last: Exception | None = None
        for _ in range(3):
            await self.limiter.wait(host)
            try:
                status, body = await self.fetch(url)
            except Exception as exc:
                self.limiter.penalize(host)
                last = exc
                continue
            if status == 429 or status >= 500:
                self.limiter.penalize(host)
                last = MarketOffline(f"{host} HTTP {status}")
                continue
            if status >= 400:
                raise MarketOffline(f"{host} HTTP {status}")
            return body
        raise MarketOffline(str(last) if last else f"{host} unavailable")

    async def list_pairs(self) -> list[dict[str, Any]]:
        try:
            boosts = await self._get(DEX_BOOSTS)
            mints = solana_mints_from_boosts(boosts, self.pair_limit)
            source = "dexscreener-boosts"
        except MarketOffline:
            boosts = None
            mints = []
            source = "dexscreener-search"
        if not mints:
            searched = await self._get(DEX_SEARCH)
            pairs = pairs_from_tokens(searched)
            pairs.sort(key=lambda p: p.get("pairCreatedAt") or 0, reverse=True)
            for pair in pairs:
                pair["_desk_source"] = "dexscreener-search"
            return pairs[: self.pair_limit]
        url = DEX_TOKENS.format(mints=",".join(quote(m, safe="") for m in mints))
        payload = await self._get(url)
        pairs = pairs_from_tokens(payload)
        for pair in pairs:
            pair["_desk_source"] = source
        return pairs[: self.pair_limit]

    async def rug_report(self, mint: str) -> dict[str, Any]:
        body = await self._get(RUG_REPORT.format(mint=quote(mint, safe="")))
        if not isinstance(body, dict):
            raise MarketOffline("rugcheck payload was not an object")
        return body

    async def quote_pair(self, mint: str) -> dict[str, Any] | None:
        payload = await self._get(DEX_TOKENS.format(mints=quote(mint, safe="")))
        pairs = pairs_from_tokens(payload)
        if not pairs:
            return None
        pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
        return pairs[0]


async def aiohttp_fetch(url: str, timeout_s: float = 15.0) -> tuple[int, Any]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=timeout_s)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers={"accept": "application/json", "user-agent": "fomo-desk-paper/1"}) as resp:
            try:
                body = await resp.json(content_type=None)
            except Exception:
                body = None
            return resp.status, body


async def probe_pump_migration(timeout_s: float = 3.0) -> dict[str, Any]:
    """Bounded read of the public PumpPortal migration socket. No subscription key. No orders."""
    import aiohttp

    started = time.monotonic()
    try:
        timeout = aiohttp.ClientTimeout(total=timeout_s + 1)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.ws_connect(PUMP_WS, heartbeat=None) as ws:
                await ws.send_json({"method": "subscribeMigration"})
                messages = 0
                deadline = time.monotonic() + timeout_s
                while time.monotonic() < deadline:
                    try:
                        msg = await asyncio.wait_for(ws.receive(), timeout=max(0.1, deadline - time.monotonic()))
                    except asyncio.TimeoutError:
                        break
                    if msg.type in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                        messages += 1
                        if messages >= 1:
                            break
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                await ws.close()
        return {"ok": True, "messages": messages, "elapsed_s": round(time.monotonic() - started, 3)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "elapsed_s": round(time.monotonic() - started, 3)}
