from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_container, require_admin
from app.api.rate_limit import rate_limit
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/config", tags=["config"])


@router.get("")
async def get_config(c: Container = Depends(get_container)) -> dict:
    cur = c.config_service.current()
    return {
        "mode": str(cur["mode"]),
        **{k: _serialize(v) for k, v in cur.items() if k != "mode"},
    }


@router.post("", dependencies=[Depends(require_admin), Depends(rate_limit)])
async def update_config(changes: dict[str, Any], c: Container = Depends(get_container)) -> dict:
    try:
        applied = c.config_service.update(changes, actor="api")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Side-effects: mode / kill switch / cooldown need to propagate immediately.
    if "kill_switch" in applied:
        new_val = c.settings.kill_switch
        if new_val:
            c.kill.turn_on("config")
        else:
            c.kill.turn_off("config")
    # Persist the full snapshot so the change survives container restarts.
    # ConfigService.persist() is a no-op when the DB isn't wired (e.g. tests
    # or an in-memory degraded mode).
    if applied:
        await c.config_service.persist(actor="api")
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
    # Prefer the DB-backed audit trail so the history survives restarts.
    # Fall back to in-memory if the DB is not available.
    if c.event_repo is not None:
        try:
            rows = await c.event_repo.recent_by_type("config_change", limit=200)
            items = [
                {
                    "ts": r.created_at.isoformat() if r.created_at else None,
                    "message": r.message,
                    "payload": r.payload,
                }
                for r in rows
            ]
            return {"audit": items, "source": "db"}
        except Exception:  # noqa: BLE001
            pass
    return {"audit": c.config_service.audit(), "source": "memory"}


def _serialize(v: Any) -> Any:
    if v is None:
        return None
    if hasattr(v, "__str__") and not isinstance(v, (int, float, bool, str, list, dict)):
        return str(v)
    return v
