"""Fans each MasterEvent out to all active followers.

Delivery semantics: AT-MOST-ONCE per (event, follower). A CopyOrder row is created before any order is
sent; if the process dies mid-flight the row stays PENDING/UNKNOWN and is never retried automatically,
because a duplicated order is worse than a missed one. Reconcile UNKNOWN orders by hand (or with a job).
"""
import asyncio
import logging
import time
from decimal import Decimal

import httpx
from django.conf import settings
from django.utils import timezone

from engine import risk
from engine.fills import close_fraction, is_open
from engine.sizing import compute_open_quantity, round_down
from engine.types import SizingMode
from exchange.bingx import BingXClient, BingXError

from ..models import CopyOrder, Follower, MasterEvent, SystemState
from .notify import notify
from .alerts import alert_admin
from .heartbeat import beat


log = logging.getLogger(__name__)
_rules = {"at": 0.0, "data": {}}


async def _symbol_rules(symbol: str):
    if time.monotonic() - _rules["at"] > 3600 or symbol not in _rules["data"]:
        async with BingXClient(base_url=settings.BINGX_BASE_URL) as c:
            _rules["data"] = await c.get_symbol_rules()
            _rules["at"] = time.monotonic()
    return _rules["data"].get(symbol)


async def _current_price(symbol: str) -> Decimal | None:
    """Market price used for every follower's slippage check (fetched once per event)."""
    for _ in range(2):
        try:
            async with BingXClient(base_url=settings.BINGX_BASE_URL) as c:
                return await c.get_price(symbol)
        except Exception:
            log.warning("price lookup failed for %s", symbol, exc_info=True)
    return None


def _order_id(order: dict) -> str:
    return str((order.get("order") or order).get("orderId", ""))


async def _open(client: BingXClient, copy: CopyOrder, event: MasterEvent, follower: Follower, rules, market_price):
    limits = follower.risk_limits()
    if reason := risk.check_open(limits, event.symbol):
        return CopyOrder.Status.SKIPPED, None, reason, ""
    if reason := risk.check_slippage(limits, event.position_side, event.price, market_price):
        return CopyOrder.Status.SKIPPED, None, reason, ""

    bal = await client.get_balance()
    sized = compute_open_quantity(
        mode=SizingMode(follower.sizing_mode), value=follower.sizing_value,
        master_qty=event.quantity, price=event.price, master_equity=event.master_equity,
        follower_equity=bal.equity, rules=rules, limits=limits,
    )
    if sized.quantity is None:
        return CopyOrder.Status.SKIPPED, None, sized.skip_reason, ""

    lev = risk.effective_leverage(event.leverage, limits)
    new_margin = sized.quantity * event.price / Decimal(lev or event.leverage or 1)
    if reason := risk.check_exposure(limits, bal.used_margin, new_margin, bal.equity):
        return CopyOrder.Status.SKIPPED, None, reason, ""

    if lev:
        await client.set_leverage(event.symbol, event.position_side, lev)
    order = await client.place_market_order(
        symbol=event.symbol, side=event.side, position_side=event.position_side,
        quantity=sized.quantity, client_order_id=copy.client_order_id)
    detail = f"opened {sized.quantity}"
    if market_price is not None:
        detail += f" (price drift {risk.adverse_slippage_pct(event.position_side, event.price, market_price):+.2f}%)"
    return CopyOrder.Status.FILLED, sized.quantity, detail, _order_id(order)


async def _close(client: BingXClient, copy: CopyOrder, event: MasterEvent, follower: Follower, rules, market_price):
    positions = await client.get_positions(event.symbol)
    pos = next((p for p in positions if p.position_side == event.position_side), None)
    if pos is None:
        return CopyOrder.Status.SKIPPED, None, "no matching position to close", ""
    if event.position_qty_after <= 0:
        qty = pos.qty  # master fully closed -> close everything
    else:
        frac = close_fraction(event.quantity, event.position_qty_after)  # share of the position he held BEFORE this fill
        qty = min(round_down(pos.qty * frac, rules.step_size), pos.qty)
    if qty <= Decimal(0):
        return CopyOrder.Status.SKIPPED, None, "close size rounds to zero", ""
    order = await client.close_market(symbol=event.symbol, position_side=event.position_side,
                                      quantity=qty, client_order_id=copy.client_order_id)
    return CopyOrder.Status.FILLED, qty, f"closed {qty}", _order_id(order)


async def _copy_one(event: MasterEvent, follower: Follower, opening: bool, rules, market_price,
                    sem: asyncio.Semaphore) -> None:
    async with sem:
        copy, created = await CopyOrder.objects.aget_or_create(
            master_event=event, follower=follower,
            defaults={"client_order_id": f"m{event.id}f{follower.id}"})
        if not created:
            return  # at-most-once: never re-send
        status, qty, detail, exch_id = CopyOrder.Status.FAILED, None, "", ""
        try:
            api_key, api_secret = follower.credential.get_keys()
            async with BingXClient(api_key, api_secret, base_url=settings.BINGX_BASE_URL,
                                   dry_run=settings.DRY_RUN) as client:
                step = _open if opening else _close
                status, qty, detail, exch_id = await step(client, copy, event, follower, rules, market_price)
        except httpx.TimeoutException:
            status, detail = CopyOrder.Status.UNKNOWN, "timeout: the order may or may not exist"
        except (BingXError, httpx.HTTPError) as e:
            status, detail = CopyOrder.Status.FAILED, str(e)[:500]
        except Exception:
            log.exception("copy failed for follower %s event %s", follower.id, event.id)
            status, detail = CopyOrder.Status.FAILED, "internal error"
        copy.status, copy.quantity, copy.detail, copy.exchange_order_id = status, qty, detail, exch_id
        await copy.asave()

         # --- PnL Card Generation Logic ---
        photo_bytes = None
        if not opening and status == CopyOrder.Status.FILLED:
            try:
                from .pnl_card import generate_pnl_card

                # Use the ledger-computed result fields (same source community.py's group card
                # uses) instead of event.raw / event.leverage, which are only populated for
                # OPENING fills and are meaningless here.
                entry = event.result_entry_price
                exit_p = event.result_exit_price or event.price
                lev = event.result_leverage

                if entry is None or lev is None:
                    # Without a real entry price and leverage the ROI would be a guess: skip
                    # the card entirely rather than render one with made-up numbers.
                    log.warning("skipping follower PnL card for event %s: result fields missing", event.id)
                else:
                    # Follower's OWN absolute PnL in USDT, using their own closed quantity.
                    if event.position_side.upper() == "LONG":
                        usdt_pnl = (exit_p - entry) * qty
                    else:
                        usdt_pnl = (entry - exit_p) * qty

                    photo_bytes = generate_pnl_card(
                        symbol=event.symbol,
                        side=event.position_side,
                        leverage=int(lev),
                        entry_price=entry,
                        exit_price=exit_p,
                        pnl_usdt=usdt_pnl,
                        image_format="PNG",
                        brand=f"@{follower.username}" if follower.username else "",
                    )
            except Exception as e:
                log.exception("Failed to generate PnL card for event %s: %s", event.id, e)

    label = f"{'[DRY RUN] ' if settings.DRY_RUN else ''}{event.symbol} {event.position_side} {'OPEN' if opening else 'CLOSE'}"
    text = {
        CopyOrder.Status.FILLED: f"✅ Copied {label}: {detail}",
        CopyOrder.Status.SKIPPED: f"⏭️ Skipped {label}: {detail}",
        CopyOrder.Status.FAILED: f"❌ Copy FAILED {label}: {detail}",
        CopyOrder.Status.UNKNOWN: f"❓ {label}: {detail}. Please check your BingX account.",
    }[status]
    await notify(follower.telegram_id, text, photo_bytes)


async def process_event(event: MasterEvent) -> None:
    event.status = MasterEvent.Status.PROCESSING
    await event.asave(update_fields=["status"])

    async def finish(status, why=""):
        event.status = status
        await event.asave(update_fields=["status"])
        if why:
            log.warning("event %s %s: %s", event.id, status, why)

    if not await SystemState.aenabled():
        return await finish(MasterEvent.Status.SKIPPED, "global kill switch is OFF")

    opening = is_open(event.side, event.position_side)
    age = (timezone.now() - event.created_at).total_seconds()
    if opening and age > settings.MAX_OPEN_EVENT_AGE_SECONDS:
        await alert_admin(f"Skipped a STALE open ({event.symbol} {event.position_side}, {age:.0f}s old): the executor "
                          "was down or backed up, so followers did not copy it.", key=f"stale:{event.id}")
        return await finish(MasterEvent.Status.SKIPPED, f"stale open ({age:.0f}s old), not copied")

    rules = await _symbol_rules(event.symbol)
    if rules is None:
        return await finish(MasterEvent.Status.SKIPPED, f"no contract rules for {event.symbol}")
    market_price = await _current_price(event.symbol) if opening else None

    followers = [f async for f in Follower.objects.filter(
        is_active=True, is_banned=False, credential__isnull=False,
        terms_acceptances__version=settings.TERMS_VERSION).select_related("credential")]
    sem = asyncio.Semaphore(settings.MAX_CONCURRENCY)
    await asyncio.gather(*(_copy_one(event, f, opening, rules, market_price, sem) for f in followers))
    await finish(MasterEvent.Status.DONE)
    log.info("event %s processed for %d followers", event.id, len(followers))


async def run_executor(poll_seconds: float = 0.3) -> None:
    # Single executor process: events are handled strictly in order (an open is always processed before its close).
    await MasterEvent.objects.filter(status=MasterEvent.Status.PROCESSING).aupdate(status=MasterEvent.Status.PENDING)
    while True:
        await beat("executor")
        event = await MasterEvent.objects.filter(status=MasterEvent.Status.PENDING).order_by("id").afirst()
        if event is None:
            await asyncio.sleep(poll_seconds)
            continue
        try:
            await process_event(event)
        except Exception as exc:
            log.exception("event %s crashed", event.id)
            await alert_admin(f"Executor crashed while copying master trade #{event.id} ({event.symbol}): "
                              f"{type(exc).__name__}. Some followers may NOT have copied it.", key=f"exec:{event.id}")