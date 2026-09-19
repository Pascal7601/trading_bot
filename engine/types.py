"""Plain data types for the copy engine. No Django, no network: easy to unit test."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum


class SizingMode(str, Enum):
    # Follower notional = master notional * (follower equity / master equity) * value
    PROPORTIONAL = "proportional"
    # Follower notional = value (USDT) on every opening trade
    FIXED_NOTIONAL = "fixed_notional"
    # Follower quantity = master quantity * value
    MULTIPLIER = "multiplier"


@dataclass(frozen=True)
class SymbolRules:
    step_size: Decimal    # quantity increment
    min_qty: Decimal
    min_notional: Decimal  # minimum order value in USDT (0 if none)


@dataclass(frozen=True)
class RiskLimits:
    max_notional_per_trade: Decimal | None = None
    max_leverage: int | None = None
    allowed_symbols: frozenset[str] = frozenset()  # empty = all allowed
    blocked_symbols: frozenset[str] = frozenset()
    max_slippage_pct: Decimal | None = None    # adverse move vs the master's entry price
    max_exposure_pct: Decimal | None = None    # margin in use / equity, counting the new trade
    max_daily_loss_pct: Decimal | None = None  # drawdown from the 24h equity peak
    stop_loss_roi_pct: Decimal | None = None   # close a position at -X% ROI
    flatten_on_max_loss: bool = False          # also close all positions when the daily limit trips


@dataclass(frozen=True)
class SizingResult:
    quantity: Decimal | None
    skip_reason: str | None = None