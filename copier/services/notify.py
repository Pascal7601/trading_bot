import logging

import httpx
from django.conf import settings

log = logging.getLogger(__name__)


async def notify(chat_id: int, text: str, photo_bytes: bytes | None = None) -> None:
    """Best-effort Telegram message. A failed notification must never affect trading."""
    if not settings.TELEGRAM_BOT_TOKEN:
        return
        
    base_url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            if photo_bytes:
                # Send as a photo with the text as the caption
                data = {"chat_id": chat_id, "caption": text}
                files = {"photo": ("pnl.png", photo_bytes, "image/png")}
                response = await client.post(f"{base_url}/sendPhoto", data=data, files=files)
            else:
                # Send as standard text
                payload = {"chat_id": chat_id, "text": text}
                response = await client.post(f"{base_url}/sendMessage", json=payload)
                
            response.raise_for_status()
    except Exception:
        log.warning("telegram notify failed for %s", chat_id, exc_info=True)