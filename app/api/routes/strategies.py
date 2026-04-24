"""Strategy discovery + toggle + exchange-binding API.

GET  /strategies                   — catalog (static) + enabled flag + selected exchanges
POST /strategies/{id}/enable       — flip on; rejected unless status=READY/DETECT_ONLY
                                     AND the selected exchange count meets min/max AND
                                     (in live mode) those exchanges have API keys configured.
POST /strategies/{id}/disable      — unconditional off.
POST /strategies/{id}/exchanges    — update the selected exchange list (validates count).
"""

from __future__ import annotations

from dataclasses import asdict
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_container, require_admin
from app.api.rate_limit import rate_limit
from app.common.enums import Mode
from app.runtime.dependency_container import Container
from app.strategy.registry import (
    STRATEGIES,
    StrategyStatus,
)
from app.strategy.registry import (
    get as get_meta,
)

router = APIRouter(prefix="/strategies", tags=["strategies"])


def _enabled_attr(sid: str) -> str:
    return f"strategy_{sid}_enabled"


def _exchanges_attr(sid: str) -> str:
    return f"strategy_{sid}_exchanges"


def _selected_exchanges(settings, sid: str) -> list[str]:
    raw = (getattr(settings, _exchanges_attr(sid), "") or "").strip()
    return [x.strip() for x in raw.split(",") if x.strip()]


@router.get("")
async def list_strategies(c: Container = Depends(get_container)) -> dict:
    available = c.registry.names()
    items = []
    for meta in STRATEGIES:
        flag = getattr(c.settings, _enabled_attr(meta.id), False)
        sel = _selected_exchanges(c.settings, meta.id)
        items.append(
            {
                **asdict(meta),
                "enabled": bool(flag),
                # Readable status badge string for the UI
                "status_zh": {
                    StrategyStatus.READY: "可用",
                    StrategyStatus.DETECT_ONLY: "仅检测（暂不执行）",
                    StrategyStatus.PLANNED: "未实现 / V2",
                }.get(meta.status, meta.status),
                "selected_exchanges": sel,
                "available_exchanges": available,
            }
        )
    return {"strategies": items}


def _validate_exchange_selection(settings, meta, names: list[str]) -> None:
    """Enforce count constraint + (in live mode) keys-configured constraint."""
    # Dedupe + normalize
    seen: list[str] = []
    for n in names:
        if n and n not in seen:
            seen.append(n)
    n = len(seen)
    if n < meta.min_exchanges:
        raise HTTPException(
            status_code=400,
            detail=(f"策略 {meta.id} 至少需要 {meta.min_exchanges} 个交易所，当前只选了 {n} 个。"),
        )
    if n > meta.max_exchanges:
        raise HTTPException(
            status_code=400,
            detail=(f"策略 {meta.id} 最多允许 {meta.max_exchanges} 个交易所，当前选了 {n} 个。"),
        )
    # In live mode, each selected exchange must be an actual configured adapter
    # (connected — i.e. has credentials). In other modes this is informational.
    if settings.mode == Mode.LIVE.value:
        registry_names = [e for e in _live_configured_exchanges(settings)]
        missing = [e for e in seen if e not in registry_names]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"live 模式下，所选交易所 {missing} 缺少 API 凭据。"
                    "请先在控制台填入并测试通过，或切换到 dry-run / paper-trade。"
                ),
            )


def _live_configured_exchanges(settings) -> list[str]:
    out = []
    if settings.binance_api_key and settings.binance_api_secret:
        out.append("binance")
    if settings.okx_api_key and settings.okx_api_secret and settings.okx_passphrase:
        out.append("okx")
    return out


@router.post("/{sid}/enable", dependencies=[Depends(require_admin), Depends(rate_limit)])
async def enable(sid: str, c: Container = Depends(get_container)) -> dict:
    meta = get_meta(sid)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown strategy: {sid}")
    if meta.status == StrategyStatus.PLANNED:
        raise HTTPException(
            status_code=400,
            detail=f"strategy {sid} is PLANNED and has no implementation yet; enable rejected. See registry description for V2 timeline.",
        )
    # Validate the current selection before flipping on.
    sel = _selected_exchanges(c.settings, sid)
    _validate_exchange_selection(c.settings, meta, sel)

    attr = _enabled_attr(sid)
    if not hasattr(c.settings, attr):
        raise HTTPException(status_code=500, detail=f"no settings flag for {sid}")
    setattr(c.settings, attr, True)
    return {"id": sid, "enabled": True, "status": meta.status, "selected_exchanges": sel}


@router.post("/{sid}/disable", dependencies=[Depends(require_admin), Depends(rate_limit)])
async def disable(sid: str, c: Container = Depends(get_container)) -> dict:
    meta = get_meta(sid)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown strategy: {sid}")
    attr = _enabled_attr(sid)
    if not hasattr(c.settings, attr):
        raise HTTPException(status_code=500, detail=f"no settings flag for {sid}")
    setattr(c.settings, attr, False)
    return {"id": sid, "enabled": False, "status": meta.status}


class ExchangesBody(BaseModel):
    exchanges: List[str]


@router.post("/{sid}/exchanges", dependencies=[Depends(require_admin), Depends(rate_limit)])
async def set_exchanges(sid: str, body: ExchangesBody, c: Container = Depends(get_container)) -> dict:
    meta = get_meta(sid)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"unknown strategy: {sid}")
    available = set(c.registry.names())
    unknown = [e for e in body.exchanges if e not in available]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"未知交易所：{unknown}；可用：{sorted(available)}",
        )
    _validate_exchange_selection(c.settings, meta, body.exchanges)

    attr = _exchanges_attr(sid)
    if not hasattr(c.settings, attr):
        raise HTTPException(status_code=500, detail=f"no settings exchanges field for {sid}")
    # Persist as normalized CSV
    setattr(c.settings, attr, ",".join(body.exchanges))
    return {"id": sid, "selected_exchanges": body.exchanges}
