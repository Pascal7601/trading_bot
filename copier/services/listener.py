"""Watches the master's account (read-only key) and records his filled orders as MasterEvents.

Position sizes come from a fill-by-fill ledger (see engine/ledger.py), re-synced with the exchange
every minute, so partial take-profits / scale-outs are sized exactly.
"""
import asyncio
import logging
import time
from decimal import Decimal

from django.conf import settings

from engine.fills import is_open
from engine.ledger import PositionLedger
from exchange.bingx import BingXClient
from exchange.parsing import RawFill, parse_fill_event
from exchange.stream import stream_messages

from ..models import MasterEvent

log = logging.getLogger(__name__)

ledger = PositionLedger()
_last_fill_at: dict[tuple[str, str], float] = {}
_state: dict[str, Decimal] = {}  # last known master equity


async def seed_ledger(master: BingXClient) -> None:
    for p in await master.get_positions():
        ledger.seed(p.symbol, p.position_side, p.qty)
    log.info("ledger seeded with %d open master positions", len(ledger.keys()))


async def record_fill(master: BingXClient, fill: RawFill) -> None:
    if fill.position_side not in ("LONG", "SHORT"):
        log.error("Master order %s has position side %r: master must use HEDGE mode. Ignored.",
                  fill.order_id, fill.position_side)
        return
    opening = is_open(fill.side, fill.position_side)
    leverage, exchange_qty = None, None
    if opening:  # leverage and the current equity are needed to size followers
        positions = await master.get_positions(fill.symbol)
        pos = next((p for p in positions if p.position_side == fill.position_side), None)
        leverage, exchange_qty = (pos.leverage, pos.qty) if pos else (None, None)
        _state["equity"] = await master.get_equity()
    equity = _state.get("equity") or await master.get_equity()

    if not ledger.knows(fill.symbol, fill.position_side) and exchange_qty is None:
        log.warning("closing fill on a position the ledger has never seen (%s %s): copying as a FULL close",
                    fill.symbol, fill.position_side)
    _, after = ledger.apply(fill.symbol, fill.side, fill.position_side, fill.quantity, exchange_qty)
    _last_fill_at[(fill.symbol, fill.position_side)] = time.monotonic()

    _, created = await MasterEvent.objects.aget_or_create(
        order_id=fill.order_id,
        defaults=dict(
            symbol=fill.symbol, side=fill.side, position_side=fill.position_side,
            quantity=fill.quantity, price=fill.avg_price, leverage=leverage,
            position_qty_after=after, master_equity=equity, raw=fill.raw,
        ),
    )
    log.info("master fill %s %s %s/%s qty=%s (position now %s) %s", fill.order_id, fill.symbol, fill.side,
             fill.position_side, fill.quantity, after, "recorded" if created else "duplicate ignored")


async def resync_loop(master: BingXClient, every: float = 60, quiet: float = 5) -> None:
    """Correct any drift between the ledger and the exchange (missed messages, manual transfers, ...)."""
    while True:
        await asyncio.sleep(every)
        try:
            live = {(p.symbol, p.position_side): p.qty for p in await master.get_positions()}
            now = time.monotonic()
            for key in set(live) | set(ledger.keys()):
                if now - _last_fill_at.get(key, 0) < quiet:
                    continue  # a fill just happened: the exchange number may be ahead of us
                real, mine = live.get(key, Decimal(0)), ledger.qty(*key)
                if real != mine:
                    log.warning("ledger drift on %s: ledger=%s exchange=%s -> resynced", key, mine, real)
                    ledger.seed(*key, real)
        except Exception:
            log.exception("ledger resync failed")


async def run_listener() -> None:
    async with BingXClient(settings.MASTER_API_KEY, settings.MASTER_API_SECRET,
                           base_url=settings.BINGX_BASE_URL) as master:
        try:
            await seed_ledger(master)
        except Exception:
            log.exception("could not seed the ledger; the periodic resync will retry")
        resync = asyncio.create_task(resync_loop(master))
        try:
            async for msg in stream_messages(master):
                if settings.LOG_RAW_STREAM:
                    log.info("RAW %s", msg)
                fill = parse_fill_event(msg)
                if fill is None:
                    continue
                try:
                    await record_fill(master, fill)  # sequential on purpose: fills are handled in order
                except Exception:
                    # The fill is lost to followers if this fails: alert loudly (see README TODO: admin alerts).
                    log.exception("FAILED to record master fill %s", fill.order_id)
        finally:
            resync.cancel()