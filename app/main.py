"""
FastAPI application entry. Serves the JSON API and the static operations panel.
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import (
    balances,
    control,
    credentials,
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
    app.include_router(credentials.router)

    @app.get("/metrics")
    async def metrics_endpoint() -> Response:
        m = get_metrics()
        return Response(content=m.render(), media_type="text/plain; version=0.0.4")

    # Serve the operations panel at /.  Cache-busting:
    #  - index.html / app.js / styles.css are served with Cache-Control:
    #    no-store so that Cloudflare's default 4h CDN cache does not pin an
    #    outdated combination of HTML + JS.
    #  - Additionally we rewrite the src/href of app.js & styles.css in
    #    index.html to include ?v=<build_ts> so already-cached browser copies
    #    revalidate.
    static_dir = Path(__file__).resolve().parent.parent / "frontend" / "static"
    build_tag = str(int(time.time()))

    class NoStoreForHtmlJs(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            resp = await call_next(request)
            p = request.url.path
            if p == "/" or p.endswith((".html", ".js", ".css")):
                resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
                resp.headers["Pragma"] = "no-cache"
            return resp

    app.add_middleware(NoStoreForHtmlJs)

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
                html = index.read_text(encoding="utf-8")
                html = html.replace("/ui/styles.css", f"/ui/styles.css?v={build_tag}")
                html = html.replace("/ui/app.js", f"/ui/app.js?v={build_tag}")
                return Response(
                    content=html,
                    media_type="text/html",
                    headers={"Cache-Control": "no-store"},
                )
            return Response(content="<h1>arb-system</h1>", media_type="text/html")

        @app.get("/m", include_in_schema=False)
        @app.get("/m/", include_in_schema=False)
        async def mobile_root() -> Response:
            """Mobile-first operations panel (phones 320–430px)."""
            page = static_dir / "mobile.html"
            if page.exists():
                html = page.read_text(encoding="utf-8")
                html = html.replace("/ui/mobile.css", f"/ui/mobile.css?v={build_tag}")
                html = html.replace("/ui/mobile.js", f"/ui/mobile.js?v={build_tag}")
                return Response(
                    content=html,
                    media_type="text/html",
                    headers={"Cache-Control": "no-store"},
                )
            return Response(content="<h1>arb-system · mobile</h1>", media_type="text/html")

    return app


app = create_app()
