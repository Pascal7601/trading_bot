"""Async client for the BingX USDT-M perpetual swap REST API.

VERIFY before production: endpoint paths / parameter names / response fields against the current
BingX docs (https://bingx-api.github.io/docs/). Places to double check are marked  # VERIFY.
Use BingX's demo (VST) environment for first tests.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from decimal import Decimal

import httpx

from engine.stats import Income
from engine.types import SymbolRules

from .signing import build_query, sign
from engine.keycheck import KeyPermissions
from .parsing import parse_key_permissions, parse_position_mode


log = logging.getLogger(__name__)

BASE_URL = "https://open-api.bingx.com"
WS_URL = "wss://open-api-swap.bingx.com/swap-market"


class BingXError(Exception):
    def __init__(self, code: int | str, msg: str):
        super().__init__(f"BingX error {code}: {msg}")
        self.code, self.msg = code, msg


@dataclass(frozen=True)
class Position:
    symbol: str
    position_side: str
    qty: Decimal
    leverage: int | None
    avg_price: Decimal


@dataclass(frozen=True)
class Balance:
    equity: Decimal
    available: Decimal
    used_margin: Decimal


class BingXClient:
    def __init__(self, api_key: str | None = None, api_secret: str | None = None, *,
                 base_url: str = BASE_URL, timeout: float = 10.0, dry_run: bool = False):
        self.api_key, self.api_secret = api_key, api_secret
        self.dry_run = dry_run
        self._http = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.aclose()

    # ---- low level -------------------------------------------------------
    async def _request(self, method: str, path: str, params: dict | None = None, *, signed: bool = True):
        headers = {"X-BX-APIKEY": self.api_key} if self.api_key else {}
        if signed:
            if not (self.api_key and self.api_secret):
                raise BingXError("client", "credentials required for signed request")
            query = build_query(params or {}, int(time.time() * 1000))
            query += "&signature=" + sign(query, self.api_secret)
        else:
            query = build_query(params or {}, int(time.time() * 1000)) if params else ""
        resp = await self._http.request(method, f"{path}?{query}" if query else path, headers=headers)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("code", 0) != 0:
            raise BingXError(body.get("code"), body.get("msg", ""))
        return body.get("data", body) if isinstance(body, dict) else body

    # ---- account ---------------------------------------------------------
    async def get_balance(self) -> Balance:
        data = await self._request("GET", "/openApi/swap/v2/user/balance")  # VERIFY field names
        bal = data["balance"] if "balance" in data else data
        return Balance(
            equity=Decimal(str(bal["equity"])),
            available=Decimal(str(bal.get("availableMargin", "0"))),
            used_margin=Decimal(str(bal.get("usedMargin", "0"))),
        )

    async def get_equity(self) -> Decimal:
        return (await self.get_balance()).equity

    async def get_income(self, start_ms: int, end_ms: int, limit: int = 1000) -> list[Income]:
        """Realized PnL, fees and funding records (max `limit` rows; no paging in the MVP)."""
        data = await self._request("GET", "/openApi/swap/v2/user/income",  # VERIFY path + field names
                                   {"startTime": start_ms, "endTime": end_ms, "limit": limit})
        return [Income(kind=str(i.get("incomeType", "")).upper(), amount=Decimal(str(i.get("income", "0"))))
                for i in data or []]

    async def get_position_mode(self) -> bool | None:
        """True = Hedge (two-way), False = One-way, None = could not be determined."""
        try:
            data = await self._request("GET", "/openApi/swap/v1/positionSide/dual")
        except Exception:
            log.info("position mode lookup failed", exc_info=True)
            return None
        return parse_position_mode(data)

    async def get_key_permissions(self) -> KeyPermissions:
        """What this API key is allowed to do. Two BingX endpoints are merged; the response shape is
        unverified, so run `python manage.py check_key` once and adjust exchange/parsing.py if needed."""
        payloads = []
        for path in ("/openApi/v1/account/apiPermissions", "/openApi/v1/account/apiRestrictions"):
            try:
                payloads.append(await self._request("GET", path))
            except Exception:
                log.info("key permission lookup failed on %s", path, exc_info=True)
        return parse_key_permissions(*payloads)

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        params = {"symbol": symbol} if symbol else {}
        data = await self._request("GET", "/openApi/swap/v2/user/positions", params)  # VERIFY
        out = []
        for p in data or []:
            qty = Decimal(str(p.get("positionAmt", "0")))
            if qty == 0:
                continue
            lev = p.get("leverage")
            out.append(Position(
                symbol=str(p["symbol"]).upper(),
                position_side=str(p.get("positionSide", "")).upper(),
                qty=abs(qty),
                leverage=int(lev) if lev not in (None, "") else None,
                avg_price=Decimal(str(p.get("avgPrice", "0"))),
            ))
        return out

    # ---- trading ---------------------------------------------------------
    async def set_leverage(self, symbol: str, position_side: str, leverage: int) -> None:
        if self.dry_run:
            log.info("[DRY RUN] would set leverage %s %s x%s", symbol, position_side, leverage)
            return
        await self._request("POST", "/openApi/swap/v2/trade/leverage",  # VERIFY
                            {"symbol": symbol, "side": position_side, "leverage": leverage})

    async def place_market_order(self, *, symbol: str, side: str, position_side: str,
                                 quantity: Decimal, client_order_id: str) -> dict:
        if self.dry_run:
            log.info("[DRY RUN] would place MARKET %s %s/%s qty=%s id=%s",
                     symbol, side, position_side, quantity, client_order_id)
            return {"order": {"orderId": "DRY-RUN"}}
        return await self._request("POST", "/openApi/swap/v2/trade/order", {  # VERIFY (clientOrderId name)
            "symbol": symbol, "side": side, "positionSide": position_side,
            "type": "MARKET", "quantity": format(quantity, "f"), "clientOrderId": client_order_id,
        })

    async def close_market(self, *, symbol: str, position_side: str, quantity: Decimal,
                            client_order_id: str) -> dict:
        side = "SELL" if position_side.upper() == "LONG" else "BUY"
        return await self.place_market_order(symbol=symbol, side=side, position_side=position_side,
                                             quantity=quantity, client_order_id=client_order_id)

    # ---- market data (public) -------------------------------------------
    async def get_price(self, symbol: str) -> Decimal:
        data = await self._request("GET", "/openApi/swap/v2/quote/price", {"symbol": symbol}, signed=False)  # VERIFY
        if isinstance(data, list):
            data = data[0]
        return Decimal(str(data["price"]))

    async def get_symbol_rules(self) -> dict[str, SymbolRules]:
        data = await self._request("GET", "/openApi/swap/v2/quote/contracts", signed=False)  # VERIFY fields
        rules = {}
        for c in data or []:
            precision = int(c.get("quantityPrecision", 0))
            step = Decimal(1).scaleb(-precision)
            rules[str(c["symbol"]).upper()] = SymbolRules(
                step_size=step,
                min_qty=Decimal(str(c.get("tradeMinQuantity", step))),
                min_notional=Decimal(str(c.get("tradeMinUSDT", 0))),
            )
        return rules

    # ---- user data stream (listen key) ----------------------------------
    async def create_listen_key(self) -> str:
        data = await self._request("POST", "/openApi/user/auth/userDataStream", signed=False)
        return data["listenKey"]

    async def extend_listen_key(self, listen_key: str) -> None:
        await self._request("PUT", "/openApi/user/auth/userDataStream", {"listenKey": listen_key}, signed=False)