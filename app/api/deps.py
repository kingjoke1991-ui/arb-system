"""FastAPI dependency helpers."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, Request, status

from app.config.settings import get_settings
from app.runtime.dependency_container import Container


def get_container(request: Request) -> Container:
    c = getattr(request.app.state, "container", None)
    if c is None:
        raise HTTPException(status_code=503, detail="app_not_ready")
    return c


def require_admin(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> None:
    settings = get_settings()
    if not settings.admin_api_token or x_admin_token != settings.admin_api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_admin_token")
