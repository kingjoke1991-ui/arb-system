"""Maps canonical symbols (BTC/USDT) to each exchange's venue symbol if needed."""

from __future__ import annotations

from app.models.symbol import CanonicalSymbol, parse_canonical


class SymbolNormalizer:
    """For Binance and OKX, ccxt already accepts canonical ``BTC/USDT``."""

    def __init__(self) -> None:
        self._overrides: dict[tuple[str, str], str] = {}

    def set_override(self, exchange: str, canonical: str, venue_symbol: str) -> None:
        self._overrides[(exchange, canonical.upper())] = venue_symbol

    def to_venue(self, exchange: str, canonical: str) -> str:
        return self._overrides.get((exchange, canonical.upper()), canonical.upper())

    def to_canonical(self, exchange: str, venue_symbol: str) -> CanonicalSymbol:
        return parse_canonical(venue_symbol)
