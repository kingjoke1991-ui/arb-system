"""
FastAPI application entry. Serves the JSON API and the static operations panel.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    balances,
    control,
    health,
    hedges,
    marketdata,
    opportunities,
    orders,
    reports,
    strategies,
)
from app.api.routes import (
    config as config_routes,
)
from app.config.settings import get_settings
from app.runtime.app_bootstrap import bootstrap, teardown
from app.services.metrics_service import get_metrics


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="arb-system",
        version="0.1.0",
        description="Cross-exchange spot arbitrage MVP.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def _startup() -> None:
        app.state.container = await bootstrap(settings)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        c = getattr(app.state, "container", None)
        if c:
            await teardown(c)

    app.include_router(health.router)
    app.include_router(config_routes.router)
    app.include_router(balances.router)
    app.include_router(opportunities.router)
    app.include_router(orders.router)
    app.include_router(hedges.router)
    app.include_router(control.router)
    app.include_router(reports.router)
    app.include_router(marketdata.router)
    app.include_router(strategies.router)

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        m = get_metrics()
        return Response(content=m.render(), media_type="text/plain; version=0.0.4")

    # Serve the operations panel at /
    static_dir = Path(__file__).resolve().parent.parent / "frontend" / "static"
    if static_dir.is_dir():
        app.mount(
            "/ui",
            StaticFiles(directory=str(static_dir), html=True),
            name="ui",
        )

        @app.get("/", include_in_schema=False)
        async def root() -> Response:
            index = static_dir / "index.html"
            if index.exists():
                return Response(content=index.read_text(encoding="utf-8"), media_type="text/html")
            return Response(content="<h1>arb-system</h1>", media_type="text/html")

    return app


app = create_app()
