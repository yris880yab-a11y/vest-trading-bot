"""TopstepX (ProjectX Gateway API) client with the same interface the bots use for Vest.

Topstep rules that shape this client:
* Orders must come from your own computer: no VPS, VPN or remote server.
* Live Funded accounts may not trade through the API (Trading Combine and Express Funded only).
* Futures size is whole contracts; P&L = points x point value x contracts.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .client import VestAPIError

log = logging.getLogger(__name__)

# timeframe -> (unit, unitNumber, minutes per bar); units: 2 = minute, 3 = hour, 4 = day
TIMEFRAMES = {"1m": (2, 1, 1), "5m": (2, 5, 5), "15m": (2, 15, 15),
              "1h": (3, 1, 60), "4h": (3, 4, 240), "1d": (4, 1, 1440)}
LIMIT, MARKET, STOP, BUY, SELL = 1, 2, 4, 0, 1
TOKEN_TTL = 23 * 3600  # tokens last 24h; refresh a bit earlier


class TopstepAPIError(VestAPIError):
    pass


class TopstepClient:
    def __init__(self, base_url: str, username: str | None, api_key: str | None,
                 account: str | None = None, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.username, self.api_key, self.account_ref = username, api_key, account
        self.timeout = timeout
        self.session = requests.Session()
        self.token: str | None = None
        self.token_at = 0.0
        self.account_id: int | None = None
        self.contracts: dict[str, str] = {}  # symbol -> contractId

    # ------------------------------------------------------------------ http
    def _post(self, path: str, body: dict, auth: bool = True) -> dict:
        headers = {"Content-Type": "application/json"}
        if auth:
            self._ensure_token()
            headers["Authorization"] = f"Bearer {self.token}"
        resp = self.session.post(f"{self.base_url}{path}", json=body, headers=headers,
                                 timeout=self.timeout)
        try:
            data = resp.json()
        except ValueError:
            data = {"errorMessage": resp.text}
        if resp.status_code != 200 or not data.get("success", False) or data.get("errorCode", 0):
            raise TopstepAPIError(resp.status_code, data)
        return data

    def _ensure_token(self) -> None:
        if self.token and time.time() - self.token_at < TOKEN_TTL:
            return
        if not (self.username and self.api_key):
            raise RuntimeError("Thiếu TOPSTEP_USERNAME / TOPSTEP_API_KEY trong .env")
        data = self._post("/api/Auth/loginKey",
                          {"userName": self.username, "apiKey": self.api_key}, auth=False)
        self.token, self.token_at = data["token"], time.time()

    # -------------------------------------------------------------- lookups
    def accounts(self) -> list[dict]:
        return self._post("/api/Account/search", {"onlyActiveAccounts": True})["accounts"]

    def balance(self) -> float:
        """Real account balance, shared by every bot trading this account."""
        acc_id = self.resolve_account()
        for a in self.accounts():
            if int(a["id"]) == acc_id:
                return float(a["balance"])
        raise RuntimeError(f"Không thấy tài khoản id {acc_id}")

    def resolve_account(self) -> int:
        if self.account_id is not None:
            return self.account_id
        accs = self.accounts()
        if self.account_ref:
            match = [a for a in accs
                     if str(a.get("id")) == self.account_ref or a.get("name") == self.account_ref]
        else:
            match = accs if len(accs) == 1 else []
        if len(match) != 1:
            names = ", ".join(f"{a.get('name')} (id {a.get('id')})" for a in accs)
            raise RuntimeError(f"Đặt TOPSTEP_ACCOUNT = tên hoặc id một tài khoản: {names}")
        self.account_id = int(match[0]["id"])
        return self.account_id

    def contract_id(self, symbol: str) -> str:
        if symbol.startswith("CON."):
            return symbol
        if symbol not in self.contracts:
            found = self._post("/api/Contract/search", {"searchText": symbol, "live": False})["contracts"]
            active = [c for c in found if c.get("activeContract", True)
                      and str(c.get("name", "")).upper().startswith(symbol.upper())]
            if not active:
                raise RuntimeError(f"Không tìm thấy hợp đồng đang giao dịch cho {symbol!r}")
            self.contracts[symbol] = active[0]["id"]
            log.info("%s -> %s (%s)", symbol, active[0]["id"], active[0].get("description", ""))
        return self.contracts[symbol]

    # ------------------------------------------------------- bot interface
    def klines(self, symbol: str, interval: str = "1m", limit: int = 200) -> list[dict]:
        unit, number, minutes = TIMEFRAMES[interval]
        end = datetime.now(timezone.utc)
        # ask for 3x the span so weekends and the daily break still leave ``limit`` bars
        start = end - timedelta(minutes=minutes * limit * 3)
        fmt = "%Y-%m-%dT%H:%M:%S.000Z"
        bars = self._post("/api/History/retrieveBars", {
            "contractId": self.contract_id(symbol), "live": False,
            "startTime": start.strftime(fmt), "endTime": end.strftime(fmt),
            "unit": unit, "unitNumber": number, "limit": limit, "includePartialBar": True,
        })["bars"]
        bars = sorted(bars, key=lambda b: b["t"])[-limit:]
        return [{"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b.get("v", 0)}
                for b in bars]

    def place_order(self, *, symbol: str, is_buy: bool, size: str, order_type: str = "MARKET",
                    limit_price: str | None = None, reduce_only: bool = False,
                    nonce: int | None = None, brackets: tuple[int, int] | None = None) -> Any:
        """Market order. ``brackets`` = (SL ticks, TP ticks) away from the fill, both positive:
        attached as an OCO pair (needed when Auto-OCO brackets are on for the account)."""
        qty = int(float(size))
        account, contract = self.resolve_account(), self.contract_id(symbol)
        if reduce_only:
            return self._post("/api/Position/partialCloseContract",
                              {"accountId": account, "contractId": contract, "size": qty})
        body = {"accountId": account, "contractId": contract, "type": MARKET,
                "side": BUY if is_buy else SELL, "size": qty}
        if brackets:
            # ticks are signed by direction: a long's stop is below (-), its target above (+)
            sign = 1 if is_buy else -1
            sl_ticks, tp_ticks = (max(1, int(round(x))) for x in brackets)
            body["stopLossBracket"] = {"ticks": -sign * sl_ticks, "type": STOP}
            body["takeProfitBracket"] = {"ticks": sign * tp_ticks, "type": LIMIT}
        return self._post("/api/Order/place", body)

    def open_orders(self, symbol: str) -> list[dict]:
        cid = self.contract_id(symbol)
        orders = self._post("/api/Order/searchOpen", {"accountId": self.resolve_account()})["orders"]
        return [o for o in orders if o.get("contractId") == cid]

    def bracket_ids(self, symbol: str) -> tuple[int | None, int | None]:
        """(stop order id, take-profit order id) resting on this contract, if any."""
        stop = tp = None
        for o in self.open_orders(symbol):
            if o.get("type") == STOP and stop is None:
                stop = int(o["id"])
            elif o.get("type") == LIMIT and tp is None:
                tp = int(o["id"])
        return stop, tp

    def modify_order(self, order_id: int, *, size: str | None = None,
                     stop_price: float | None = None, limit_price: float | None = None) -> None:
        body: dict = {"accountId": self.resolve_account(), "orderId": order_id}
        if size is not None:
            body["size"] = int(float(size))
        if stop_price is not None:
            body["stopPrice"] = stop_price
        if limit_price is not None:
            body["limitPrice"] = limit_price
        self._post("/api/Order/modify", body)

    def cancel_all(self, symbol: str) -> None:
        for o in self.open_orders(symbol):
            self.cancel_order(int(o["id"]))

    def place_stop(self, *, symbol: str, is_buy: bool, size: str, stop_price: float) -> int:
        """Resting stop order on the exchange: protects the trade between polls."""
        data = self._post("/api/Order/place", {
            "accountId": self.resolve_account(), "contractId": self.contract_id(symbol),
            "type": STOP, "side": BUY if is_buy else SELL, "size": int(float(size)),
            "stopPrice": stop_price})
        return int(data["orderId"])

    def cancel_order(self, order_id: int) -> None:
        try:
            self._post("/api/Order/cancel", {"accountId": self.resolve_account(), "orderId": order_id})
        except TopstepAPIError as e:  # already filled or cancelled
            log.info("Cancel %s: %s", order_id, e)

    def position_size(self, symbol: str) -> int:
        cid = self.contract_id(symbol)
        positions = self._post("/api/Position/searchOpen",
                               {"accountId": self.resolve_account()})["positions"]
        return sum(int(p.get("size", 0)) for p in positions if p.get("contractId") == cid)

    def account(self) -> dict:
        """Open positions shaped like Vest's /account so the bot can read them."""
        positions = self._post("/api/Position/searchOpen",
                               {"accountId": self.resolve_account()})["positions"]
        by_id = {v: k for k, v in self.contracts.items()}
        return {"positions": [{"symbol": by_id.get(p["contractId"], p["contractId"]),
                               "isLong": p.get("type") == 1, "size": p.get("size", 0),
                               "entryPrice": p.get("averagePrice", 0)} for p in positions]}

    def set_leverage(self, symbol: str, leverage: int) -> None:
        return None  # futures margin is set by Topstep, not per order

    def exchange_info(self, symbols: list[str] | None = None) -> Any:
        return {"accounts": self.accounts(),
                "contracts": [self._post("/api/Contract/search", {"searchText": s, "live": False})
                              for s in (symbols or [])]}
