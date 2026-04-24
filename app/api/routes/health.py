from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends

from app.api.deps import get_container
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/health", tags=["health"])

# Rolling latency samples per exchange (last 10 samples). Stored at module
# scope so repeated probes can compute a smoothed value.
_latency_samples: dict[str, list[int]] = {}
_MAX_SAMPLES = 10


@router.get("")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/dependencies")
async def dependencies(c: Container = Depends(get_container)) -> dict:
    db_ok = False
    if c.db:
        db_ok = await c.db.ping()
    return {
        "db": db_ok,
        "adapters": [a.name for a in c.registry.all()],
    }


@router.get("/exchanges")
async def exchanges(c: Container = Depends(get_container)) -> dict:
    results = c.health.check_all(c.registry.names())
    return {
        "exchanges": [
            {
                "name": h.exchange,
                "marketdata_ok": h.marketdata_ok,
                "balance_ok": h.balance_ok,
                "reason": h.reason,
            }
            for h in results
        ],
        "kill_switch": c.kill.status(),
        "circuit_breaker": c.breaker.status(),
    }


async def _probe_one(adapter) -> int | None:
    """Cheap public-endpoint RTT probe. Returns ms or None on failure.

    Uses fetch_time() via ccxt (public, no auth). If adapter does not expose
    ``fetch_time`` we fall back to fetching a single order book; in both cases
    we measure the wall-clock round-trip from before the call to after.
    """
    client = getattr(adapter, "_client", None)
    if client is None:
        return None
    fn = getattr(client, "fetch_time", None)
    start = time.perf_counter()
    try:
        if fn is not None:
            await asyncio.wait_for(asyncio.to_thread(fn), timeout=3.0)
        else:
            # last-resort: poke a public ticker we know exists
            await asyncio.wait_for(
                asyncio.to_thread(client.fetch_order_book, "BTC/USDT", 1),
                timeout=3.0,
            )
    except Exception:  # noqa: BLE001
        return None
    return int((time.perf_counter() - start) * 1000)


@router.get("/latency")
async def latency(c: Container = Depends(get_container)) -> dict:
    """Public-endpoint RTT probe for each configured exchange.

    Returns a flat list with ``{name, latency_ms, avg_ms, samples}`` per
    exchange. Intended for a tiny top-bar widget in the UI; re-polls every
    ~10 s from the browser. Never requires API keys.
    """
    names = c.registry.names()
    adapters = [c.registry.get(n) for n in names]
    results = await asyncio.gather(*(_probe_one(a) for a in adapters), return_exceptions=False)

    out = []
    for name, ms in zip(names, results):
        samples = _latency_samples.setdefault(name, [])
        if ms is not None:
            samples.append(ms)
            if len(samples) > _MAX_SAMPLES:
                samples[:] = samples[-_MAX_SAMPLES:]
        avg = int(sum(samples) / len(samples)) if samples else None
        out.append(
            {
                "name": name,
                "latency_ms": ms,  # this probe; None on failure
                "avg_ms": avg,  # rolling average (last 10)
                "samples": len(samples),
            }
        )
    return {"exchanges": out}
