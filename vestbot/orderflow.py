"""Bookmap-style order-flow signals for NQ/MNQ from L2 depth + time & sales.

What a Bookmap trader reads off the screen, turned into fixed rules:

* **Heatmap walls** — resting limit orders much bigger than the levels around them
  (the yellow lines). A wall that has stood for ``wall_age`` seconds is support/resistance.
* **Bubbles** — single aggressive prints (or prints in the same millisecond) of at least
  ``big_trade`` lots ("30 or more lot entries").
* **Absorption** — aggressive sellers keep hitting one price, ``absorb_min_vol`` lots in
  ``absorb_window`` seconds, but price can't trade lower and bounces ``confirm_ticks``
  → buyers absorbed the selling → LONG (mirror for SHORT).
* **Wall bounce** — sellers test a standing bid wall (``test_min_vol`` lots within
  ``touch_ticks``), the wall stays, price bounces → LONG.
* **Wall eaten** — aggressive buyers trade through an ask wall (at least ``eat_frac`` of its
  size traded there before it disappeared) → breakout LONG. A wall that vanishes with no
  trades is *pulled* (spoof) and is only logged.
* **Delta / CVD** and **book imbalance** (top ``imb_levels`` bid vs ask size) add confluence.

A trigger (absorption, wall bounce, wall eaten) scores 2; each agreeing bubble / delta /
imbalance / same-level wall adds 1, a strongly opposite delta takes 1. A signal needs
``min_score``. SL goes ``sl_buffer`` ticks behind the trigger level; TP stops one tick in
front of the nearest opposite wall, otherwise ``rr`` × risk. Pure code: every method takes
the event time, so the same engine runs live, on recordings and in tests.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, fields
from statistics import median

# ProjectX DomType values in GatewayDepth
DOM_ASK, DOM_BID, DOM_BEST_ASK, DOM_BEST_BID, DOM_RESET = 1, 2, 3, 4, 6
DOM_NEW_BEST_BID, DOM_NEW_BEST_ASK = 9, 10
ASK_TYPES = (DOM_ASK, DOM_BEST_ASK, DOM_NEW_BEST_ASK)
BID_TYPES = (DOM_BID, DOM_BEST_BID, DOM_NEW_BEST_BID)
BUY, SELL = 0, 1  # ProjectX trade ``type`` = aggressor side


@dataclass
class FlowParams:
    tick: float = 0.25
    big_trade: int = 30          # bubble: one print (or same-ms prints) >= this many lots
    bubble_window: float = 30.0  # a bubble counts as confluence for this many seconds
    wall_min: int = 40           # a wall has at least this many lots ...
    wall_mult: float = 4.0       # ... and >= this x the median level size near the price
    wall_range: int = 80         # look for walls within this many ticks of the price
    wall_age: float = 10.0       # a wall must stand this long before it counts
    eat_frac: float = 0.5        # wall gone after this share of it traded = eaten, else pulled
    absorb_window: float = 30.0
    absorb_min_vol: int = 150    # aggressive lots into one level for absorption
    absorb_band: int = 2         # ticks around the defended level that count as "that level"
    touch_ticks: int = 2         # within this many ticks of a wall = touched it
    test_min_vol: int = 30       # aggressive lots into the wall for a real test of it
    confirm_ticks: int = 4       # bounce needed off the level before entering
    delta_window: float = 60.0
    delta_min: int = 100         # |delta| over delta_window that counts as agreeing
    imb_levels: int = 10
    imb_ratio: float = 2.0
    min_score: int = 3
    sl_buffer: int = 6           # SL this many ticks behind the trigger level
    max_sl: float = 15.0         # points; further = skip
    min_sl: float = 3.0
    rr: float = 2.0              # TP when no wall in the way
    min_rr: float = 1.2          # skip when the nearest opposite wall leaves less than this
    cooldown: float = 60.0       # seconds between signals in the same direction
    eval_every: float = 0.1      # re-check triggers at most this often (busy tape)

    @classmethod
    def from_dict(cls, raw: dict) -> "FlowParams":
        """Build from lower-cased FLOW_* keys, e.g. {"big_trade": "20"}."""
        kw = {}
        for f in fields(cls):
            if raw.get(f.name) not in (None, ""):
                kw[f.name] = type(f.default)(float(raw[f.name]))
        return cls(**kw)


@dataclass
class Wall:
    side: str           # "bid" or "ask"
    price: float
    size: float
    first_seen: float
    last_seen: float
    traded: float = 0.0  # lots traded at this price while the wall stood


@dataclass
class FlowSignal:
    ts: float
    direction: str      # LONG / SHORT
    kind: str           # absorption / wall_bounce / wall_eaten
    level: float
    entry: float
    sl: float
    tp: float
    score: int
    reasons: list[str] = field(default_factory=list)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    def text(self, symbol: str = "NQ") -> str:
        rr = abs(self.tp - self.entry) / self.risk if self.risk else 0
        return (f"{symbol} {self.direction} ({self.kind} @ {self.level:,.2f}) score {self.score}\n"
                f"Entry {self.entry:,.2f} | SL {self.sl:,.2f} ({self.risk:.2f} pts) | "
                f"TP {self.tp:,.2f} ({rr:.1f}R)\n" + " · ".join(self.reasons))


class OrderBook:
    def __init__(self):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    def apply(self, dom_type: int, price: float, volume: float) -> None:
        if dom_type == DOM_RESET:
            self.bids.clear()
            self.asks.clear()
            return
        if dom_type in BID_TYPES:
            book, other, crossed = self.bids, self.asks, lambda p: p <= price
        elif dom_type in ASK_TYPES:
            book, other, crossed = self.asks, self.bids, lambda p: p >= price
        else:
            return
        if volume > 0:
            book[price] = volume
            for p in [p for p in other if crossed(p)]:  # drop stale levels across the spread
                del other[p]
        else:
            book.pop(price, None)

    @property
    def best_bid(self) -> float | None:
        return max(self.bids) if self.bids else None

    @property
    def best_ask(self) -> float | None:
        return min(self.asks) if self.asks else None

    def imbalance(self, levels: int) -> float:
        """Top-of-book bid size / ask size (> 1 = more bids)."""
        b = sum(self.bids[p] for p in sorted(self.bids, reverse=True)[:levels])
        a = sum(self.asks[p] for p in sorted(self.asks)[:levels])
        return b / a if a else (float("inf") if b else 1.0)


class FlowEngine:
    def __init__(self, params: FlowParams | None = None):
        self.p = params or FlowParams()
        self.book = OrderBook()
        self.trades: deque[tuple[float, float, float, int]] = deque()  # ts, price, size, side
        self.walls: dict[tuple[str, float], Wall] = {}
        self.bubbles: deque[tuple[float, float, float, int]] = deque()
        self.events: deque[str] = deque(maxlen=50)  # pulled/eaten walls, bubbles: for logs
        self.cvd = 0.0
        self.last_price: float | None = None
        self.last_signal: dict[str, float] = {}
        self.used_levels: dict[tuple[str, str, float], float] = {}
        self._pending: tuple[float, float, int, float] | None = None  # same-ms print aggregate
        self._eaten: list[Wall] = []
        self._last_eval = -1e18

    # ------------------------------------------------------------------ input
    def on_depth(self, ts: float, dom_type: int, price: float, volume: float) -> None:
        self.book.apply(dom_type, price, volume)

    def on_trade(self, ts: float, price: float, size: float, side: int | None = None) -> FlowSignal | None:
        if side is None:  # no aggressor flag: classify against the book
            ask = self.book.best_ask
            side = BUY if ask is not None and price >= ask else SELL
        self.last_price = price
        self.cvd += size if side == BUY else -size
        self.trades.append((ts, price, size, side))
        for key in (("bid", price), ("ask", price)):
            if key in self.walls:
                self.walls[key].traded += size
        self._bubble(ts, price, size, side)
        if ts - self._last_eval < self.p.eval_every:
            return None
        return self.evaluate(ts)

    def _bubble(self, ts, price, size, side) -> None:
        pend = self._pending
        if pend and pend[0] == ts and pend[2] == side:
            self._pending = (ts, price, side, pend[3] + size)
        else:
            self._flush_bubble()
            self._pending = (ts, price, side, size)
        if self._pending[3] >= self.p.big_trade:
            self._flush_bubble()

    def _flush_bubble(self) -> None:
        if self._pending and self._pending[3] >= self.p.big_trade:
            ts, price, side, size = self._pending
            self.bubbles.append((ts, price, size, side))
            self.events.append(f"bubble {'BUY' if side == BUY else 'SELL'} {size:g} @ {price:,.2f}")
        self._pending = None

    # ------------------------------------------------------------------ state
    def _trim(self, ts: float) -> None:
        keep = max(self.p.absorb_window, self.p.delta_window)
        while self.trades and self.trades[0][0] < ts - keep:
            self.trades.popleft()
        while self.bubbles and self.bubbles[0][0] < ts - self.p.bubble_window:
            self.bubbles.popleft()
        for k, t in list(self.used_levels.items()):
            if t < ts - self.p.cooldown * 5:
                del self.used_levels[k]

    def update_walls(self, ts: float) -> None:
        price = self.last_price
        if price is None:
            return
        span = self.p.wall_range * self.p.tick
        seen = set()
        for side, book in (("bid", self.book.bids), ("ask", self.book.asks)):
            near = {p: v for p, v in book.items() if abs(p - price) <= span}
            if not near:
                continue
            floor = max(self.p.wall_min, self.p.wall_mult * median(near.values()))
            for p, v in near.items():
                if v < floor:
                    continue
                seen.add((side, p))
                w = self.walls.get((side, p))
                if w is None:
                    self.walls[(side, p)] = Wall(side, p, v, ts, ts)
                else:
                    w.size, w.last_seen = max(w.size, v), ts
        for key, w in list(self.walls.items()):
            if key in seen:
                continue
            # left the window or shrank below the floor; only judge walls the price reached
            del self.walls[key]
            if ts - w.first_seen < self.p.wall_age or abs(w.price - price) > span:
                continue
            if w.traded >= self.p.eat_frac * w.size:
                self.events.append(f"{w.side} wall EATEN {w.size:g} @ {w.price:,.2f}")
                self._eaten.append(w)
            elif self.book.bids.get(w.price, 0) + self.book.asks.get(w.price, 0) < w.size * 0.3:
                self.events.append(f"{w.side} wall PULLED {w.size:g} @ {w.price:,.2f}")

    def standing_walls(self, ts: float, side: str) -> list[Wall]:
        return [w for w in self.walls.values()
                if w.side == side and ts - w.first_seen >= self.p.wall_age]

    def delta(self, ts: float) -> float:
        return sum(s if side == BUY else -s for t, _, s, side in self.trades
                   if t >= ts - self.p.delta_window)

    # ------------------------------------------------------------- triggers
    def _window(self, ts: float) -> list[tuple[float, float, float, int]]:
        return [x for x in self.trades if x[0] >= ts - self.p.absorb_window]

    def _absorption(self, ts: float, d: int) -> float | None:
        """LONG (d=1): sellers hammered the window low and price bounced. Mirror for SHORT."""
        win = self._window(ts)
        if not win or self.last_price is None:
            return None
        level = min(x[1] for x in win) if d > 0 else max(x[1] for x in win)
        band = self.p.absorb_band * self.p.tick
        hit_side = SELL if d > 0 else BUY
        vol = sum(s for _, p, s, side in win if side == hit_side and abs(p - level) <= band)
        bounced = d * (self.last_price - level) >= self.p.confirm_ticks * self.p.tick
        return level if vol >= self.p.absorb_min_vol and bounced else None

    def _wall_bounce(self, ts: float, d: int) -> float | None:
        win = self._window(ts)
        if not win or self.last_price is None:
            return None
        side = "bid" if d > 0 else "ask"
        extreme = min(x[1] for x in win) if d > 0 else max(x[1] for x in win)
        touch = self.p.touch_ticks * self.p.tick
        hit_side = SELL if d > 0 else BUY
        for w in sorted(self.standing_walls(ts, side), key=lambda w: abs(w.price - extreme)):
            touched = -self.p.tick <= d * (extreme - w.price) <= touch
            bounced = d * (self.last_price - extreme) >= self.p.confirm_ticks * self.p.tick
            tested = sum(s for _, p, s, sd in win
                         if sd == hit_side and abs(p - w.price) <= touch) >= self.p.test_min_vol
            if touched and bounced and tested:
                return w.price
        return None

    def _wall_eaten(self, ts: float, d: int) -> float | None:
        side = "ask" if d > 0 else "bid"
        for w in self._eaten:
            if w.side == side and self.last_price is not None and d * (self.last_price - w.price) >= 0:
                return w.price
        return None

    # ------------------------------------------------------------- evaluate
    def evaluate(self, ts: float) -> FlowSignal | None:
        self._last_eval = ts
        self._trim(ts)
        self.update_walls(ts)
        best = None
        for d, name in ((1, "LONG"), (-1, "SHORT")):
            if ts - self.last_signal.get(name, -1e18) < self.p.cooldown:
                continue
            for kind, fn in (("absorption", self._absorption), ("wall_bounce", self._wall_bounce),
                             ("wall_eaten", self._wall_eaten)):
                level = fn(ts, d)
                if level is None or (name, kind, level) in self.used_levels:
                    continue
                sig = self._build(ts, d, name, kind, level)
                if sig and (best is None or sig.score > best.score):
                    best = sig
        self._eaten = [w for w in self._eaten if ts - w.last_seen < self.p.absorb_window]
        if best:
            self.last_signal[best.direction] = ts
            self.used_levels[(best.direction, best.kind, best.level)] = ts
        return best

    def _build(self, ts, d, name, kind, level) -> FlowSignal | None:
        p, entry = self.p, self.last_price
        score, reasons = 2, [kind.replace("_", " ") + f" {level:,.2f}"]
        own_side = "bid" if d > 0 else "ask"
        if kind == "absorption" and any(abs(w.price - level) <= p.absorb_band * p.tick
                                        for w in self.standing_walls(ts, own_side)):
            score += 1
            reasons.append("wall at the level")
        if kind == "wall_bounce":
            win = self._window(ts)
            hit = sum(s for _, pr, s, side in win
                      if side == (SELL if d > 0 else BUY) and abs(pr - level) <= p.absorb_band * p.tick)
            if hit >= p.absorb_min_vol:
                score += 1
                reasons.append(f"absorbed {hit:g} lots")
        bub = [b for b in self.bubbles if b[3] == (BUY if d > 0 else SELL)]
        if bub:
            score += 1
            reasons.append(f"bubble {max(b[2] for b in bub):g} lots")
        delta = self.delta(ts)
        if d * delta >= p.delta_min:
            score += 1
            reasons.append(f"delta {delta:+g}")
        elif -d * delta >= 2 * p.delta_min:
            score -= 1
            reasons.append(f"delta against {delta:+g}")
        imb = self.book.imbalance(p.imb_levels)
        if (imb >= p.imb_ratio) if d > 0 else (imb > 0 and 1 / imb >= p.imb_ratio):
            score += 1
            reasons.append(f"book imbalance {imb:.1f}")
        if score < p.min_score:
            return None

        sl = level - d * p.sl_buffer * p.tick
        risk = d * (entry - sl)
        if risk < p.min_sl:
            sl, risk = entry - d * p.min_sl, p.min_sl
        if risk > p.max_sl:
            return None
        tp = entry + d * p.rr * risk
        opposite = [w.price for w in self.standing_walls(ts, "ask" if d > 0 else "bid")
                    if d * (w.price - entry) > 0]
        if opposite:
            wall = min(opposite) if d > 0 else max(opposite)
            capped = wall - d * p.tick
            if d * (capped - entry) < p.min_rr * risk:
                return None
            if d * (capped - tp) < 0:
                tp = capped
                reasons.append(f"TP before wall {wall:,.2f}")
        return FlowSignal(ts, name, kind, level, entry, round(sl, 2), round(tp, 2), score, reasons)

    # --------------------------------------------------------------- status
    def status(self, ts: float) -> str:
        bb, ba = self.book.best_bid, self.book.best_ask
        walls = sorted(self.standing_walls(ts, "bid") + self.standing_walls(ts, "ask"),
                       key=lambda w: -w.size)[:4]
        ws = ", ".join(f"{w.side} {w.size:g}@{w.price:,.2f}" for w in walls) or "-"
        return (f"px {self.last_price} bid {bb} ask {ba} | CVD {self.cvd:+g} "
                f"Δ{self.p.delta_window:g}s {self.delta(ts):+g} | "
                f"imb {self.book.imbalance(self.p.imb_levels):.2f} | walls {ws}")
