"""Exchange API credential management.

Exposes read (masked) + write endpoints for the Binance/OKX API keys. Writes
persist to the .env file on disk so they survive a process restart, then
reload_settings() is called to reflect the new values in-process. Live
adapter reconnection happens on the next balance/orderbook cycle because
each adapter reads the creds lazily at connect-time.

Security notes:
- Secret and passphrase are never returned in full. Key is returned
  partially masked so the operator can verify which account is configured.
- GET does NOT require admin token (so the UI can show masked status without
  revealing anything useful); POST / reveal DO require it.
- The .env file lives on the same box as the process. There is no
  additional encryption at rest beyond the filesystem permissions.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import get_container, require_admin
from app.api.rate_limit import rate_limit
from app.runtime.dependency_container import Container

router = APIRouter(prefix="/exchanges", tags=["credentials"])


# Map of exchange name -> (env var names for key, secret, passphrase, sandbox).
# Built from the catalog so "add an exchange" = one catalog entry + settings
# fields. Perpetual entries use ``{id}-perp`` suffix. Entries whose spec says
# supports_perp=False don't emit a perp card (Kraken, Coinbase).
def _build_exchange_map() -> dict[str, dict]:
    from app.adapters.exchanges_catalog import SUPPORTED_EXCHANGES

    m: dict[str, dict] = {}
    for spec in SUPPORTED_EXCHANGES:
        upper = spec.id.upper()
        m[spec.id] = {
            "key": f"{upper}_API_KEY",
            "secret": f"{upper}_API_SECRET",
            "passphrase": f"{upper}_PASSPHRASE" if spec.requires_passphrase else None,
            "sandbox": f"{upper}_SANDBOX",
            "display_name": spec.display_name,
            "default_taker_bps": str(spec.default_taker_bps),
            "notes": spec.notes,
            "kind": "spot",
        }
        if spec.supports_perp:
            m[f"{spec.id}-perp"] = {
                "key": f"{upper}_PERP_API_KEY",
                "secret": f"{upper}_PERP_API_SECRET",
                "passphrase": f"{upper}_PERP_PASSPHRASE" if spec.perp_requires_passphrase else None,
                "sandbox": f"{upper}_SANDBOX",
                "display_name": f"{spec.display_name} 永续",
                "default_taker_bps": str(spec.default_taker_bps),
                "notes": "",
                "kind": "perp",
            }
    return m


_EXCHANGES: dict[str, dict] = _build_exchange_map()


def _mask(v: str, show: int = 4) -> str:
    if not v:
        return ""
    if len(v) <= show * 2:
        return "•" * len(v)
    return f"{v[:show]}{'•' * min(len(v) - show * 2, 16)}{v[-show:]}"


def _env_path() -> Path:
    # Allow override for tests; default = repo root / .env
    override = os.environ.get("ARB_ENV_FILE")
    if override:
        return Path(override)
    # main.py is in app/, so repo root = parent of app/
    # but when running in docker the cwd is /app and .env is mounted at /app/.env
    # via docker-compose env_file. We can detect both:
    for candidate in (Path("/app/.env"), Path.cwd() / ".env"):
        if candidate.exists():
            return candidate
    return Path.cwd() / ".env"


def _read_env_file() -> dict[str, str]:
    path = _env_path()
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _write_env_updates(updates: dict[str, str]) -> None:
    """Update-or-append each key in the .env file, preserving other lines + comments."""
    path = _env_path()
    if not path.exists():
        path.write_text("", encoding="utf-8")
    lines = path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    out_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            k = stripped.split("=", 1)[0].strip()
            if k in remaining:
                out_lines.append(f"{k}={remaining.pop(k)}")
                continue
        out_lines.append(line)
    for k, v in remaining.items():
        out_lines.append(f"{k}={v}")
    path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


class CredentialsPayload(BaseModel):
    key: str | None = None
    secret: str | None = None
    passphrase: str | None = None
    sandbox: bool | None = None


def _status_for(name: str, c: Container, reveal: bool = False) -> dict:
    if name not in _EXCHANGES:
        raise HTTPException(status_code=404, detail=f"unknown exchange: {name}")
    cfg = _EXCHANGES[name]
    s = c.settings
    # Prefer live settings values (they may have been updated without file sync)
    key_val = getattr(s, cfg["key"].lower(), "") or ""
    secret_val = getattr(s, cfg["secret"].lower(), "") or ""
    passphrase_val = getattr(s, cfg["passphrase"].lower(), "") or "" if cfg["passphrase"] else ""
    sandbox_val = bool(getattr(s, cfg["sandbox"].lower(), False))
    if reveal:
        return {
            "name": name,
            "display_name": cfg.get("display_name", name),
            "kind": cfg.get("kind", "spot"),
            "default_taker_bps": cfg.get("default_taker_bps", ""),
            "notes": cfg.get("notes", ""),
            "configured": bool(key_val and secret_val),
            "key": key_val,
            "secret": secret_val,
            "passphrase": passphrase_val,
            "sandbox": sandbox_val,
        }
    return {
        "name": name,
        "display_name": cfg.get("display_name", name),
        "kind": cfg.get("kind", "spot"),
        "default_taker_bps": cfg.get("default_taker_bps", ""),
        "notes": cfg.get("notes", ""),
        "configured": bool(key_val and secret_val),
        "key_masked": _mask(key_val),
        "secret_masked": _mask(secret_val, show=0) if secret_val else "",
        "passphrase_masked": _mask(passphrase_val, show=0) if passphrase_val else "",
        "has_passphrase_slot": cfg["passphrase"] is not None,
        "sandbox": sandbox_val,
    }


@router.get("/credentials")
async def list_credentials(c: Container = Depends(get_container)) -> dict:
    """Masked summary for all known exchanges. Safe for non-admin viewing."""
    return {"exchanges": [_status_for(n, c) for n in _EXCHANGES]}


@router.get("/{name}/credentials/reveal", dependencies=[Depends(require_admin), Depends(rate_limit)])
async def reveal(name: str, c: Container = Depends(get_container)) -> dict:
    return _status_for(name, c, reveal=True)


_SAFE_ENV_VALUE = re.compile(r"^[A-Za-z0-9_\-./:+=~@!%^&*()\[\]{}|<>?, ]*$")


def _validate_env_value(key: str, v: str) -> None:
    if "\n" in v or "\r" in v:
        raise HTTPException(status_code=400, detail=f"{key} contains newline")
    if not _SAFE_ENV_VALUE.match(v):
        raise HTTPException(status_code=400, detail=f"{key} contains disallowed characters")
    if len(v) > 256:
        raise HTTPException(status_code=400, detail=f"{key} too long")


@router.post(
    "/{name}/credentials",
    dependencies=[Depends(require_admin), Depends(rate_limit)],
)
async def update_credentials(
    name: str,
    payload: CredentialsPayload = Body(...),
    c: Container = Depends(get_container),
) -> dict:
    if name not in _EXCHANGES:
        raise HTTPException(status_code=404, detail=f"unknown exchange: {name}")
    cfg = _EXCHANGES[name]

    updates_env: dict[str, str] = {}
    updates_settings: dict[str, object] = {}
    if payload.key is not None:
        _validate_env_value(cfg["key"], payload.key)
        updates_env[cfg["key"]] = payload.key
        updates_settings[cfg["key"].lower()] = payload.key
    if payload.secret is not None:
        _validate_env_value(cfg["secret"], payload.secret)
        updates_env[cfg["secret"]] = payload.secret
        updates_settings[cfg["secret"].lower()] = payload.secret
    if payload.passphrase is not None:
        if cfg["passphrase"] is None:
            raise HTTPException(status_code=400, detail=f"{name} has no passphrase field")
        _validate_env_value(cfg["passphrase"], payload.passphrase)
        updates_env[cfg["passphrase"]] = payload.passphrase
        updates_settings[cfg["passphrase"].lower()] = payload.passphrase
    if payload.sandbox is not None:
        updates_env[cfg["sandbox"]] = "true" if payload.sandbox else "false"
        updates_settings[cfg["sandbox"].lower()] = bool(payload.sandbox)

    if not updates_env:
        raise HTTPException(status_code=400, detail="no fields to update")

    _write_env_updates(updates_env)
    # Apply to live settings so the next adapter reconnect picks up new values.
    for k, v in updates_settings.items():
        if hasattr(c.settings, k):
            setattr(c.settings, k, v)

    if c.event_repo:
        try:
            await c.event_repo.log(
                event_type="credentials_update",
                severity="info",
                component="credentials",
                message=f"updated credentials for {name}",
                payload={"name": name, "fields": sorted(updates_env.keys())},
            )
        except Exception:  # noqa: BLE001
            pass

    return {"ok": True, "updated_env_keys": sorted(updates_env.keys())}


@router.post(
    "/{name}/credentials/test",
    dependencies=[Depends(require_admin), Depends(rate_limit)],
)
async def test_credentials(
    name: str,
    mode: Literal["balances"] = Body(default="balances", embed=True),
    c: Container = Depends(get_container),
) -> dict:
    """Smoke-test current credentials by calling fetch_balances on the live adapter.

    Does NOT modify state. Returns a success flag + latency + either
    the first few non-zero asset names or the error message.
    """
    import time

    adapter = None
    if name.endswith("-perp"):
        adapter = c.perp_registry.get(name.removesuffix("-perp"))
    else:
        try:
            adapter = c.registry.get(name)
        except KeyError:
            adapter = None
    if adapter is None:
        raise HTTPException(status_code=404, detail=f"adapter {name} not found")
    start = time.perf_counter()
    try:
        snaps = await adapter.fetch_balances()
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        non_zero = [s.asset for s in snaps if (s.free + s.locked) > 0][:5]
        return {
            "ok": True,
            "name": name,
            "latency_ms": elapsed_ms,
            "mode": mode,
            "assets_sample": non_zero,
        }
    except Exception as e:  # noqa: BLE001
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        return {
            "ok": False,
            "name": name,
            "latency_ms": elapsed_ms,
            "mode": mode,
            "error": str(e)[:300],
        }
