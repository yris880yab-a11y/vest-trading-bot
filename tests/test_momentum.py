from datetime import datetime, timedelta, timezone

from test_bot import make_cfg

from vestbot.momentum import MomoParams, momentum_signal
from vestbot.momentum_bot import MomentumBot
from vestbot.smc import Candle


def quiet_5m(n=30, base=30000.0, size=10.0):
    """Choppy 5M candles with an ATR of about ``size``."""
    out, p = [], base
    for i in range(n):
        step = size / 2 if i % 2 else -size / 2
        out.append(Candle(i, p, max(p, p + step) + 2, min(p, p + step) - 2, p + step))
        p += step
    return out


def burst(direction=1, body=20.0, wick_back=1.0):
    """5M history + a fast forming 5M candle and two strong 1M candles."""
    c5 = quiet_5m()
    o = c5[-1].c
    c = o + direction * body
    hi, lo = (c + wick_back, o - 1) if direction > 0 else (o + 1, c - wick_back)
    c5.append(Candle(99, o, hi, lo, c))
    c1 = [Candle(i, o, o + 1, o - 1, o) for i in range(10)]
    m = o + direction * (body - 12)
    c1 += [Candle(10, m, max(m, m + direction * 6) + 0.5, min(m, m + direction * 6) - 0.5, m + direction * 6),
           Candle(11, m + direction * 6, max(c, m + direction * 6) + 0.5, min(c, m + direction * 6) - 0.5, c)]
    c15 = quiet_5m(30, size=20)
    return c1, c5, c15


def test_fast_5m_candle_gives_long_with_scaled_target():
    c1, c5, c15 = burst(+1)
    sig, why = momentum_signal(c1, c5, c15)
    assert sig is not None, why
    assert sig.direction == "LONG"
    p = MomoParams()
    assert p.min_sl <= sig.price - sig.sl <= p.max_sl
    assert p.min_tp <= sig.tp - sig.price <= p.max_tp
    assert sig.scalp_tp == sig.price + p.scalp_tp


def test_short_is_the_mirror():
    sig, why = momentum_signal(*burst(-1))
    assert sig is not None and sig.direction == "SHORT", why
    assert sig.sl > sig.price > sig.tp


def test_no_trade_on_slow_or_rejected_candle():
    sig, why = momentum_signal(*burst(+1, body=8))
    assert sig is None and any("chưa đủ nhanh" in w for w in why)
    sig, why = momentum_signal(*burst(+1, wick_back=15))
    assert sig is None and any("râu" in w for w in why)


class Feed:
    """Serves 1M/5M/15M candles and emulates a resting stop like the exchange."""

    def __init__(self, c1, c5, c15):
        self.data = {"1m": c1, "5m": c5, "15m": c15}
        self.orders, self.stops, self.pos = [], {}, 0.0

    def klines(self, symbol, interval, limit):
        return [[x.t, x.o, x.h, x.l, x.c, 0] for x in self.data[interval]]

    def place_order(self, **kw):
        self.orders.append(kw)
        self.pos += float(kw["size"]) * (1 if kw["is_buy"] else -1)
        return {"id": len(self.orders)}

    def place_stop(self, *, symbol, is_buy, size, stop_price):
        sid = 100 + len(self.stops)
        self.stops[sid] = stop_price
        return sid

    def cancel_order(self, order_id):
        self.stops.pop(order_id, None)

    def position_size(self, symbol):
        return abs(self.pos)

    def set_price(self, px):
        last = self.data["1m"][-1]
        self.data["1m"] = self.data["1m"][:-1] + [Candle(last.t, last.o, max(last.h, px), min(last.l, px), px)]


def bot_for(feed, **over):
    cfg = make_cfg(dry_run=False, strategy="momentum", order_size="2", size_decimals=0,
                   momo={}, **over)
    bot = MomentumBot(cfg, feed)
    t0 = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
    bot.clock = [t0]
    bot.now = lambda: bot.clock[0]
    return bot


def test_entry_rests_a_stop_then_scalps_half_and_moves_to_break_even():
    feed = Feed(*burst(+1))
    bot = bot_for(feed)
    bot.tick()
    t = bot.trade
    assert t and t.side == "LONG" and feed.orders[0]["is_buy"] is True
    assert list(feed.stops.values()) == [round(t.sl / 0.25) * 0.25]

    feed.set_price(t.targets[0] + 0.25)  # scalp target
    bot.tick()
    assert feed.orders[-1]["reduce_only"] and feed.orders[-1]["size"] == "1"
    assert bot.trade.sl == t.entry + 0.25            # break-even
    assert list(feed.stops.values()) == [t.entry + 0.25]  # stop re-armed for the rest
    bot.tick()
    assert len(feed.orders) == 2                       # same candle: nothing new


def test_exchange_stop_fill_is_booked_without_a_second_order():
    feed = Feed(*burst(+1))
    bot = bot_for(feed)
    bot.tick()
    feed.pos = 0  # the resting stop filled between polls
    bot.tick()
    assert bot.trade is None and len(feed.orders) == 1  # no extra close order sent
    assert bot.day_r < -0.9


def test_time_stop():
    feed = Feed(*burst(+1))
    bot = bot_for(feed)
    bot.tick()
    bot.clock[0] += timedelta(minutes=16)
    bot.tick()
    assert bot.trade is None and feed.orders[-1]["reduce_only"]


def test_reentry_in_same_candle_only_when_allowed():
    feed = Feed(*burst(+1))
    bot = bot_for(feed)
    bot.tick()
    feed.pos = 0
    bot.tick()                        # stopped out by the exchange stop
    bot.tick()
    assert bot.trade is None          # default: one entry per 5M candle
    bot.params = bot.params.__class__(max_per_candle=2)
    bot.tick()
    assert bot.trade is not None      # second entry allowed in the same candle


def closed_impulse(retrace=2.0):
    """The 5M impulse has just closed; the new 5M candle and 1M sit near its close."""
    c1, c5, c15 = burst(+1)
    x = c5[-1]
    px = x.c - retrace
    c5.append(Candle(100, x.c, x.c + 0.5, px - 0.5, px))
    c1 = c1[:-1] + [Candle(12, x.c, x.c + 0.5, x.c - 1, x.c - 0.5), Candle(13, x.c - 0.5, x.c, px - 0.5, px)]
    return c1, c5, c15


def test_close_mode_enters_after_the_impulse_closes():
    p = MomoParams(entry_mode="close")
    sig, why = momentum_signal(*closed_impulse(retrace=2.0), p)
    assert sig is not None and sig.direction == "LONG", why
    sig, why = momentum_signal(*closed_impulse(retrace=12.0), p)  # gave back 60% of a 20pt candle
    assert sig is None and any("hồi quá" in w for w in why)


def test_pullback_mode_waits_for_dip_then_resumption():
    c1, c5, c15 = burst(+1)
    top = c1[-1].c
    dip = Candle(12, top, top + 0.5, top - 7, top - 6)        # 1M pullback ~35% of 20pt impulse
    go = Candle(13, top - 6, top + 2, top - 6.5, top + 1.5)   # breaks back above the dip candle
    p = MomoParams(entry_mode="pullback")
    sig, why = momentum_signal(c1 + [dip], c5, c15, p)
    assert sig is None
    sig, why = momentum_signal(c1 + [dip, go], c5, c15, p)
    assert sig is not None and sig.direction == "LONG", why
    assert sig.sl <= top - 7 - p.sl_buffer + 1e-9 or sig.price - sig.sl == p.min_sl


def test_breakout_mode_needs_tight_pause_then_break():
    c1, c5, c15 = burst(+1)
    top = c1[-1].c
    pause = [Candle(12 + i, top, top + 1, top - 2, top - 0.5) for i in range(3)]
    p = MomoParams(entry_mode="breakout")
    sig, why = momentum_signal(c1 + pause + [Candle(15, top, top + 0.5, top - 1, top)], c5, c15, p)
    assert sig is None and any("chờ phá" in w for w in why)
    sig, why = momentum_signal(c1 + pause + [Candle(15, top, top + 3, top - 0.5, top + 2.5)], c5, c15, p)
    assert sig is not None and sig.direction == "LONG", why
