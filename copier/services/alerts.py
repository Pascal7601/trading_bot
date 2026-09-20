import logging
import time

from django.conf import settings

from engine.health import Throttle

from .notify import notify

log = logging.getLogger(__name__)
_throttle = Throttle(600)


async def alert_admin(text: str, key: str | None = None, cooldown: float = 600, icon: str = "🚨") -> None:
    """Message the admin chat (ADMIN_CHAT_ID). With a `key`, the same alert is sent at most once per `cooldown`
    seconds so a persistent problem doesn't flood you. Always logged, even when no admin chat is configured."""
    (log.error if icon == "🚨" else log.info)("ADMIN ALERT: %s", text)
    if key is not None and not _throttle.allow(key, time.monotonic(), cooldown):
        return
    try:
        chat_id = int(settings.ADMIN_CHAT_ID)
    except (TypeError, ValueError):
        return  # ADMIN_CHAT_ID not configured
    await notify(chat_id, f"{icon} {text}"[:3500])