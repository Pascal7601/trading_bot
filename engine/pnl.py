"""Trade result maths. Pure Python (no Pillow, no Django)."""
from __future__ import annotations

from decimal import Decimal


def roi_pct(side: str, leverage: int, entry: Decimal, exit_: Decimal) -> Decimal:
    """Leveraged return on margin, in percent: price move x leverage.
    An ESTIMATE: it ignores trading fees and funding payments."""
    side = side.upper()
    if side not in ("LONG", "SHORT"):
        raise ValueError(f"side must be LONG or SHORT, got {side!r}")
    if leverage < 1:
        raise ValueError("leverage must be >= 1")
    entry, exit_ = Decimal(entry), Decimal(exit_)
    if entry <= 0 or exit_ <= 0:
        raise ValueError("prices must be positive")
    move = (exit_ - entry) / entry if side == "LONG" else (entry - exit_) / entry
    return move * leverage * 100