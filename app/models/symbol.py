from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CanonicalSymbol:
    base: str
    quote: str
    canonical: str  # e.g. BTC/USDT
    venue_symbol: str | None = None  # e.g. BTCUSDT on binance


def parse_canonical(symbol: str) -> CanonicalSymbol:
    if "/" not in symbol:
        raise ValueError(f"canonical symbol must contain '/': {symbol}")
    base, quote = symbol.upper().split("/", 1)
    return CanonicalSymbol(base=base, quote=quote, canonical=f"{base}/{quote}")
