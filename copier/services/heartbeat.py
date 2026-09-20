import logging
import time

from django.utils import timezone

from ..models import Heartbeat

log = logging.getLogger(__name__)
_last: dict[str, tuple[float, str]] = {}


async def beat(name: str, detail: str = "", every: float = 10.0) -> None:
    """Record 'this process is alive' (at most once per `every` seconds unless the detail changes)."""
    now = time.monotonic()
    previous = _last.get(name)
    if previous and now - previous[0] < every and previous[1] == detail:
        return
    _last[name] = (now, detail)
    try:
        await Heartbeat.objects.aupdate_or_create(name=name, defaults={"beat_at": timezone.now(), "detail": detail[:200]})
    except Exception:
        log.warning("heartbeat write failed", exc_info=True)