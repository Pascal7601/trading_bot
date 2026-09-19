"""BingX request signing (HMAC-SHA256 over the query string). Pure functions."""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlencode


def build_query(params: dict[str, object], timestamp_ms: int) -> str:
    """Sorted, urlencoded query string including the timestamp. This exact string is signed AND sent."""
    items = {k: str(v) for k, v in params.items() if v is not None}
    items["timestamp"] = str(timestamp_ms)
    return urlencode(sorted(items.items()))


def sign(query: str, secret: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()