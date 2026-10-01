"""Bot that trades the sweep -> MSS -> retest framework from :mod:`vestbot.smc`.

Trade management:
* TP1 hit  -> close 50%, stop to break-even.
* TP2 hit  -> close half of the rest, stop to TP1.
* TP3 hit  -> close half of the rest (runner keeps going) or everything if no runner target.
* Opposite 1M MSS (structure reclaimed): before TP1 -> cut half once; after that -> exit the rest.
* Opposite 5M MSS, stop-loss or the runner target -> close everything.
No adding to winners, no chasing: one entry per setup.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from .bot import Bot
from .smc import TIMEFRAMES, Report, analyze, parse_candles

log = logging.getLogger("vestbot.smc")

KLINE_LIMITS = {"1d": 60, "4h": 120, "1h": 120, "15m": 150, "5m": 150, "1m": 200}


@dataclass
class Trade:
    side: str
    entry: float
    sl: float
    targets: list[float | None]
    remaining: float
    setup_key: tuple
    stage: int = 0  # number of targets already hit
    trimmed: bool = False  # already cut half on a 1M reclaim
    reclaim_key: tuple | None = None  # last opposite 1M MSS acted on


class SMCBot(Bot):
    def __init__(self, cfg, client=None):
        super().__init__(cfg, client)
        self.trade: Trade | None = None
        self.last_setup: tuple | None = None
        self.last_decision: str | None = None

    # ------------------------------------------------------------------ data
    def fetch(self) -> tuple[dict, list | None]:
        data = {tf: parse_candles(self.client.klines(self.cfg.symbol, tf, limit=KLINE_LIMITS[tf]))
                for tf in TIMEFRAMES}
        confirm = None
        if self.cfg.confirm_symbol:
            confirm = parse_candles(self.client.klines(self.cfg.confirm_symbol, "1m", limit=60))
        return data, confirm

    def tick(self) -> None:
        data, confirm = self.fetch()
        rep = analyze(data, self.cfg.symbol, confirm, self.cfg.min_momentum)
        if rep.decision != self.last_decision:
            log.info("\n%s", rep.render())
            self.last_decision = rep.decision
        else:
            log.info("%s %s | %s", self.cfg.symbol, f"{rep.price:,.2f}", rep.decision)

        if self.trade:
            self.manage(rep)
            return
        if self.position is not None:
            # a position opened outside the bot: never stack on top of it
            log.warning("Đang có vị thế mở sẵn %s — bot không vào lệnh mới", self.position)
            if not self.cfg.dry_run:
                self.sync_position()
            return
        if rep.decision in ("LONG", "SHORT"):
            self.enter(rep)

    # ----------------------------------------------------------------- orders
    def _fmt(self, size: float) -> str:
        q = 10 ** self.cfg.size_decimals
        return f"{math.floor(size * q) / q:.{self.cfg.size_decimals}f}"

    def enter(self, rep: Report) -> None:
        m1 = rep.mss1
        key = (rep.direction, m1.mss_level, m1.sweep_price) if m1 else None
        if key is None or key == self.last_setup:
            return  # already traded this exact setup
        self.last_setup = key
        size = float(self.cfg.order_size)
        log.info("ENTRY %s %s @ %.2f SL %.2f targets %s", rep.direction, self.cfg.order_size,
                 rep.price, rep.sl, rep.targets)
        self._market(is_buy=rep.direction == "LONG", size=self._fmt(size), price=rep.price,
                     reduce_only=False)
        self.trade = Trade(rep.direction, rep.price, rep.sl, list(rep.targets), size, key)

    def _reduce(self, fraction: float, price: float, reason: str) -> None:
        t = self.trade
        assert t is not None
        qty = float(self._fmt(t.remaining * fraction))
        if fraction >= 1 or qty <= 0 or float(self._fmt(t.remaining - qty)) <= 0:
            qty = t.remaining
        log.info("EXIT %s %s (%s)", t.side, self._fmt(qty), reason)
        self._market(is_buy=t.side == "SHORT", size=self._fmt(qty), price=price, reduce_only=True)
        t.remaining -= qty
        if t.remaining <= 1e-12:
            self.trade = None

    def manage(self, rep: Report) -> None:
        t = self.trade
        assert t is not None
        price = rep.price
        sign = 1 if t.side == "LONG" else -1

        if sign * (price - t.sl) <= 0:
            self._reduce(1, price, f"SL {t.sl:,.2f}")
            return
        m5 = rep.mss5
        if m5 and m5.direction != t.side:
            self._reduce(1, price, f"5M structure đảo chiều (MSS {m5.mss_level:,.2f})")
            return
        m1 = rep.mss1
        if m1 and m1.direction != t.side and (m1.mss_level, m1.sweep_price) != t.reclaim_key:
            t.reclaim_key = (m1.mss_level, m1.sweep_price)
            if t.stage == 0 and not t.trimmed:
                t.trimmed = True
                self._reduce(0.5, price, f"1M reclaim ngược {m1.mss_level:,.2f} — giảm vị thế")
            else:
                self._reduce(1, price, f"1M reclaim ngược {m1.mss_level:,.2f} — thoát runner")
            return

        tp = t.targets[t.stage] if t.stage < len(t.targets) else None
        if tp is None or sign * (price - tp) < 0:
            return
        if t.stage == 3 or (t.stage == 2 and t.targets[3] is None):
            self._reduce(1, price, f"target {tp:,.2f}")
            return
        new_sl = t.entry if t.stage == 0 else t.targets[t.stage - 1]
        self._reduce(0.5, price, f"TP{t.stage + 1} {tp:,.2f}")
        if self.trade:
            t.sl = new_sl
            t.stage += 1
            log.info("Protect profit: SL -> %.2f", t.sl)
