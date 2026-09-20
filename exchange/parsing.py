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
from engine.keycheck import KeyPermissions


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



# ---- API key permissions / position mode (shapes are NOT verified: see `manage.py check_key`) ----------
def _flag(v) -> bool | None:
    """Interpret an exchange flag. Only unambiguous values count; anything else is 'unknown'."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v) if v in (0, 1) else None
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "1", "yes", "on", "enabled"):
            return True
        if t in ("false", "0", "no", "off", "disabled"):
            return False
    return None


def _verdict(values: list[bool]) -> bool | None:
    return True if any(values) else (False if values else None)


def _scan(node, withdraw: list[bool], trade: list[bool], ip: list[bool]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            k = str(key).lower()
            if isinstance(value, (dict, list)):
                _scan(value, withdraw, trade, ip)
                continue
            flag = _flag(value)
            if flag is None:
                continue
            if "withdraw" in k:
                withdraw.append(flag)
            elif "ip" in k and ("restrict" in k or "whitelist" in k):
                ip.append(flag)
            elif any(w in k for w in ("future", "swap", "perpetual", "contract")) or k in ("trade", "trading", "cantrade"):
                trade.append(flag)
    elif isinstance(node, list):
        names = [x for x in node if isinstance(x, str)]
        if names and len(names) == len(node):  # a list of permission NAMES: everything granted is listed
            lowered = [n.lower() for n in names]
            withdraw.append(any("withdraw" in n for n in lowered))
            if any(w in n for n in lowered for w in ("future", "swap", "perpetual")):
                trade.append(True)
        else:
            for item in node:
                _scan(item, withdraw, trade, ip)


def parse_key_permissions(*payloads) -> KeyPermissions:
    """Tolerant parser: unknown shapes give None (= unverified), never a guess."""
    withdraw: list[bool] = []
    trade: list[bool] = []
    ip: list[bool] = []
    for payload in payloads:
        _scan(payload, withdraw, trade, ip)
    return KeyPermissions(can_withdraw=_verdict(withdraw), can_trade=_verdict(trade), ip_restricted=_verdict(ip),
                          raw=tuple(payloads))


def parse_position_mode(data) -> bool | None:
    """True = Hedge (two-way), False = One-way, None = unknown."""
    if not isinstance(data, dict):
        return None
    for key in ("dualSidePosition", "dualSide", "hedgeMode", "positionMode"):
        if key in data:
            v = data[key]
            if isinstance(v, str) and v.strip().lower() in ("hedge", "dual", "both", "two-way"):
                return True
            if isinstance(v, str) and v.strip().lower() in ("one-way", "oneway", "single"):
                return False
            return _flag(v)
    return parse_position_mode(data["data"]) if "data" in data else None