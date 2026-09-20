"""Watches the master's account (read-only key) and records his filled orders as MasterEvents.

Position sizes come from a fill-by-fill ledger (see engine/ledger.py), re-synced with the exchange
every minute, so partial take-profits / scale-outs are sized exactly.
A watchdog alerts the admin when the stream is down or silent.
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
from .alerts import alert_admin
from .heartbeat import beat

log = logging.getLogger(__name__)

ledger = PositionLedger()
_last_fill_at: dict[tuple[str, str], float] = {}
_state: dict[str, Decimal] = {}  # last known master equity
health = {"connected": False, "last_frame": time.monotonic()}


def _on_stream_event(event: str) -> None:
    if event == "connected":
        health["connected"], health["last_frame"] = True, time.monotonic()
    elif event == "frame":
        health["last_frame"] = time.monotonic()
    elif event == "disconnected":
        health["connected"] = False


async def seed_ledger(master: BingXClient) -> None:
    for p in await master.get_positions():
        ledger.seed(p.symbol, p.position_side, p.qty, p.avg_price, p.leverage)
    log.info("ledger seeded with %d open master positions", len(ledger.keys()))


async def record_fill(master: BingXClient, fill: RawFill) -> None:
    if fill.position_side not in ("LONG", "SHORT"):
        await alert_admin(f"Master order {fill.order_id} ({fill.symbol}) came from a one-way-mode position and was "
                          "IGNORED. The master account must use HEDGE mode.", key="master_hedge")
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
    _, after = ledger.apply(fill.symbol, fill.side, fill.position_side, fill.quantity, exchange_qty,
                        price=fill.avg_price, leverage=leverage)
    _last_fill_at[(fill.symbol, fill.position_side)] = time.monotonic()
    result = None if opening or after > 0 else ledger.take_result(fill.symbol, fill.position_side)
    card = {}
    if result is not None:  # the position is fully closed: remember its result for the PnL card
        card = dict(result_entry_price=result.entry_price, result_exit_price=result.exit_price,
                    result_leverage=result.leverage, result_qty=result.closed_qty,
                    card_status="pending" if settings.CARD_MODE != "off" else "none")

    _, created = await MasterEvent.objects.aget_or_create(
        order_id=fill.order_id,
        defaults=dict(
            symbol=fill.symbol, side=fill.side, position_side=fill.position_side,
            quantity=fill.quantity, price=fill.avg_price, leverage=leverage,
            position_qty_after=after, master_equity=equity, raw=fill.raw, **card,
        ),
    )


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
                    await alert_admin(f"Position drift on {key[0]} {key[1]}: the bot thought {mine}, BingX says "
                                      f"{real}. The ledger was corrected, but followers may be out of step "
                                      "(a trade was missed or changed outside the stream).", key=f"drift:{key}")
                    ledger.seed(*key, real)
        except Exception:
            log.exception("ledger resync failed")


async def watchdog_loop(check_every: float = 15, down_after: float = 45) -> None:
    """Alert when the stream is disconnected, or connected but silent, for too long."""
    down_since: float | None = None
    alerted = False
    while True:
        await asyncio.sleep(check_every)
        now = time.monotonic()
        silence = settings.STREAM_SILENCE_ALERT_SECONDS
        silent = bool(silence) and now - health["last_frame"] > silence
        if not health["connected"] or silent:
            down_since = down_since or now
            if now - down_since >= down_after and not alerted:
                alerted = True
                why = "disconnected" if not health["connected"] else f"silent for {int(now - health['last_frame'])}s"
                await alert_admin(f"Master stream is {why}: your brother's trades are NOT being copied until it "
                                  "recovers, and trades made meanwhile will not be replayed.", key="stream", cooldown=900)
        else:
            if alerted:
                await alert_admin("Master stream recovered. Trades made while it was down were NOT copied; "
                                  "check for out-of-step followers.", key="stream_ok", cooldown=0, icon="✅")
            down_since, alerted = None, False
        await beat("listener", "connected" if health["connected"] else "stream down")


async def run_listener() -> None:
    async with BingXClient(settings.MASTER_API_KEY, settings.MASTER_API_SECRET,
                           base_url=settings.BINGX_BASE_URL) as master:
        try:
            await seed_ledger(master)
        except Exception:
            log.exception("could not seed the ledger; the periodic resync will retry")
        tasks = [asyncio.create_task(resync_loop(master)), asyncio.create_task(watchdog_loop())]
        try:
            async for msg in stream_messages(master, _on_stream_event):
                if settings.LOG_RAW_STREAM:
                    log.info("RAW %s", msg)
                fill = parse_fill_event(msg)
                if fill is None:
                    continue
                try:
                    await record_fill(master, fill)  # sequential on purpose: fills are handled in order
                except Exception as exc:
                    await alert_admin(f"FAILED to record master fill {fill.order_id} ({fill.symbol} {fill.side}/"
                                      f"{fill.position_side} {fill.quantity}): {type(exc).__name__}. Followers did "
                                      "NOT copy it.", key=f"fill:{fill.order_id}")
                    log.exception("FAILED to record master fill %s", fill.order_id)
        finally:
            for task in tasks:
                task.cancel()