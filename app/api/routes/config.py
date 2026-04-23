from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.api.deps import get_container, require_admin
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def get_config(c: Container = Depends(get_container)) -> dict:
    cur = c.config_service.current()
    return {
        "mode": str(cur["mode"]),
        **{k: _serialize(v) for k, v in cur.items() if k != "mode"},
    }


@router.post("", dependencies=[Depends(require_admin)])
async def update_config(changes: dict[str, Any], c: Container = Depends(get_container)) -> dict:
    applied = c.config_service.update(changes, actor="api")
    # Side-effects: mode / kill switch / cooldown need to propagate immediately.
    if "kill_switch" in applied:
        new_val = c.settings.kill_switch
        if new_val:
            c.kill.turn_on("config")
        else:
            c.kill.turn_off("config")
    if c.event_repo:
        try:
            await c.event_repo.log(
                event_type="config_change",
                severity="info",
                component="config",
                message="config updated",
                payload=applied,
            )
        except Exception:  # noqa: BLE001
            pass
    return {"applied": applied, "current": await get_config(c)}


@router.get("/audit", dependencies=[Depends(require_admin)])
async def audit(c: Container = Depends(get_container)) -> dict:
    return {"audit": c.config_service.audit()}


def _serialize(v: Any) -> Any:
    if hasattr(v, "__str__") and not isinstance(v, (int, float, bool, str, list, dict)):
        return str(v)
    return v
