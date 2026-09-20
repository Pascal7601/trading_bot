"""Tracks the master's positions from his fills: exact scale-out sizing, plus the entry/exit needed for PnL cards.

Why fill-by-fill: asking the exchange for the position AFTER each fill is racy. If he closes 25% and then 50%
within milliseconds, both lookups can already include both fills, and the first close would be over-sized.
Fill-by-fill arithmetic avoids that; the listener periodically re-syncs the ledger with the exchange.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .fills import is_open

Key = tuple[str, str]  # (symbol, position_side)


@dataclass
class _Pos:
    qty: Decimal = Decimal(0)
    avg_entry: Decimal | None = None
    leverage: int | None = None
    closed_qty: Decimal = Decimal(0)    # size closed so far in this position's life...
    closed_value: Decimal = Decimal(0)  # ...and what it was worth at the closing prices


@dataclass(frozen=True)
class TradeResult:
    """A fully closed position: average entry, average exit over ALL its closes, and leverage."""
    symbol: str
    position_side: str
    entry_price: Decimal
    exit_price: Decimal
    leverage: int | None
    closed_qty: Decimal


class PositionLedger:
    def __init__(self) -> None:
        self._pos: dict[Key, _Pos] = {}

    def knows(self, symbol: str, position_side: str) -> bool:
        return (symbol, position_side) in self._pos

    def qty(self, symbol: str, position_side: str) -> Decimal:
        pos = self._pos.get((symbol, position_side))
        return pos.qty if pos else Decimal(0)

    def keys(self) -> list[Key]:
        return list(self._pos)

    def seed(self, symbol: str, position_side: str, qty: Decimal, avg_entry: Decimal | None = None,
             leverage: int | None = None) -> None:
        """Set the size from the exchange. Entry price / leverage are only overwritten when given."""
        pos = self._pos.setdefault((symbol, position_side), _Pos())
        pos.qty = max(qty, Decimal(0))
        if pos.qty == 0:
            pos.avg_entry, pos.closed_qty, pos.closed_value = None, Decimal(0), Decimal(0)
        elif avg_entry is not None and avg_entry > 0:
            pos.avg_entry = avg_entry
        if leverage is not None:
            pos.leverage = leverage

    def apply(self, symbol: str, side: str, position_side: str, fill_qty: Decimal,
              exchange_qty_after: Decimal | None = None, price: Decimal | None = None,
              leverage: int | None = None) -> tuple[Decimal, Decimal]:
        """Apply one fill; returns (position_before, position_after).
        `exchange_qty_after` is only used to seed a position we have never seen."""
        opening = is_open(side, position_side)
        key = (symbol, position_side)
        known = key in self._pos
        pos = self._pos.setdefault(key, _Pos())
        if known:
            before = pos.qty
        elif exchange_qty_after is not None:
            before = exchange_qty_after - fill_qty if opening else exchange_qty_after + fill_qty
        else:  # unknown history: assume a close takes everything (the safest reading for followers)
            before = Decimal(0) if opening else fill_qty
        before = max(before, Decimal(0))
        after = before + fill_qty if opening else max(before - fill_qty, Decimal(0))

        px = price if price is not None and price > 0 else None
        if opening:
            if before <= 0:                       # a brand-new position starts a new life
                pos.closed_qty, pos.closed_value = Decimal(0), Decimal(0)
                pos.avg_entry = px
            elif px is not None and pos.avg_entry is not None:
                pos.avg_entry = (before * pos.avg_entry + fill_qty * px) / after
            if leverage is not None:
                pos.leverage = leverage
        else:
            done = min(fill_qty, before)
            if px is not None and done > 0:
                pos.closed_qty += done
                pos.closed_value += done * px
        pos.qty = after
        return before, after

    def take_result(self, symbol: str, position_side: str) -> TradeResult | None:
        """After a position is fully closed: its result (once), then reset for the next trade."""
        pos = self._pos.get((symbol, position_side))
        if pos is None or pos.qty != 0:
            return None
        result = None
        if pos.closed_qty > 0 and pos.avg_entry is not None:
            result = TradeResult(symbol, position_side, pos.avg_entry, pos.closed_value / pos.closed_qty,
                                 pos.leverage, pos.closed_qty)
        pos.avg_entry, pos.closed_qty, pos.closed_value = None, Decimal(0), Decimal(0)
        return result