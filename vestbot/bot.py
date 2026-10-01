"""Main trading loop."""
from __future__ import annotations

import logging
import time
from typing import Any

from .client import VestAPIError, VestClient
from .config import Config
from .strategy import (
    Position, crossover_signal, exit_reason, last_closed_open_time, parse_closes, slippage_price,
)

log = logging.getLogger("vestbot")


def _find_position(account: Any, symbol: str) -> Position | None:
    """Best-effort parse of the open position for ``symbol`` from GET /account."""
    positions = account.get("positions", []) if isinstance(account, dict) else []
    for p in positions:
        if p.get("symbol") != symbol:
            continue
        size = float(p.get("size", 0) or 0)
        if size == 0:
            continue
        is_long = p.get("isLong")
        if is_long is None:
            is_long = size > 0
        return Position(
            side="LONG" if is_long else "SHORT",
            size=abs(size),
            entry_price=float(p.get("entryPrice", 0) or 0),
        )
    return None


class Bot:
    def __init__(self, cfg: Config, client: VestClient | None = None):
        self.cfg = cfg
        self.client = client or VestClient(
            cfg.rest_url,
            api_key=cfg.api_key,
            account_group=cfg.account_group,
            signing_private_key=cfg.signing_private_key,
        )
        self.position: Position | None = None
        self.last_signal_candle: object = None

    # ----------------------------------------------------------------- setup
    def start(self) -> None:
        mode = "DRY-RUN (no real orders)" if self.cfg.dry_run else "LIVE"
        log.info("Starting bot on %s %s [%s] mode=%s", self.cfg.env, self.cfg.symbol,
                 self.cfg.interval, mode)
        if not self.cfg.dry_run:
            self.cfg.require_credentials()
            try:
                self.client.set_leverage(self.cfg.symbol, self.cfg.leverage)
                log.info("Leverage set to %sx", self.cfg.leverage)
            except VestAPIError as e:
                log.warning("Could not set leverage: %s", e)
            self.sync_position()

    def sync_position(self) -> None:
        try:
            self.position = _find_position(self.client.account(), self.cfg.symbol)
            log.info("Current position: %s", self.position)
        except VestAPIError as e:
            log.warning("Could not read account: %s", e)

    # ------------------------------------------------------------------ loop
    def run_forever(self) -> None:
        self.start()
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                raise
            except Exception:  # keep the bot alive on transient errors
                log.exception("Error in tick")
            time.sleep(self.cfg.poll_seconds)

    def tick(self) -> None:
        klines = self.client.klines(self.cfg.symbol, self.cfg.interval,
                                    limit=self.cfg.slow_ema * 5)
        closes = parse_closes(klines)
        if not closes:
            log.warning("No kline data")
            return
        price = closes[-1]
        signal = crossover_signal(closes, self.cfg.fast_ema, self.cfg.slow_ema)
        candle = last_closed_open_time(klines)
        if signal != "HOLD" and candle is not None and candle == self.last_signal_candle:
            signal = "HOLD"  # already acted on this candle's crossover
        log.info("price=%.4f signal=%s position=%s", price, signal, self.position)

        if self.position:
            reason = exit_reason(self.position, price, self.cfg.stop_loss_pct,
                                 self.cfg.take_profit_pct)
            opposite = (signal == "LONG" and self.position.side == "SHORT") or (
                signal == "SHORT" and self.position.side == "LONG")
            if reason or opposite:
                self.close_position(price, reason or "opposite signal")

        if self.position is None and signal in ("LONG", "SHORT"):
            self.open_position(signal, price)
        if signal != "HOLD":
            self.last_signal_candle = candle

    # --------------------------------------------------------------- orders
    def _market(self, *, is_buy: bool, size: str, price: float, reduce_only: bool) -> None:
        limit = slippage_price(price, is_buy, self.cfg.max_slippage_pct)
        side = "BUY" if is_buy else "SELL"
        if self.cfg.dry_run:
            log.info("[DRY-RUN] %s %s %s @ market (limit %s) reduceOnly=%s",
                     side, size, self.cfg.symbol, limit, reduce_only)
            return
        resp = self.client.place_order(symbol=self.cfg.symbol, is_buy=is_buy, size=size,
                                       order_type="MARKET", limit_price=limit,
                                       reduce_only=reduce_only)
        log.info("%s order sent: %s", side, resp)

    def open_position(self, side: str, price: float) -> None:
        self._market(is_buy=side == "LONG", size=self.cfg.order_size, price=price,
                     reduce_only=False)
        self.position = Position(side=side, size=float(self.cfg.order_size), entry_price=price)
        if not self.cfg.dry_run:
            self.sync_position()

    def close_position(self, price: float, reason: str) -> None:
        pos = self.position
        assert pos is not None
        log.info("Closing %s position: %s", pos.side, reason)
        size = f"{pos.size:.8f}".rstrip("0").rstrip(".")
        self._market(is_buy=pos.side == "SHORT", size=size, price=price, reduce_only=True)
        self.position = None
        if not self.cfg.dry_run:
            self.sync_position()
