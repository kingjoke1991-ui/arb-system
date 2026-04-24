"""
Lightweight in-process rate limiter for admin-authenticated mutating endpoints.

Applied as a FastAPI dependency so it only kicks in for routes we explicitly
gate (/config POST, /control/*). The bucket is per-client-IP and reset every
``window_s`` seconds. Bypasses when the feature is disabled in settings.

No external dependency on slowapi; for the MVP we stay small and explicit.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Deque

from fastapi import HTTPException, Request

from app.config.settings import get_settings


class _Bucket:
    def __init__(self):
        self.events: Deque[float] = deque()
        self.lock = asyncio.Lock()


_buckets: dict[str, _Bucket] = defaultdict(_Bucket)
# Defensive cap: don't let a single rogue client balloon memory.
_MAX_CLIENTS = 10000


async def rate_limit(
    request: Request,
    max_per_minute: int = 60,
) -> None:
    settings = get_settings()
    if not getattr(settings, "admin_rate_limit_enabled", True):
        return

    client = request.client.host if request.client else "anon"
    if len(_buckets) > _MAX_CLIENTS:
        # Keep memory bounded: drop the oldest. Lock-free reset is acceptable
        # because this branch is hit almost never.
        _buckets.clear()

    b = _buckets[client]
    window_s = 60.0
    now = time.time()
    async with b.lock:
        while b.events and (now - b.events[0]) > window_s:
            b.events.popleft()
        if len(b.events) >= max_per_minute:
            retry_in = window_s - (now - b.events[0])
            raise HTTPException(
                status_code=429,
                detail=f"rate limit exceeded ({max_per_minute}/min); retry in {retry_in:.0f}s",
            )
        b.events.append(now)
