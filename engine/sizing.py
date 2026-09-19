"""Turn a master opening fill into a follower order quantity."""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

from .types import RiskLimits, SizingMode, SizingResult, SymbolRules


def round_down(value: Decimal, step: Decimal) -> Decimal:
    if step <= 0:
        return value
    return (value / step).to_integral_value(rounding=ROUND_DOWN) * step


def compute_open_quantity(
    *,
    mode: SizingMode,
    value: Decimal,
    master_qty: Decimal,
    price: Decimal,
    master_equity: Decimal,
    follower_equity: Decimal,
    rules: SymbolRules,
    limits: RiskLimits | None = None,
) -> SizingResult:
    if price <= 0 or master_qty <= 0:
        return SizingResult(None, "invalid master fill")
    if value <= 0:
        return SizingResult(None, "sizing value must be positive")
    if follower_equity <= 0:
        return SizingResult(None, "follower has no available equity")

    if mode == SizingMode.PROPORTIONAL:
        if master_equity <= 0:
            return SizingResult(None, "master equity unknown")
        notional = master_qty * price * (follower_equity / master_equity) * value
        qty = notional / price
    elif mode == SizingMode.FIXED_NOTIONAL:
        qty = value / price
    elif mode == SizingMode.MULTIPLIER:
        qty = master_qty * value
    else:  # pragma: no cover
        return SizingResult(None, f"unknown sizing mode {mode}")

    if limits and limits.max_notional_per_trade is not None:
        qty = min(qty, limits.max_notional_per_trade / price)

    qty = round_down(qty, rules.step_size)
    if qty < rules.min_qty or qty * price < rules.min_notional:
        return SizingResult(None, "below the exchange minimum order size")
    return SizingResult(qty)