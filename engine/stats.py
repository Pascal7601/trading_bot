"""Period performance stats from exchange income records. Pure Python."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal

REALIZED = {"REALIZED_PNL"}
FEES = {"TRADING_FEE", "COMMISSION"}
FUNDING = {"FUNDING_FEE"}
# Everything else (transfers, bonuses, ...) is ignored on purpose: deposits are not performance.


@dataclass(frozen=True)
class Income:
    kind: str
    amount: Decimal


@dataclass(frozen=True)
class PeriodStats:
    closes: int
    wins: int
    losses: int
    realized: Decimal
    fees: Decimal
    funding: Decimal
    net: Decimal
    net_pct: Decimal | None
    best: Decimal
    worst: Decimal


def summarize(items: Iterable[Income], start_equity: Decimal | None) -> PeriodStats:
    realized_list, fees, funding = [], Decimal(0), Decimal(0)
    for it in items:
        kind = it.kind.upper()
        if kind in REALIZED:
            if it.amount != 0:
                realized_list.append(it.amount)
        elif kind in FEES:
            fees += it.amount
        elif kind in FUNDING:
            funding += it.amount
    realized = sum(realized_list, Decimal(0))
    net = realized + fees + funding
    net_pct = net / start_equity * 100 if start_equity and start_equity > 0 else None
    return PeriodStats(
        closes=len(realized_list),
        wins=sum(1 for x in realized_list if x > 0),
        losses=sum(1 for x in realized_list if x < 0),
        realized=realized, fees=fees, funding=funding, net=net, net_pct=net_pct,
        best=max(realized_list, default=Decimal(0)), worst=min(realized_list, default=Decimal(0)),
    )


def _m(x: Decimal) -> str:
    return f"{x:+,.2f}"


def format_report(s: PeriodStats, days: int) -> str:
    rate = f"  ({s.wins / s.closes * 100:.0f}% win rate)" if s.closes else ""
    pct = f" ({s.net_pct:+.1f}%)" if s.net_pct is not None else ""
    lines = [
        f"📊 Your last {days} days",
        f"Closed trades: {s.closes}  |  Wins: {s.wins}  |  Losses: {s.losses}{rate}",
        f"Net P&L: {_m(s.net)} USDT{pct}",
        f"Realized {_m(s.realized)} · Fees {_m(s.fees)} · Funding {_m(s.funding)}",
    ]
    if s.closes:
        lines.append(f"Best close {_m(s.best)} · Worst close {_m(s.worst)}")
    lines.append("Based on your BingX records. Past results don't predict future results.")
    return "\n".join(lines)