"""Tiny in-process pub/sub that fans dashboard events out to SSE subscribers."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    def subscribe(self) -> asyncio.Queue[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[str]) -> None:
        self._subscribers.discard(q)

    def publish(self, event: str, data: Any) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # a stalled browser tab shouldn't block the call
                self._subscribers.discard(q)


bus = EventBus()
