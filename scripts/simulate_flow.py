"""Simulate a day of NQ order flow (L2 depth + trades) in ProjectX message format.

No candles: market orders eat the book level by level, so price only moves when the
resting size at the touch is used up. Walls (big resting orders) appear away from the
price; when price reaches one it either holds (aggressors get absorbed, then flow turns),
gets eaten (flow keeps lifting/hitting it), or is pulled before the touch (spoof).

    python scripts/simulate_flow.py --out data/sim.jsonl --hours 6.5 --seed 1 --edge 0
    python -m vestbot.flow_signal NQ --replay data/sim.jsonl

``--edge 0``: a held or eaten wall says nothing about what comes next — after a short
reaction price is a random walk again (a fair test: the bot should not make money).
``--edge 0.7``: after a held/eaten wall the flow follows that direction for 1-3 minutes
with probability 0.7, i.e. the Bookmap read works. Synthetic data shows whether the
mechanics work, not whether NQ really behaves like this.
"""
from __future__ import annotations

import argparse
import json
import math
import random

TICK = 0.25


class Sim:
    def __init__(self, seed: int, edge: float, start_ts: float, start_px: float = 25000.0,
                 trend: float = 0.08):
        self.r = random.Random(seed)
        self.trend = trend
        self.edge = edge
        self.ts = start_ts
        self.bid = round(start_px / TICK)  # prices in ticks; ask = bid + 1 always
        self.book: dict[int, float] = {}   # tick -> size, bids <= bid, asks > bid
        self.walls: dict[int, dict] = {}   # tick -> {"size", "fate", "touched"}
        self.p_buy = 0.5                   # share of aggressive volume that buys
        self.bias_until, self.bias = 0.0, 0.5
        self.intensity = 1.0
        self.out: list[dict] = []
        for t in range(self.bid - 80, self.bid + 81):
            self.book[t] = self._base()
        self._snapshot()

    def _base(self) -> float:
        return max(1, round(self.r.lognormvariate(math.log(6), 0.6)))

    def _depth(self, t: int, size: float) -> None:
        typ = 2 if t <= self.bid else 1
        self.out.append({"k": "depth", "d": [{"timestamp": round(self.ts, 3), "type": typ,
                                              "price": t * TICK, "volume": size}]})

    def _snapshot(self) -> None:
        self.out.append({"k": "depth", "d": [{"timestamp": self.ts, "type": 6, "price": 0, "volume": 0}]})
        for t, s in self.book.items():
            self._depth(t, s)

    def _trade(self, price_t: int, size: float, side: int) -> None:
        self.out.append({"k": "trade", "d": [{"timestamp": round(self.ts, 3), "price": price_t * TICK,
                                              "volume": size, "type": side}]})

    # ---------------------------------------------------------------- market
    def _market(self, side: int, size: float) -> None:
        """Aggressive order: buy eats asks upward, sell eats bids downward."""
        while size > 0:
            t = self.bid + 1 if side == 0 else self.bid
            avail = self.book.get(t, 0) or self._base()
            fill = min(size, avail)
            self._trade(t, fill, side)
            size -= fill
            left = avail - fill
            if t in self.walls:
                self._touch(t, side)
                w = self.walls[t]
                if w["fate"] == "hold":  # iceberg: the defender refills, price can't get through
                    left = max(left, w["size"] * 0.5)
            if left > 0:
                self.book[t] = left
                self._depth(t, left)
                continue
            self.book.pop(t, None)
            self._depth(t, 0)
            w = self.walls.pop(t, None)
            if w:  # wall eaten: follow-through in the eating direction
                self._react(+1 if side == 0 else -1)
            self.bid += 1 if side == 0 else -1
            # the vacated level flips side and refills; extend the book's far edge
            new = self.bid if side == 0 else self.bid + 1
            self.book[new] = self._base()
            self._depth(new, self.book[new])
            far = self.bid + 80 if side == 0 else self.bid - 80
            if far not in self.book:
                self.book[far] = self._base()
                self._depth(far, self.book[far])

    def _touch(self, t: int, side: int) -> None:
        w = self.walls[t]
        if w["touched"]:
            return
        w["touched"] = True
        if w["fate"] == "hold":
            # aggressors keep hitting for 15-30 s (absorption), then the flow turns
            self.bias, self.bias_until = (0.75 if side == 0 else 0.25), self.ts + self.r.uniform(15, 30)
            self.intensity = 2.5
            w["turn_at"] = self.bias_until
        else:  # eaten: heavy flow until it is gone
            self.bias, self.bias_until = (0.85 if side == 0 else 0.15), self.ts + 120
            self.intensity = 3.0

    def _react(self, d: int) -> None:
        """After a wall resolves: flow follows ``d`` with probability ``edge``, else the
        opposite way; with ``edge`` 0 the flow is simply neutral again."""
        if self.edge and self.r.random() >= self.edge:
            d = -d
        self.bias = 0.5 + 0.2 * d if self.edge else 0.5
        self.bias_until = self.ts + self.r.uniform(60, 180)
        self.intensity = 1.5

    # ------------------------------------------------------------------ walls
    def _walls(self, dt: float) -> None:
        if self.r.random() < dt / 120:  # a new wall about every 2 minutes
            d = self.r.choice((-1, 1))
            t = self.bid + (d * self.r.randint(10, 40) if d > 0 else -self.r.randint(10, 40))
            size = self.r.randint(150, 500)
            fate = self.r.choices(("hold", "eaten", "pull"), (0.45, 0.35, 0.2))[0]
            self.walls[t] = {"size": size, "fate": fate, "touched": False}
            self.book[t] = size
            self._depth(t, size)
        for t, w in list(self.walls.items()):
            if w["fate"] == "pull" and abs(t - self.bid) <= 4:
                self.walls.pop(t)
                self.book[t] = self._base()
                self._depth(t, self.book[t])
            elif w.get("turn_at") and self.ts >= w["turn_at"]:
                w.pop("turn_at")
                d = -1 if t > self.bid else 1  # held ask wall -> down, held bid wall -> up
                self._react(d)
                w["fate"] = "done"  # an ordinary level from now on
                if self.edge == 0:  # fair test: the defender leaves, nothing props price up
                    self.walls.pop(t)
                    self.book[t] = self._base()
                    self._depth(t, self.book[t])
            elif abs(t - self.bid) > 120:
                self.walls.pop(t)

    # -------------------------------------------------------------------- run
    def step(self, dt: float) -> None:
        self.ts += dt
        if self.ts >= self.bias_until:  # background regime: random trend for 3-15 min
            self.bias = min(0.7, max(0.3, self.r.gauss(0.5, self.trend)))
            self.bias_until, self.intensity = self.ts + self.r.uniform(180, 900), 1.0
        # slow random drift in who is aggressive, pulled toward the regime bias
        self.p_buy += 0.1 * (self.bias - self.p_buy) + self.r.gauss(0, 0.02)
        self.p_buy = min(0.9, max(0.1, self.p_buy))
        n = self._poisson(6 * self.intensity * dt)
        for _ in range(n):
            side = 0 if self.r.random() < self.p_buy else 1
            size = 1 + int(self.r.expovariate(0.6))
            if self.r.random() < 0.008:
                size = self.r.randint(25, 60)  # occasional bubble
            self._market(side, size)
        self._walls(dt)
        # passive traders refresh sizes near the touch now and then
        if self.r.random() < dt * 3:
            t = self.bid + self.r.randint(-10, 11)
            if t not in self.walls and t in self.book:
                self.book[t] = self._base()
                self._depth(t, self.book[t])

    def _poisson(self, lam: float) -> int:
        k, p, L = 0, 1.0, math.exp(-lam)
        while True:
            p *= self.r.random()
            if p <= L:
                return k
            k += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--hours", type=float, default=6.5)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--edge", type=float, default=0.0)
    ap.add_argument("--start", type=float, default=1792071000.0, help="epoch seconds (13:30 UTC)")
    a = ap.parse_args()
    sim, dt = Sim(a.seed, a.edge, a.start), 0.1
    with open(a.out, "w") as f:
        for _ in range(int(a.hours * 3600 / dt)):
            sim.step(dt)
            for m in sim.out:
                f.write(json.dumps(m) + "\n")
            sim.out.clear()


if __name__ == "__main__":
    main()
