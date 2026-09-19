"""Per-follower risk checks. Functions return a reason string when a trade must be skipped."""
from __future__ import annotations

from decimal import Decimal

from .types import RiskLimits


def check_open(limits: RiskLimits, symbol: str) -> str | None:
    symbol = symbol.upper()
    if symbol in limits.blocked_symbols:
        return f"{symbol} is blocked in your settings"
    if limits.allowed_symbols and symbol not in limits.allowed_symbols:
        return f"{symbol} is not in your allowed symbols"
    return None


def effective_leverage(master_leverage: int | None, limits: RiskLimits) -> int | None:
    """Mirror the master's leverage, capped by the follower's own maximum.
    None means 'leave the follower's current leverage setting alone'."""
    if master_leverage is None:
        return None
    if limits.max_leverage is not None:
        return min(master_leverage, limits.max_leverage)
    return master_leverage


def adverse_slippage_pct(position_side: str, master_price: Decimal, market_price: Decimal) -> Decimal:
    """How much WORSE (in %) the follower's entry is than the master's. Positive = worse.
    A LONG is hurt by a higher price, a SHORT by a lower one."""
    if master_price <= 0:
        return Decimal(0)
    move = (market_price - master_price) / master_price * 100
    return move if position_side.upper() == "LONG" else -move


def check_slippage(limits: RiskLimits, position_side: str, master_price: Decimal,
                   market_price: Decimal | None) -> str | None:
    if limits.max_slippage_pct is None:
        return None
    if market_price is None:  # fail closed: we cannot prove the price is acceptable
        return "couldn't verify the current price, so the trade was skipped for safety"
    slip = adverse_slippage_pct(position_side, master_price, market_price)
    if slip > limits.max_slippage_pct:
        return (f"price moved {slip:.2f}% against the trader's entry "
                f"(your limit is {limits.max_slippage_pct.normalize():f}%)")
    return None


def check_exposure(limits: RiskLimits, used_margin: Decimal, new_margin: Decimal, equity: Decimal) -> str | None:
    if limits.max_exposure_pct is None or equity <= 0:
        return None
    after = (used_margin + new_margin) / equity * 100
    if after > limits.max_exposure_pct:
        return (f"this trade would put {after:.0f}% of your equity in margin "
                f"(your limit is {limits.max_exposure_pct.normalize():f}%)")
    return None


def drawdown_pct(peak_equity: Decimal, current_equity: Decimal) -> Decimal:
    """Percent below the peak (0 when at or above it)."""
    if peak_equity <= 0 or current_equity >= peak_equity:
        return Decimal(0)
    return (peak_equity - current_equity) / peak_equity * 100


def daily_loss_hit(limits: RiskLimits, peak_equity: Decimal, current_equity: Decimal) -> bool:
    return (limits.max_daily_loss_pct is not None
            and drawdown_pct(peak_equity, current_equity) >= limits.max_daily_loss_pct)


def stop_loss_hit(limits: RiskLimits, roi_pct_now: Decimal) -> bool:
    return limits.stop_loss_roi_pct is not None and roi_pct_now <= -limits.stop_loss_roi_pct