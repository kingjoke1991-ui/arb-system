from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.api.deps import get_container, require_admin
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/control", tags=["control"], dependencies=[Depends(require_admin)])


@router.post("/pause")
async def pause(c: Container = Depends(get_container)) -> dict:
    c.settings.mode = "dry-run"  # quickest way to stop live/paper loops without killing process
    return {"mode": c.settings.mode}


@router.post("/resume")
async def resume(mode: str = Body(..., embed=True), c: Container = Depends(get_container)) -> dict:
    assert mode in ("dry-run", "paper-trade", "live")
    c.settings.mode = mode
    return {"mode": c.settings.mode}


@router.post("/mode")
async def set_mode(mode: str = Body(..., embed=True), c: Container = Depends(get_container)) -> dict:
    assert mode in ("dry-run", "paper-trade", "live")
    c.settings.mode = mode
    return {"mode": c.settings.mode}


@router.post("/kill-switch/on")
async def ks_on(c: Container = Depends(get_container)) -> dict:
    c.kill.turn_on("api")
    c.settings.kill_switch = True
    return c.kill.status()


@router.post("/kill-switch/off")
async def ks_off(c: Container = Depends(get_container)) -> dict:
    c.kill.turn_off("api")
    c.settings.kill_switch = False
    return c.kill.status()


@router.post("/circuit-breaker/reset")
async def cb_reset(c: Container = Depends(get_container)) -> dict:
    c.breaker.reset()
    return c.breaker.status()


@router.post("/reload-config")
async def reload_config(c: Container = Depends(get_container)) -> dict:
    from app.config.settings import reload_settings

    new = reload_settings()
    # Best-effort: copy over runtime-tweakable values
    for k in c.config_service.current().keys():
        if hasattr(new, k):
            setattr(c.settings, k, getattr(new, k))
    return {"ok": True}


@router.post("/reconcile/balances")
async def reconcile(c: Container = Depends(get_container)) -> dict:
    drift = await c.reconciler.run_once()
    return {"drift": drift}


@router.post("/cooldown/{symbol}")
async def set_cooldown(
    symbol: str, seconds: int | None = None, c: Container = Depends(get_container)
) -> dict:
    c.risk.set_cooldown(symbol, seconds)
    return {"symbol": symbol, "seconds": seconds or c.settings.cooldown_seconds}
