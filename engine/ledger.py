"""Tracks the master's position sizes from his fills, so scale-outs are exact.

Why: asking the exchange for the position AFTER each fill is racy. If he closes 25% and then 50% within
milliseconds, both lookups can already include both fills, and the first close would be over-sized.
Fill-by-fill arithmetic avoids that; the listener periodically re-syncs the ledger with the exchange.
"""
from __future__ import annotations

from decimal import Decimal

from .fills import is_open

Key = tuple[str, str]  # (symbol, position_side)


class PositionLedger:
    def __init__(self) -> None:
        self._qty: dict[Key, Decimal] = {}

    def knows(self, symbol: str, position_side: str) -> bool:
        return (symbol, position_side) in self._qty

    def qty(self, symbol: str, position_side: str) -> Decimal:
        return self._qty.get((symbol, position_side), Decimal(0))

    def keys(self) -> list[Key]:
        return list(self._qty)

    def seed(self, symbol: str, position_side: str, qty: Decimal) -> None:
        self._qty[(symbol, position_side)] = max(qty, Decimal(0))

    def apply(self, symbol: str, side: str, position_side: str, fill_qty: Decimal,
              exchange_qty_after: Decimal | None = None) -> tuple[Decimal, Decimal]:
        """Apply one fill; returns (position_before, position_after).
        `exchange_qty_after` is only used to seed a position we have never seen."""
        opening = is_open(side, position_side)
        key = (symbol, position_side)
        if key in self._qty:
            before = self._qty[key]
        elif exchange_qty_after is not None:
            before = exchange_qty_after - fill_qty if opening else exchange_qty_after + fill_qty
        else:  # unknown history: assume a close takes everything (the safest reading for followers)
            before = Decimal(0) if opening else fill_qty
        before = max(before, Decimal(0))
        after = before + fill_qty if opening else max(before - fill_qty, Decimal(0))
        self._qty[key] = after
        return before, after