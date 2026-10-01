"""Bot that trades the sweep -> MSS -> retest framework from :mod:`vestbot.smc`.

Trade management:
* TP1 hit  -> close 50%, stop to break-even.
* TP2 hit  -> close half of the rest, stop to TP1.
* TP3 hit  -> close half of the rest (runner keeps going) or everything if no runner target.
* Opposite 1M MSS (structure reclaimed): before TP1 -> cut half once; after that -> exit the rest.
* Opposite 5M MSS, stop-loss or the runner target -> close everything.
No adding to winners, no chasing: one entry per setup.

Risk guards: at most ``BOT_MAX_TRADES_PER_DAY`` entries and stop for the day once the
realized loss reaches ``BOT_MAX_DAILY_LOSS_R``. Days follow the CME session (22:00 UTC).
The open trade (SL/TP stage) is saved to ``BOT_STATE_FILE`` so a restart keeps managing it.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    risk: float = 0.0  # initial |entry - SL|, for R accounting
    size: float = 0.0  # initial size
    pnl_r: float = 0.0  # realized so far, in R

    @classmethod
    def from_json(cls, d: dict) -> "Trade":
        d = dict(d)
        for k in ("setup_key", "reclaim_key"):
            d[k] = tuple(d[k]) if d.get(k) is not None else None
        return cls(**d)


class SMCBot(Bot):
    def __init__(self, cfg, client=None):
        super().__init__(cfg, client)
        self.trade: Trade | None = None
        self.last_setup: tuple | None = None
        self.last_decision: str | None = None
        self.day: str | None = None
        self.day_r = 0.0
        self.day_trades = 0
        self.load_state()

    # ----------------------------------------------------------------- state
    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def session_day(self) -> str:
        return (self.now() + timedelta(hours=2)).date().isoformat()  # CME day starts 22:00 UTC

    def load_state(self) -> None:
        path = self.cfg.state_file
        if not path or not Path(path).exists():
            return
        d = json.loads(Path(path).read_text())
        self.trade = Trade.from_json(d["trade"]) if d.get("trade") else None
        self.last_setup = tuple(d["last_setup"]) if d.get("last_setup") else None
        self.day, self.day_r, self.day_trades = d.get("day"), d.get("day_r", 0.0), d.get("day_trades", 0)
        log.info("Loaded state: trade=%s day=%s R=%.2f trades=%s", self.trade, self.day,
                 self.day_r, self.day_trades)

    def save_state(self) -> None:
        if not self.cfg.state_file:
            return
        d = {"trade": asdict(self.trade) if self.trade else None, "last_setup": self.last_setup,
             "day": self.day, "day_r": self.day_r, "day_trades": self.day_trades}
        tmp = Path(self.cfg.state_file).with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2))
        tmp.replace(self.cfg.state_file)

    def start(self) -> None:
        super().start()
        if self.trade and not self.cfg.dry_run and self.position is None:
            log.warning("Saved trade %s has no position on the exchange — dropping it", self.trade)
            self.trade = None
            self.save_state()
        if self.trade:
            self.position = None  # the saved trade is the bot's own position

    def _roll_day(self) -> None:
        day = self.session_day()
        if day != self.day:
            self.day, self.day_r, self.day_trades = day, 0.0, 0

    def risk_block(self) -> str | None:
        if self.day_trades >= self.cfg.max_trades_per_day:
            return f"đã đủ {self.cfg.max_trades_per_day} lệnh hôm nay"
        if self.day_r <= -self.cfg.max_daily_loss_r:
            return f"đã lỗ {self.day_r:.2f}R hôm nay (giới hạn {self.cfg.max_daily_loss_r}R)"
        return None

    # ------------------------------------------------------------------ data
    def fetch(self) -> tuple[dict, list | None]:
        data = {tf: parse_candles(self.client.klines(self.cfg.symbol, tf, limit=KLINE_LIMITS[tf]))
                for tf in TIMEFRAMES}
        confirm = None
        if self.cfg.confirm_symbol:
            confirm = parse_candles(self.client.klines(self.cfg.confirm_symbol, "1m", limit=60))
        return data, confirm

    def tick(self) -> None:
        self._roll_day()
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
            block = self.risk_block()
            if block:
                log.info("Bỏ qua tín hiệu %s: %s", rep.decision, block)
                return
            self.enter(rep)

    # ----------------------------------------------------------------- orders
    def _fmt(self, size: float) -> str:
        q = 10 ** self.cfg.size_decimals
        return f"{math.floor(size * q) / q:.{self.cfg.size_decimals}f}"

    def position_size(self, rep: Report) -> float:
        """Fixed BOT_ORDER_SIZE, or size so that hitting SL loses BOT_RISK_USD (linear perps:
        P&L = size x price move), capped by BOT_MAX_NOTIONAL_USD."""
        if not self.cfg.risk_usd:
            return float(self.cfg.order_size)
        size = self.cfg.risk_usd / abs(rep.price - rep.sl)
        if self.cfg.max_notional_usd:
            size = min(size, self.cfg.max_notional_usd / rep.price)
        return float(self._fmt(size))

    def enter(self, rep: Report) -> None:
        m1 = rep.mss1
        key = (rep.direction, m1.mss_level, m1.sweep_price) if m1 else None
        if key is None or key == self.last_setup:
            return  # already traded this exact setup
        self.last_setup = key
        size = self.position_size(rep)
        if size <= 0:
            log.warning("Khối lượng tính ra = 0 (rủi ro quá nhỏ so với SL) — bỏ qua lệnh")
            return
        log.info("ENTRY %s %s @ %.2f SL %.2f (1R = $%.2f) targets %s", rep.direction,
                 self._fmt(size), rep.price, rep.sl, size * abs(rep.price - rep.sl), rep.targets)
        self._market(is_buy=rep.direction == "LONG", size=self._fmt(size), price=rep.price,
                     reduce_only=False)
        self.trade = Trade(rep.direction, rep.price, rep.sl, list(rep.targets), size, key,
                           risk=abs(rep.price - rep.sl), size=size)
        self.day_trades += 1
        self.save_state()

    def _reduce(self, fraction: float, price: float, reason: str) -> None:
        t = self.trade
        assert t is not None
        qty = float(self._fmt(t.remaining * fraction))
        if fraction >= 1 or qty <= 0 or float(self._fmt(t.remaining - qty)) <= 0:
            qty = t.remaining
        log.info("EXIT %s %s (%s)", t.side, self._fmt(qty), reason)
        self._market(is_buy=t.side == "SHORT", size=self._fmt(qty), price=price, reduce_only=True)
        sign = 1 if t.side == "LONG" else -1
        r = sign * (price - t.entry) * qty / (t.risk * t.size) if t.risk and t.size else 0.0
        t.pnl_r += r
        self.day_r += r
        t.remaining -= qty
        if t.remaining <= 1e-12:
            log.info("Trade closed: %+.2fR (hôm nay %+.2fR)", t.pnl_r, self.day_r)
            self.trade = None
        self.save_state()

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
                self.save_state()
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
            self.save_state()
