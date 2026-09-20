"""Master account event stream: listen key + WebSocket with keepalive and auto-reconnect."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable

from .bingx import WS_URL, BingXClient
from .parsing import decode_ws_message, loads

log = logging.getLogger(__name__)
KEEPALIVE_SECONDS = 25 * 60  # listen key is valid ~60 min; extend well before that


async def _keepalive(client: BingXClient, listen_key: str) -> None:
    while True:
        await asyncio.sleep(KEEPALIVE_SECONDS)
        try:
            await client.extend_listen_key(listen_key)
        except Exception:
            log.exception("listen key keepalive failed")


async def stream_messages(client: BingXClient, on_event: Callable[[str], None] | None = None) -> AsyncIterator[dict]:
    """Yield decoded JSON messages forever, reconnecting with backoff.
    `on_event` (optional, must be quick and non-blocking) is told 'connected', 'frame' (ANY frame, pings
    included) and 'disconnected', so a watchdog can tell a healthy stream from a dead or silent one."""
    import websockets  # imported lazily so pure modules stay importable without it

    emit = on_event or (lambda _event: None)
    backoff = 1
    while True:
        keepalive = None
        try:
            listen_key = await client.create_listen_key()
            keepalive = asyncio.create_task(_keepalive(client, listen_key))
            async with websockets.connect(f"{WS_URL}?listenKey={listen_key}", ping_interval=None) as ws:
                log.info("master stream connected")
                emit("connected")
                backoff = 1
                async for raw in ws:
                    emit("frame")
                    text = decode_ws_message(raw)
                    if text.strip() == "Ping":
                        await ws.send("Pong")
                        continue
                    msg = loads(text)
                    if msg is not None:
                        yield msg
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("master stream error; reconnecting in %ss", backoff)
        finally:
            emit("disconnected")
            if keepalive:
                keepalive.cancel()
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)