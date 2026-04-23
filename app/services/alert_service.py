"""Minimal alert pipeline. Webhook delivery; severity-based filtering."""

from __future__ import annotations

import asyncio

import httpx

from app.common.enums import Severity
from app.common.logging import get_logger
from app.config.settings import Settings

log = get_logger("services.alert")


_SEVERITY_ORDER = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2, Severity.CRITICAL: 3}


class AlertService:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._min = Severity(settings.alert_min_severity)

    def _should_send(self, sev: Severity) -> bool:
        return _SEVERITY_ORDER[sev] >= _SEVERITY_ORDER[self._min]

    async def send(self, severity: Severity, title: str, body: str, extra: dict | None = None) -> None:
        if not self._should_send(severity):
            return
        url = self._settings.alert_webhook
        payload = {"severity": severity.value, "title": title, "body": body, "extra": extra or {}}
        log.info("alert", **payload)
        if not url:
            return
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                await client.post(url, json=payload)
        except Exception as e:  # noqa: BLE001
            log.warning("alert_post_failed", error=str(e))
