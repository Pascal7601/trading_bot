"""Independent watchdog: alerts the admin when a process dies, trades get stuck, or copies start failing.

Run it as its own process. Set HEALTHCHECK_URL (e.g. a healthchecks.io check) so that YOU also get told
if the monitor itself, or the whole server, goes down.
"""
import asyncio
import logging
import time
from datetime import timedelta

import httpx
from django.conf import settings
from django.utils import timezone

from engine.health import failure_spike, stale_processes

from ..models import CopyOrder, Heartbeat, MasterEvent, SystemState
from .alerts import alert_admin

log = logging.getLogger(__name__)

EXPECTED = ("listener", "executor", "guardian", "bot")
CHECK_EVERY = 30          # seconds
HEARTBEAT_MAX_AGE = 60    # seconds of silence before a process is reported
GRACE_SECONDS = 120       # don't report missing heartbeats right after the monitor itself starts
WINDOW = timedelta(minutes=15)


async def check_once(grace: bool) -> None:
    now = timezone.now()

    if not grace:
        ages = {h.name: (now - h.beat_at).total_seconds() async for h in Heartbeat.objects.all()}
        for problem in stale_processes(EXPECTED, ages, HEARTBEAT_MAX_AGE):
            await alert_admin(problem.text, key=f"hb:{problem.key}", cooldown=900)

    stuck = await MasterEvent.objects.filter(
        status__in=[MasterEvent.Status.PENDING, MasterEvent.Status.PROCESSING],
        created_at__lt=now - timedelta(seconds=90)).acount()
    if stuck:
        await alert_admin(f"{stuck} master trade(s) have been waiting for more than 90 seconds. "
                          "The executor may be stuck or down.", key="stuck", cooldown=900)

    recent = CopyOrder.objects.filter(created_at__gte=now - WINDOW)
    unknown = await recent.filter(status=CopyOrder.Status.UNKNOWN).acount()
    failed = await recent.filter(status=CopyOrder.Status.FAILED).acount()
    filled = await recent.filter(status=CopyOrder.Status.FILLED).acount()
    if unknown:
        await alert_admin(f"{unknown} copy order(s) in the last 15 minutes ended UNKNOWN (timeouts). Those followers "
                          "may hold positions the bot doesn't know about: admin > Copy orders > status 'unknown'.",
                          key="unknown", cooldown=900)
    if failure_spike(filled + failed + unknown, failed + unknown):
        await alert_admin(f"{failed + unknown} of the last {filled + failed + unknown} copy attempts failed. "
                          "Check the BingX status, your server IP and the executor logs.", key="spike", cooldown=900)

    if not await SystemState.aenabled():
        await alert_admin("The global kill switch is OFF: nothing is being copied.", key="killswitch",
                          cooldown=3600, icon="⚠️")

    if settings.HEALTHCHECK_URL:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.get(settings.HEALTHCHECK_URL)
        except Exception:
            log.warning("healthcheck ping failed", exc_info=True)


async def run_monitor() -> None:
    started = time.monotonic()
    while True:
        try:
            await check_once(grace=time.monotonic() - started < GRACE_SECONDS)
        except Exception:
            log.exception("monitor cycle failed")
        await asyncio.sleep(CHECK_EVERY)