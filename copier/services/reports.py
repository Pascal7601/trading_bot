"""Weekly performance summaries, built from BingX's own income records (realized PnL, fees, funding)."""
import asyncio
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from engine.stats import format_report, summarize
from exchange.bingx import BingXClient

from ..models import ApiCredential, EquitySnapshot, Follower
from .notify import notify

log = logging.getLogger(__name__)


async def build_report(follower: Follower, days: int = 7) -> str:
    cred = await ApiCredential.objects.aget(follower=follower)  # explicit query: safe inside async code
    api_key, api_secret = cred.get_keys()
    end_ms = int(time.time() * 1000)
    async with BingXClient(api_key, api_secret, base_url=settings.BINGX_BASE_URL) as client:
        income = await client.get_income(end_ms - days * 86_400_000, end_ms)
        equity = (await client.get_balance()).equity
    first = await EquitySnapshot.objects.filter(
        follower=follower, created_at__gte=timezone.now() - timedelta(days=days)).order_by("created_at").afirst()
    start_equity = first.equity if first else equity - summarize(income, None).net
    return format_report(summarize(income, start_equity), days)


async def send_weekly_reports(days: int = 7) -> None:
    followers = [f async for f in Follower.objects.filter(is_banned=False, credential__isnull=False)]
    sem = asyncio.Semaphore(5)

    async def one(f: Follower) -> None:
        async with sem:
            try:
                await notify(f.telegram_id, await build_report(f, days))
            except Exception:
                log.exception("weekly report failed for follower %s", f.id)

    await asyncio.gather(*(one(f) for f in followers))
    log.info("weekly reports attempted for %d followers", len(followers))