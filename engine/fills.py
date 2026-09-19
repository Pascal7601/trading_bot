"""Helpers to interpret a master fill (hedge-mode semantics)."""
from __future__ import annotations

from decimal import Decimal

LONG, SHORT = "LONG", "SHORT"
BUY, SELL = "BUY", "SELL"


def is_open(side: str, position_side: str) -> bool:
    """LONG+BUY and SHORT+SELL open/increase a position; the opposite sides reduce/close it."""
    side, position_side = side.upper(), position_side.upper()
    if position_side not in (LONG, SHORT) or side not in (BUY, SELL):
        raise ValueError(f"unsupported side/position_side: {side}/{position_side}")
    return (position_side == LONG and side == BUY) or (position_side == SHORT and side == SELL)


def close_fraction(fill_qty: Decimal, position_qty_after: Decimal) -> Decimal:
    """Share of the master's pre-fill position that this closing fill represents (0 < x <= 1)."""
    after = max(position_qty_after, Decimal(0))
    before = after + fill_qty
    if fill_qty <= 0 or before <= 0:
        return Decimal(0)
    return min(fill_qty / before, Decimal(1))