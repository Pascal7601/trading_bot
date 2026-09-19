"""Parse BingX user-data-stream messages. Pure functions (no network).

NOTE: field names below follow BingX's documented ORDER_TRADE_UPDATE payload
(o.s symbol, o.S side, o.ps position side, o.X status, o.z filled qty, o.ap avg price, o.i order id).
VERIFY against real payloads captured from the master's stream (run the listener with LOG_RAW_STREAM=1)
before trusting this with real money.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class RawFill:
    order_id: str
    symbol: str
    side: str
    position_side: str
    quantity: Decimal
    avg_price: Decimal
    raw: dict


def decode_ws_message(data: bytes | str) -> str:
    """BingX pushes gzip-compressed frames; plain-text frames (e.g. 'Ping') are passed through."""
    if isinstance(data, bytes):
        try:
            return gzip.decompress(data).decode()
        except OSError:
            return data.decode()
    return data


def parse_fill_event(msg: dict) -> RawFill | None:
    """Return a RawFill for a fully filled order, else None. Partial fills are ignored in the MVP."""
    if msg.get("e") != "ORDER_TRADE_UPDATE":
        return None
    o = msg.get("o") or {}
    if o.get("X") != "FILLED":
        return None
    qty = Decimal(str(o.get("z") or o.get("q") or "0"))
    if qty <= 0:
        return None
    return RawFill(
        order_id=str(o.get("i", "")),
        symbol=str(o.get("s", "")).upper(),
        side=str(o.get("S", "")).upper(),
        position_side=str(o.get("ps", "")).upper(),
        quantity=qty,
        avg_price=Decimal(str(o.get("ap") or o.get("p") or "0")),
        raw=msg,
    )


def loads(text: str) -> dict | None:
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    return obj if isinstance(obj, dict) else None