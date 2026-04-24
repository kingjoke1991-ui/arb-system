from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.api.deps import get_container, require_admin
from app.api.rate_limit import rate_limit
from app.common.enums import Mode
from app.runtime.dependency_container import Container

router = APIRouter(
    prefix="/control",
    tags=["control"],
    dependencies=[Depends(require_admin), Depends(rate_limit)],
)


def _validate_mode(mode: str) -> str:
    if mode not in {m.value for m in Mode}:
        raise HTTPException(status_code=400, detail=f"invalid mode: {mode}")
    return mode


@router.post("/pause")
async def pause(c: Container = Depends(get_container)) -> dict:
    """Freeze scanning/execution without changing mode.

    Previously this flipped ``mode`` to ``dry-run`` which both lost the
    previous mode context and confused the UI. Now it sets a dedicated
    ``paused`` flag that the scanner honors independently.
    """
    c.settings.paused = True
    return {"paused": True, "mode": c.settings.mode}


@router.post("/resume")
async def resume(
    mode: str | None = Body(default=None, embed=True),
    c: Container = Depends(get_container),
) -> dict:
    """Un-pause. Optionally switch mode in the same call (kept for
    backwards compatibility with the existing frontend)."""
    if mode is not None:
        c.settings.mode = _validate_mode(mode)
    c.settings.paused = False
    return {"paused": False, "mode": c.settings.mode}


@router.post("/mode")
async def set_mode(mode: str = Body(..., embed=True), c: Container = Depends(get_container)) -> dict:
    c.settings.mode = _validate_mode(mode)
    return {"mode": c.settings.mode, "paused": c.settings.paused}


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
