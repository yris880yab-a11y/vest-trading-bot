"""Minimal REST client for the Vest exchange (https://vestmarkets.com)."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .signing import sign_cancel, sign_order

log = logging.getLogger(__name__)


class VestAPIError(RuntimeError):
    def __init__(self, status: int, payload: Any):
        super().__init__(f"Vest API error {status}: {payload}")
        self.status = status
        self.payload = payload


def now_ms() -> int:
    return int(time.time() * 1000)


class VestClient:
    def __init__(
        self,
        rest_url: str,
        *,
        api_key: str | None = None,
        account_group: int | None = None,
        signing_private_key: str | None = None,
        timeout: float = 10.0,
    ):
        self.rest_url = rest_url.rstrip("/")
        self.api_key = api_key
        self.account_group = account_group
        self.signing_private_key = signing_private_key
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------------ http
    def _headers(self, private: bool) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.account_group is not None:
            headers["xrestservermm"] = f"restserver{self.account_group}"
        if private:
            if not self.api_key:
                raise RuntimeError("This endpoint needs VEST_API_KEY")
            headers["X-API-KEY"] = self.api_key
        return headers

    def _request(self, method: str, path: str, *, private: bool = False,
                 params: dict | None = None, body: dict | None = None) -> Any:
        url = f"{self.rest_url}{path}"
        resp = self.session.request(
            method, url, params=params, json=body,
            headers=self._headers(private), timeout=self.timeout,
        )
        try:
            payload = resp.json()
        except ValueError:
            payload = resp.text
        if resp.status_code >= 400:
            raise VestAPIError(resp.status_code, payload)
        return payload

    # ------------------------------------------------------------ public data
    def exchange_info(self, symbols: list[str] | None = None) -> Any:
        params = {"symbols": ",".join(symbols)} if symbols else None
        return self._request("GET", "/exchangeInfo", params=params)

    def ticker_latest(self, symbols: list[str] | None = None) -> Any:
        params = {"symbols": ",".join(symbols)} if symbols else None
        return self._request("GET", "/ticker/latest", params=params)

    def klines(self, symbol: str, interval: str = "1m", limit: int = 200) -> Any:
        return self._request("GET", "/klines",
                             params={"symbol": symbol, "interval": interval, "limit": limit})

    def depth(self, symbol: str, limit: int = 20) -> Any:
        return self._request("GET", "/depth", params={"symbol": symbol, "limit": limit})

    # -------------------------------------------------------------- account
    def register(self, *, signing_addr: str, primary_addr: str, signature: str,
                 expiry_ms: int, network_type: int = 0) -> Any:
        return self._request("POST", "/register", body={
            "signingAddr": signing_addr,
            "primaryAddr": primary_addr,
            "signature": signature,
            "expiryTime": expiry_ms,
            "networkType": network_type,
        })

    def account(self) -> Any:
        return self._request("GET", "/account", private=True, params={"time": now_ms()})

    def open_orders(self, symbol: str | None = None) -> Any:
        params: dict[str, Any] = {"status": "NEW"}
        if symbol:
            params["symbol"] = symbol
        return self._request("GET", "/orders", private=True, params=params)

    def set_leverage(self, symbol: str, leverage: int) -> Any:
        return self._request("POST", "/account/leverage", private=True,
                             body={"time": now_ms(), "symbol": symbol, "value": leverage})

    # --------------------------------------------------------------- trading
    def place_order(self, *, symbol: str, is_buy: bool, size: str, order_type: str = "MARKET",
                    limit_price: str, reduce_only: bool = False,
                    nonce: int | None = None) -> Any:
        """Place an order.

        For MARKET orders Vest still needs ``limit_price``; it acts as the worst
        price you accept (slippage protection).
        """
        if not self.signing_private_key:
            raise RuntimeError("This endpoint needs VEST_SIGNING_PRIVATE_KEY")
        t = now_ms()
        nonce = t if nonce is None else nonce
        order = {
            "time": t,
            "nonce": nonce,
            "symbol": symbol,
            "isBuy": is_buy,
            "size": size,
            "orderType": order_type,
            "limitPrice": limit_price,
            "reduceOnly": reduce_only,
        }
        signature = sign_order(
            time_ms=t, nonce=nonce, order_type=order_type, symbol=symbol, is_buy=is_buy,
            size=size, limit_price=limit_price, reduce_only=reduce_only,
            private_key=self.signing_private_key,
        )
        return self._request("POST", "/orders", private=True,
                             body={"order": order, "signature": signature,
                                   "recvWindow": 60000})

    def cancel_order(self, order_id: str) -> Any:
        if not self.signing_private_key:
            raise RuntimeError("This endpoint needs VEST_SIGNING_PRIVATE_KEY")
        t = now_ms()
        signature = sign_cancel(time_ms=t, nonce=t, order_id=order_id,
                                private_key=self.signing_private_key)
        return self._request("POST", "/orders/cancel", private=True, body={
            "order": {"time": t, "nonce": t, "id": order_id},
            "signature": signature,
            "recvWindow": 60000,
        })
