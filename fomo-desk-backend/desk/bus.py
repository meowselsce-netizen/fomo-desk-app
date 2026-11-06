"""In-process async bus. Agents talk only through bus + db (STEP 5)."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, AsyncIterator, DefaultDict, Dict, List


class Bus:
    def __init__(self) -> None:
        self._qs: DefaultDict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        self._history: DefaultDict[str, List[dict]] = defaultdict(list)
        self._paused: set[str] = set()

    def pause(self, agent: str) -> None:
        self._paused.add(agent)

    def resume(self, agent: str) -> None:
        self._paused.discard(agent)

    def is_paused(self, agent: str) -> bool:
        return agent in self._paused

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        msg = dict(message)
        msg.setdefault("topic", topic)
        self._history[topic].append(msg)
        await self._qs[topic].put(msg)

    async def subscribe(self, topic: str) -> AsyncIterator[dict[str, Any]]:
        q = self._qs[topic]
        while True:
            msg = await q.get()
            yield msg

    def history(self, topic: str) -> list[dict[str, Any]]:
        return list(self._history.get(topic, []))

    def clear_history(self) -> None:
        self._history.clear()


# Topics
MIGRATION = "migration"          # raw graduated coin
SCANNER_PASS = "scanner.pass"    # waiting-room survivor → auditor
AUDITOR_PASS = "auditor.pass"
AUDITOR_LAYER = "audit_layer"    # SSE-style per-layer events
ANALYST_PASS = "analyst.pass"
EXECUTOR_OPEN = "executor.open"  # paper position opened
RISK_EXIT = "risk.exit"
NOTIFY = "notify"                # outbound push requests (Telegram later)

TOPICS = (
    MIGRATION,
    SCANNER_PASS,
    AUDITOR_PASS,
    AUDITOR_LAYER,
    ANALYST_PASS,
    EXECUTOR_OPEN,
    RISK_EXIT,
    NOTIFY,
)
