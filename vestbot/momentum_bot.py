"""Bot for the momentum scalp in :mod:`vestbot.momentum`.

Per trade: enter at market with the 5M push, rest a real stop on the exchange (when the
broker supports it), then
* half off at ``scalp_tp`` (10 points by default) and stop to break-even;
* trail the rest ``trail`` points behind the best price;
* close everything at the target, on a strong opposite 1M candle (momentum faded), or after
  ``max_hold_min`` minutes.
One entry per 5M candle. Daily limits, funded-account guards and the Topstep 3:10 PM flat
rule are shared with :class:`SMCBot`.
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo

from .momentum import MomoParams, MomoSignal, in_blackout, in_sessions, momentum_signal
from .smc import parse_candles
from .smc_bot import SMCBot, Trade

log = logging.getLogger("vestbot.momo")


class MomentumBot(SMCBot):
    def __init__(self, cfg, client=None):
        super().__init__(cfg, client)
        self.params = MomoParams.from_cfg(cfg)
        self.last_why: str | None = None

    # ------------------------------------------------------------------ helpers
    def _round(self, price: float) -> float:
        tick = self.params.tick
        return round(round(price / tick) * tick, 6)

    def _can_stop(self) -> bool:
        return not self.cfg.dry_run and hasattr(self.client, "place_stop")

    def _oco(self) -> bool:
        """TopstepX with Auto-OCO: the entry carries the stop and target, the bot edits them."""
        return (self._can_stop() and getattr(self.cfg, "topstep_oco", False)
                and hasattr(self.client, "bracket_ids"))

    def _attach_brackets(self) -> None:
        """Find the OCO legs the exchange created for the open trade."""
        t = self.trade
        try:
            t.stop_id, t.tp_id = self.client.bracket_ids(self.cfg.symbol)
        except Exception as e:
            log.warning("Không đọc được lệnh OCO: %s", e)
            return
        if t.stop_id is None:
            log.warning("Chưa thấy lệnh SL của OCO trên sàn — bot vẫn tự theo dõi SL")
        self.save_state()

    def _place_stop(self) -> None:
        t = self.trade
        if t is None or not self._can_stop():
            return
        if self._oco():
            if t.stop_id is None:
                self._attach_brackets()
            if t.stop_id is not None:
                try:  # move the OCO stop and keep both legs at the open size
                    self.client.modify_order(t.stop_id, size=self._fmt(t.remaining),
                                             stop_price=self._round(t.sl))
                    if t.tp_id is not None:
                        self.client.modify_order(t.tp_id, size=self._fmt(t.remaining))
                    self.save_state()
                    return
                except Exception as e:
                    log.warning("Không sửa được lệnh OCO (%s) — huỷ và đặt stop mới", e)
                    self._cancel_protection()
        if t.stop_id is not None:
            self.client.cancel_order(t.stop_id)
            t.stop_id = None
        try:
            t.stop_id = self.client.place_stop(symbol=self.cfg.symbol, is_buy=t.side == "SHORT",
                                               size=self._fmt(t.remaining),
                                               stop_price=self._round(t.sl))
        except Exception as e:  # the bot still watches the stop itself
            log.warning("Không đặt được stop trên sàn: %s", e)
        self.save_state()

    def _cancel_protection(self) -> None:
        t = self.trade
        if self._oco():
            try:
                self.client.cancel_all(self.cfg.symbol)
            except Exception as e:
                log.warning("Không huỷ được lệnh chờ: %s", e)
        elif t and t.stop_id is not None:
            self.client.cancel_order(t.stop_id)
        if t:
            t.stop_id = t.tp_id = None

    def _exit(self, fraction: float, price: float, reason: str) -> None:
        t = self.trade
        full = fraction >= 1 or float(self._fmt(t.remaining * fraction)) >= t.remaining
        if self._can_stop() and (full or not self._oco()):
            self._cancel_protection()  # never leave a stop that could open a new position
        elif self._oco() and t.stop_id is not None:
            # shrink the OCO legs first so they never exceed the position
            left = self._fmt(t.remaining - float(self._fmt(t.remaining * fraction)))
            for oid in (t.stop_id, t.tp_id):
                if oid is not None:
                    try:
                        self.client.modify_order(oid, size=left)
                    except Exception as e:
                        log.warning("Không giảm được khối lượng lệnh OCO %s: %s", oid, e)
        self._reduce(fraction, price, reason)
        if self.trade:  # partial exit: re-arm (or resize) the stop for what is left
            self._place_stop()

    # -------------------------------------------------------------------- loop
    def tick(self) -> None:
        self.sync_balance()
        self._roll_day()
        # the volume profile of the rejection entry needs MOMO_VP_LOOKBACK minutes of 1M bars
        n1 = 60
        if "rejection" in self.params.entry_mode:
            n1 = max(n1, int(self.params.vp_lookback) + 10)
        if self.params.active_vol_mult:
            n1 = max(n1, int(self.params.active_vol_base or self.params.vp_lookback) + 10)
        c1 = parse_candles(self.client.klines(self.cfg.symbol, "1m", limit=n1))
        c5 = parse_candles(self.client.klines(self.cfg.symbol, "5m", limit=60))
        c15 = parse_candles(self.client.klines(self.cfg.symbol, "15m", limit=60))
        price = c1[-1].c

        if self.in_flatten_window():
            if self.trade:
                self._exit(1, price, f"đóng trước {self.cfg.flatten_time_ct} giờ Chicago (luật Topstep)")
            return
        blackout = in_blackout(self.now().astimezone(ZoneInfo("America/New_York")),
                               self.params.blackout)
        if blackout and self.trade:
            self._exit(1, price, "giờ ra tin / mở cửa (MOMO_BLACKOUT)")
            return
        if self.trade:
            self.manage_momo(price, c1)
            return
        if self.position is not None:
            log.warning("Đang có vị thế mở sẵn %s — bot không vào lệnh mới", self.position)
            if not self.cfg.dry_run:
                self.sync_position()
            return

        if not in_sessions(self.now().hour, self.params.sessions) or blackout:
            return  # outside MOMO_SESSIONS, or inside a MOMO_BLACKOUT window
        sig, why = momentum_signal(c1, c5, c15, self.params)
        if sig is None:
            msg = "; ".join(why)
            if msg != self.last_why:
                log.info("%s %s | WAIT (%s)", self.cfg.symbol, f"{price:,.2f}", msg)
                self.last_why = msg
            return
        self.last_why = None
        base = (sig.direction, str(sig.candle_key))
        n = self.last_setup[2] if self.last_setup and tuple(self.last_setup[:2]) == base else 0
        if n >= self.params.max_per_candle:
            return  # entries per 5M candle used up
        key = (*base, n + 1)
        block = self.risk_block(sig)
        if block:
            log.info("Bỏ qua %s: %s", sig.direction, block)
            self.last_setup = key
            return
        self.enter_momo(sig, key)

    # ------------------------------------------------------------------- entry
    def enter_momo(self, sig: MomoSignal, key: tuple) -> None:
        self.last_setup = key
        size = self.position_size(sig)
        if size <= 0:
            log.warning("Khối lượng = 0 (rủi ro quá nhỏ so với SL %.2f điểm) — bỏ qua",
                        abs(sig.price - sig.sl))
            return
        log.info("\n%s\nENTRY %s %s (1R = $%.2f)", sig.render(self.cfg.symbol), sig.direction,
                 self._fmt(size), size * abs(sig.price - sig.sl) * self.cfg.point_value)
        self.notifier.send(sig.render(self.cfg.symbol) + f"\nKhối lượng: {self._fmt(size)}")
        is_buy = sig.direction == "LONG"
        if self._oco():
            tick = self.params.tick
            ticks = (abs(sig.price - sig.sl) / tick, abs(sig.tp - sig.price) / tick)
            resp = self.client.place_order(symbol=self.cfg.symbol, is_buy=is_buy,
                                           size=self._fmt(size), brackets=ticks)
            log.info("Lệnh + OCO (SL %d tick, TP %d tick): %s", round(ticks[0]), round(ticks[1]),
                     resp)
        else:
            self._market(is_buy=is_buy, size=self._fmt(size), price=sig.price, reduce_only=False)
        self.trade = Trade(sig.direction, sig.price, sig.sl, [sig.scalp_tp, sig.tp, None, None],
                           size, key, risk=abs(sig.price - sig.sl), size=size,
                           opened_at=self.now().timestamp(), best=sig.price)
        self.day_trades += 1
        self.save_state()
        if self._oco():
            self._attach_brackets()
        else:
            self._place_stop()

    # -------------------------------------------------------------- management
    def manage_momo(self, price: float, c1) -> None:
        t = self.trade
        p = self.params
        sign = 1 if t.side == "LONG" else -1

        # the exchange stop (or the OCO target) already filled between polls
        if self._oco() and t.stop_id is None:
            self._attach_brackets()
        if t.stop_id is not None and self._can_stop() and hasattr(self.client, "position_size"):
            try:
                if self.client.position_size(self.cfg.symbol) == 0:
                    target = t.targets[1]
                    hit_tp = target is not None and abs(price - target) < abs(price - t.sl)
                    px = target if hit_tp else t.sl
                    self._cancel_protection()  # the other OCO leg, if still resting
                    what = "chốt lời" if hit_tp else "stop"
                    self._reduce(1, px, f"{what} trên sàn đã khớp {px:,.2f}", send=False)
                    return
            except Exception as e:
                log.warning("Không kiểm tra được vị thế: %s", e)

        if sign * (price - t.sl) <= 0:
            self._exit(1, price, f"SL {t.sl:,.2f}")
            return
        guard = self.prop_breach_guard(price)
        if guard:
            self._exit(1, price, guard)
            return
        t.best = price if t.best is None else (max(t.best, price) if sign > 0 else min(t.best, price))

        scalp, target = t.targets[0], t.targets[1]
        if sign * (price - target) >= 0:
            self._exit(1, price, f"mục tiêu {target:,.2f}")
            return
        if t.stage == 0 and scalp is not None and sign * (price - scalp) >= 0:
            self._exit(0.5, price, f"chốt scalp {scalp:,.2f}")
            if self.trade:
                t.stage = 1
                t.sl = t.entry + sign * p.tick  # break-even
                self._place_stop()
            return
        if t.stage >= 1 or scalp is None:
            gap = p.trail_r * t.risk if p.trail_r else p.trail
            trail = t.best - sign * gap
            if sign * (trail - t.sl) > p.tick and sign * (price - t.entry) > gap:
                t.sl = self._round(trail)
                self._place_stop()
                log.info("Trail SL -> %.2f", t.sl)

        last = c1[-2]  # last closed 1M candle
        if sign * (last.c - last.o) <= -p.fade_body:
            self._exit(1, price, f"momentum tắt (nến 1M ngược {abs(last.c - last.o):.1f} điểm)")
            return
        if t.opened_at is not None and self.now().timestamp() - t.opened_at >= p.max_hold_min * 60:
            self._exit(1, price, f"giữ quá {p.max_hold_min:g} phút")
            return
