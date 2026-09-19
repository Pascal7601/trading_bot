"""Protective background process, independent of the master:
  * rolling 24h drawdown -> pause the follower (optionally close everything) and alert them
  * per-follower stop-loss on each open position (ROI-based)

This is a POLLING guard (every POLL_SECONDS): fast markets can gap past a stop between polls, and it
stops working if this server is down. Exchange-native stop orders are the stronger second layer (see README).
"""
import asyncio
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db.models import Max
from django.utils import timezone

from engine import risk
from engine.pnl import roi_pct
from exchange.bingx import BingXClient, Position

from ..models import EquitySnapshot, Follower, GuardEvent
from .notify import notify

log = logging.getLogger(__name__)

POLL_SECONDS = 10
SNAPSHOT_EVERY = 60          # seconds between stored equity points per follower
KEEP_SNAPSHOTS_DAYS = 8
STOP_COOLDOWN = 45           # don't re-send a stop-out for the same position within this window

_last_snapshot: dict[int, float] = {}
_cooldown: dict[tuple, float] = {}


async def _price(cache: dict, symbol: str):
    if symbol not in cache:
        async with BingXClient(base_url=settings.BINGX_BASE_URL) as c:
            cache[symbol] = await c.get_price(symbol)
    return cache[symbol]


async def _snapshot(follower: Follower, equity) -> None:
    if time.monotonic() - _last_snapshot.get(follower.id, 0) >= SNAPSHOT_EVERY:
        _last_snapshot[follower.id] = time.monotonic()
        await EquitySnapshot.objects.acreate(follower=follower, equity=equity)


async def _peak(follower: Follower, current):
    since = timezone.now() - timedelta(hours=24)
    if follower.guard_baseline_at and follower.guard_baseline_at > since:
        since = follower.guard_baseline_at  # after /resume, old losses no longer count
    agg = await EquitySnapshot.objects.filter(follower=follower, created_at__gte=since).aaggregate(top=Max("equity"))
    return max(agg["top"] or current, current)


async def _close(client: BingXClient, follower: Follower, pos: Position, tag: str, i: int = 0) -> None:
    await client.close_market(symbol=pos.symbol, position_side=pos.position_side, quantity=pos.qty,
                              client_order_id=f"g{follower.id}{tag}{int(time.time())}{i}")


async def _trip_daily_loss(client, follower: Follower, positions: list[Position], drawdown) -> None:
    follower.is_active = False
    await follower.asave(update_fields=["is_active"])  # pause FIRST: no new copies while we act
    closed = 0
    if follower.flatten_on_max_loss:
        for i, pos in enumerate(positions):
            try:
                await _close(client, follower, pos, "d", i)
                closed += 1
            except Exception:
                log.exception("flatten failed for follower %s %s", follower.id, pos.symbol)
    detail = f"drawdown {drawdown:.2f}% reached the {follower.max_daily_loss_pct.normalize():f}% limit; closed {closed}/{len(positions)} positions"
    await GuardEvent.objects.acreate(follower=follower, kind="max_daily_loss", detail=detail)
    tail = ("Your open positions were closed." if follower.flatten_on_max_loss and closed == len(positions)
            else "Your open positions were NOT all closed: please review them on BingX.")
    await notify(follower.telegram_id, f"🛑 Daily loss limit hit ({drawdown:.1f}% below your 24h peak). "
                                       f"Copying is paused. {tail}\nUse /settings to resume when you're ready.")


async def _check_stop(client, follower: Follower, limits, pos: Position, prices: dict) -> None:
    key = (follower.id, pos.symbol, pos.position_side)
    if _cooldown.get(key, 0) > time.monotonic() or not pos.leverage or pos.avg_price <= 0:
        return
    roi = roi_pct(pos.position_side, pos.leverage, pos.avg_price, await _price(prices, pos.symbol))
    if not risk.stop_loss_hit(limits, roi):
        return
    _cooldown[key] = time.monotonic() + STOP_COOLDOWN
    await _close(client, follower, pos, "s")
    await GuardEvent.objects.acreate(follower=follower, kind="stop_loss",
                                     detail=f"{pos.symbol} {pos.position_side} closed at {roi:.1f}% ROI")
    await notify(follower.telegram_id, f"🛡️ Stop-loss: closed {pos.symbol} {pos.position_side} at {roi:.1f}% ROI.")


async def _guard_one(follower: Follower, prices: dict, sem: asyncio.Semaphore) -> None:
    limits = follower.risk_limits()
    async with sem:
        try:
            api_key, api_secret = follower.credential.get_keys()
            async with BingXClient(api_key, api_secret, base_url=settings.BINGX_BASE_URL,
                                   dry_run=settings.DRY_RUN) as client:
                bal = await client.get_balance()
                await _snapshot(follower, bal.equity)
                positions = await client.get_positions()
                if follower.is_active and limits.max_daily_loss_pct is not None:
                    peak = await _peak(follower, bal.equity)
                    if risk.daily_loss_hit(limits, peak, bal.equity):
                        await _trip_daily_loss(client, follower, positions, risk.drawdown_pct(peak, bal.equity))
                        return
                if limits.stop_loss_roi_pct is not None:  # also for paused followers: their positions are still open
                    for pos in positions:
                        await _check_stop(client, follower, limits, pos, prices)
        except Exception:
            log.exception("guardian failed for follower %s", follower.id)


async def guard_cycle() -> None:
    followers = [f async for f in Follower.objects.filter(
        is_banned=False, credential__isnull=False).select_related("credential")]
    prices: dict = {}
    sem = asyncio.Semaphore(settings.MAX_CONCURRENCY)
    await asyncio.gather(*(_guard_one(f, prices, sem) for f in followers))


async def run_guardian() -> None:
    last_prune = 0.0
    while True:
        started = time.monotonic()
        try:
            await guard_cycle()
            if started - last_prune > 3600:
                last_prune = started
                cutoff = timezone.now() - timedelta(days=KEEP_SNAPSHOTS_DAYS)
                await EquitySnapshot.objects.filter(created_at__lt=cutoff).adelete()
        except Exception:
            log.exception("guardian cycle crashed")
        await asyncio.sleep(max(1.0, POLL_SECONDS - (time.monotonic() - started)))