"""Multi-timeframe liquidity strategy: sweep -> MSS -> retest.

Flow (one pass of :func:`analyze`):

    Daily/4H context -> 1H confirm -> 15M location -> 5M sweep + MSS
    -> 1M trigger/retest -> momentum score -> entry -> structural SL
    -> liquidity targets -> LONG / SHORT / WAIT

All functions are pure (candles in, report out) so they can be tested offline.
Bullish logic is implemented by mirroring prices (negating them) and reusing the
bearish logic, so both sides follow exactly the same rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

Dir = Literal["LONG", "SHORT"]
TIMEFRAMES = ("1d", "4h", "1h", "15m", "5m", "1m")

# Rules that turn the discretionary framework into numbers (tune here).
TP1_MIN_R = 0.5        # liquidity closer than this (in R) is noise, not a target
MAX_CHASE_ATR15 = 1.5  # entry may be at most this many 15M ATRs past the 5M MSS level
FRESH_5M_BARS = 12     # the 1M trigger must come within this many 5M bars of the 5M MSS
MSS_MAX_BARS = 12      # MSS must follow the sweep within this many candles (any timeframe)


# --------------------------------------------------------------------------- data
@dataclass(frozen=True)
class Candle:
    t: Any
    o: float
    h: float
    l: float  # noqa: E741
    c: float

    @property
    def body(self) -> float:
        return abs(self.c - self.o)

    @property
    def range(self) -> float:
        return self.h - self.l


def parse_candles(klines: Any) -> list[Candle]:
    """Vest /klines -> candles. Accepts ``[t, o, h, l, c, ...]`` rows or dicts."""
    rows = klines.get("data", klines) if isinstance(klines, dict) else klines
    out = []
    for r in rows:
        if isinstance(r, dict):
            def g(*keys, r=r):
                return next(r[k] for k in keys if k in r)
            out.append(Candle(g("openTime", "t"), float(g("open", "o")), float(g("high", "h")),
                              float(g("low", "l")), float(g("close", "c"))))
        else:
            out.append(Candle(r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4])))
    return out


def _mirror(c: list[Candle]) -> list[Candle]:
    return [Candle(x.t, -x.o, -x.l, -x.h, -x.c) for x in c]


# ---------------------------------------------------------------------- building blocks
@dataclass(frozen=True)
class Swing:
    i: int
    price: float
    kind: Literal["H", "L"]


def swings(c: list[Candle], n: int = 2) -> list[Swing]:
    """Fractal swing highs/lows confirmed by ``n`` candles on each side."""
    out = []
    for i in range(n, len(c) - n):
        left, right = c[i - n:i], c[i + 1:i + n + 1]
        if all(c[i].h > x.h for x in left) and all(c[i].h >= x.h for x in right):
            out.append(Swing(i, c[i].h, "H"))
        if all(c[i].l < x.l for x in left) and all(c[i].l <= x.l for x in right):
            out.append(Swing(i, c[i].l, "L"))
    return out


def structure(c: list[Candle], n: int = 2) -> str:
    """BULL (HH/HL), BEAR (LH/LL) or RANGE from the last two swings of each kind."""
    sw = swings(c, n)
    hs = [s.price for s in sw if s.kind == "H"][-2:]
    ls = [s.price for s in sw if s.kind == "L"][-2:]
    if len(hs) < 2 or len(ls) < 2:
        return "RANGE"
    if hs[1] > hs[0] and ls[1] > ls[0]:
        return "BULL"
    if hs[1] < hs[0] and ls[1] < ls[0]:
        return "BEAR"
    return "RANGE"


def atr(c: list[Candle], n: int = 14) -> float:
    if not c:
        return 0.0
    if len(c) < 2:
        return c[-1].range
    trs = [max(x.h - x.l, abs(x.h - p.c), abs(x.l - p.c)) for p, x in zip(c[:-1], c[1:])]
    trs = trs[-n:]
    return sum(trs) / len(trs)


def range_position(c: list[Candle]) -> float:
    """0 = bottom of the dealing range (discount), 1 = top (premium)."""
    hi, lo = max(x.h for x in c), min(x.l for x in c)
    return 0.5 if hi == lo else (c[-1].c - lo) / (hi - lo)


def unswept_levels(c: list[Candle], kind: Literal["H", "L"], n: int = 2) -> list[float]:
    """Swing highs/lows whose liquidity has not been taken yet."""
    out = []
    for s in swings(c, n):
        if s.kind != kind:
            continue
        later = c[s.i + 1:]
        taken = any(x.h > s.price for x in later) if kind == "H" else any(x.l < s.price for x in later)
        if not taken:
            out.append(s.price)
    return out


# ------------------------------------------------------------------------ sweep + MSS
@dataclass(frozen=True)
class MSS:
    direction: Dir
    swept_level: float   # liquidity that was taken (swing high for a short)
    sweep_price: float   # extreme of the sweep = structural invalidation
    sweep_i: int
    mss_level: float     # the swing that got broken (exact MSS price)
    mss_i: int
    displacement: bool


def _bearish_mss(c: list[Candle], n: int, lookback: int) -> MSS | None:
    if len(c) < 2 * n + 3:
        return None
    a = atr(c)
    sw = swings(c, n)
    highs = [s for s in sw if s.kind == "H"]
    lows = [s for s in sw if s.kind == "L"]
    start = max(0, len(c) - lookback)
    best: MSS | None = None
    for s in highs:
        # 1) sweep: wick through the swing high, close back below it
        j = next((k for k in range(s.i + n + 1, len(c)) if c[k].h > s.price), None)
        if j is None:
            continue
        if not (c[j].c < s.price or (j + 1 < len(c) and c[j + 1].c < s.price)):
            continue  # accepted above -> breakout, not a sweep
        # 2) the swing low that must break: last swing low formed before the sweep
        ref = [x for x in lows if x.i < j and x.i > s.i] or [x for x in lows if x.i < j]
        if not ref:
            continue
        level = ref[-1].price
        # 3) MSS: first close below that swing low, without accepting above the swept level
        k = None
        for m in range(j + 1, min(len(c), j + 1 + MSS_MAX_BARS)):
            if c[m].c > s.price and m > j + 1:
                break
            if c[m].c < level:
                k = m
                break
        if k is None or k < start:
            continue
        extreme = max(x.h for x in c[j:k + 1])
        if any(x.c > extreme for x in c[k + 1:]):
            continue  # structure fully reclaimed -> invalid
        disp = c[k].body >= a or (extreme - c[k].c) >= 2 * a
        cand = MSS("SHORT", s.price, extreme, j, level, k, disp)
        if best is None or cand.mss_i > best.mss_i:
            best = cand
    return best


def _flip(m: MSS, direction: Dir) -> MSS:
    return replace(m, direction=direction, swept_level=-m.swept_level,
                   sweep_price=-m.sweep_price, mss_level=-m.mss_level)


def find_mss(c: list[Candle], n: int = 2, lookback: int = 60) -> MSS | None:
    """Latest valid sweep -> market-structure-shift on *closed* candles."""
    bear = _bearish_mss(c, n, lookback)
    bull = _bearish_mss(_mirror(c), n, lookback)
    bull = _flip(bull, "LONG") if bull else None
    cands = [m for m in (bear, bull) if m]
    return max(cands, key=lambda m: m.mss_i) if cands else None


@dataclass(frozen=True)
class Retest:
    status: Literal["PENDING", "CONFIRMED", "TOO_FAR", "INVALID"]
    zone: tuple[float, float]


def check_retest(c: list[Candle], m: MSS, zone_atr: float = 0.3,
                 too_far_atr: float = 2.0) -> Retest:
    """After MSS: price must come back to the MSS level and fail (short) / hold (long)."""
    if m.direction == "LONG":
        r = check_retest(_mirror(c), _flip(m, "SHORT"), zone_atr, too_far_atr)
        return Retest(r.status, (-r.zone[1], -r.zone[0]))
    a = atr(c)
    lo, hi = m.mss_level, m.mss_level + zone_atr * a
    touched = confirmed = False
    for x in c[m.mss_i + 1:]:
        if x.c > m.sweep_price:
            return Retest("INVALID", (lo, hi))
        if x.h >= lo:
            touched = True
        if touched and x.c < lo:
            confirmed = True
    last = c[-1].c
    if last < lo - too_far_atr * a:
        return Retest("TOO_FAR", (lo, hi))
    if confirmed and last < hi:
        return Retest("CONFIRMED", (lo, hi))
    return Retest("PENDING", (lo, hi))


def recent_sweep(c: list[Candle], within: int = 6, n: int = 2) -> str | None:
    """Did the last ``within`` candles take a swing high/low and close back inside?"""
    sw = swings(c, n)
    recent = c[-within:]
    for s in reversed(sw):
        if s.i >= len(c) - within:
            continue
        if s.kind == "H" and any(x.h > s.price and x.c < s.price for x in recent):
            return f"sweep BSL {s.price:,.2f}"
        if s.kind == "L" and any(x.l < s.price and x.c > s.price for x in recent):
            return f"sweep SSL {s.price:,.2f}"
    return None


# --------------------------------------------------------------------------- 15M zones
@dataclass(frozen=True)
class Zone:
    lo: float
    hi: float
    kind: str

    def contains(self, price: float, pad: float = 0.0) -> bool:
        return self.lo - pad <= price <= self.hi + pad


def _supply_zones(c: list[Candle], n: int = 2) -> list[Zone]:
    a = atr(c)
    zones = []
    for i in range(2, len(c)):
        # bearish FVG not fully filled afterwards
        if c[i - 2].l > c[i].h and not any(x.h >= c[i - 2].l for x in c[i + 1:]):
            zones.append(Zone(c[i].h, c[i - 2].l, "FVG"))
        # bearish order block: last up candle before a displacement down candle
        if c[i].o - c[i].c >= a and c[i - 1].c > c[i - 1].o:
            zones.append(Zone(c[i - 1].l, c[i - 1].h, "OB"))
    for s in swings(c, n):
        if s.kind == "H":
            zones.append(Zone(s.price - 0.1 * a, s.price, "BSL"))
    return zones


def zones(c: list[Candle], direction: Dir) -> list[Zone]:
    """Supply zones (for shorts) or demand zones (for longs)."""
    if direction == "SHORT":
        return _supply_zones(c)
    return [Zone(-z.hi, -z.lo, "SSL" if z.kind == "BSL" else z.kind)
            for z in _supply_zones(_mirror(c))]


# ----------------------------------------------------------------------------- momentum
MOMENTUM_LABELS = {0: "CHOP/YẾU", 1: "CHOP/YẾU", 2: "ARMED", 3: "VALID", 4: "STRONG",
                   5: "VERY STRONG"}


def momentum(c: list[Candle], direction: Dir, m: MSS | None = None,
             confirm: bool | None = None) -> tuple[int, dict[str, bool]]:
    a = atr(c) or 1e-9
    sign = 1 if direction == "LONG" else -1
    recent = c[-6:]
    moves = [sign * (x.c - x.o) for x in recent]

    run = best = 0
    for mv in moves:
        run = run + 1 if mv > 0 else 0
        best = max(best, run)
    last3, prev3 = c[-3:], c[-6:-3]
    accel = (bool(prev3) and sum(x.range for x in last3) > 1.2 * sum(x.range for x in prev3)
             and sign * (last3[-1].c - last3[0].o) > 0)
    if m is not None:
        expansion = sign * (c[-1].c - m.sweep_price) >= 2 * a
    else:
        expansion = sign * (c[-1].c - c[-5].o) >= 2 * a if len(c) >= 5 else False

    checks = {
        "displacement": any(mv >= a for mv in moves) or bool(m and m.displacement),
        "push 3-4 nến": best >= 3,
        "acceleration": accel,
        "2 ATR expansion": expansion,
        "market confirm": bool(confirm),
    }
    return sum(checks.values()), checks


# ------------------------------------------------------------------------------ analyze
@dataclass
class Report:
    symbol: str
    price: float
    decision: str = "WAIT"
    direction: Dir | None = None
    bias: str = ""
    context_1h: str = ""
    location: str = ""
    location_zone: Zone | None = None
    mss5: MSS | None = None
    mss1: MSS | None = None
    retest: Retest | None = None
    entry: float | None = None
    sl: float | None = None
    targets: list[float | None] = field(default_factory=lambda: [None, None, None, None])
    score: int = 0
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def render(self) -> str:
        def f(x):
            return "—" if x is None else f"{x:,.2f}"

        z = self.location_zone
        lines = [
            f"=== {self.symbol} @ {f(self.price)} ===",
            f"Bias          : {self.bias}",
            f"1H context    : {self.context_1h}",
            f"Retest zone   : {f'{f(z.lo)}–{f(z.hi)} ({z.kind} 15M)' if z else '—'}  {self.location}",
        ]
        if self.mss5:
            lines.append(f"MSS 5M        : {self.mss5.direction} — sweep {f(self.mss5.swept_level)}"
                         f", phá {f(self.mss5.mss_level)}")
        else:
            lines.append("MSS 5M        : chưa có")
        if self.mss1:
            lines.append(f"MSS 1M (exact): {self.mss1.direction} — phá {f(self.mss1.mss_level)}"
                         f" (sweep {f(self.mss1.sweep_price)})")
        else:
            lines.append("MSS 1M (exact): chưa có")
        if self.retest:
            r = self.retest
            lines.append(f"Retest sau MSS: {f(r.zone[0])}–{f(r.zone[1])} → {r.status}")
        lines += [
            f"Entry         : {f(self.entry)}",
            f"SL            : {f(self.sl)}  (structural invalidation)",
            "TP1/TP2/TP3   : " + " / ".join(f(t) for t in self.targets[:3]),
            f"Target xa     : {f(self.targets[3])}",
            f"Momentum      : {self.score}/5 {MOMENTUM_LABELS[self.score]}  ["
            + ", ".join(f"{k} {'✓' if v else '✗'}" for k, v in self.checks.items()) + "]",
            f"=> {self.decision}" + (f"  ({'; '.join(self.reasons)})" if self.reasons else ""),
        ]
        return "\n".join(lines)


def _targets(direction: Dir, entry: float, tiers: list[list[float]],
             min_dist: float) -> list[float | None]:
    """TP1..TP3 + runner: nearest unswept liquidity per tier, each beyond the previous."""
    sign = 1 if direction == "LONG" else -1
    out: list[float | None] = []
    prev = entry + sign * min_dist
    for k in range(len(tiers)):
        pick = None
        for tier in tiers[k:]:
            beyond = [x for x in tier if sign * (x - prev) > 0]
            if beyond:
                pick = min(beyond, key=lambda x: abs(x - prev))
                break
        out.append(pick)
        if pick is not None:
            prev = pick
    return out


def analyze(data: dict[str, list[Candle]], symbol: str = "",
            confirm_1m: list[Candle] | None = None, min_score: int = 3) -> Report:
    """Run the full framework. ``data`` maps timeframe -> candles (last = still forming)."""
    closed = {tf: c[:-1] for tf, c in data.items()}
    price = data["1m"][-1].c
    rep = Report(symbol=symbol, price=price)
    why = rep.reasons

    # 1) Daily / 4H context -------------------------------------------------------
    d, h4 = structure(closed["1d"]), structure(closed["4h"])
    pd4 = range_position(closed["4h"][-30:])
    zone_txt = "premium" if pd4 > 0.5 else "discount"
    if d == h4 and d != "RANGE":
        htf = d
    elif d != "RANGE" and h4 != "RANGE":
        htf = f"{d} (4H pullback {h4})"
    else:
        htf = d if d != "RANGE" else h4
    prev_day = closed["1d"][-1] if closed["1d"] else None
    rep.bias = f"D {d} | 4H {h4} | 4H {zone_txt} {pd4:.0%} → HTF {htf}"

    # 2) 1H confirm --------------------------------------------------------------
    h1 = closed["1h"]
    a1h = atr(h1)
    disp1h = any(x.body >= 1.5 * a1h for x in h1[-5:])
    rep.context_1h = (f"{structure(h1)}, {recent_sweep(h1) or 'chưa sweep'}, "
                      f"displacement {'✓' if disp1h else '✗'}")

    # 4) 5M sweep + MSS decides the scalp direction ---------------------------------
    m5 = find_mss(closed["5m"], lookback=36)
    rep.mss5 = m5
    if m5 is None:
        why.append("5M chưa sweep + MSS")
        return rep
    direction = m5.direction
    rep.direction = direction
    if htf.startswith("BULL" if direction == "SHORT" else "BEAR"):
        rep.bias += f" (scalp {direction} ngược HTF — chỉ trade nếu structure rõ)"

    # 3) 15M location ------------------------------------------------------------
    m15 = closed["15m"]
    a15 = atr(m15)
    pd15 = range_position(m15[-96:])
    edge = (lambda z: z.hi) if direction == "SHORT" else (lambda z: z.lo)
    hit = [z for z in zones(m15[-60:], direction)
           if (z.contains(m5.swept_level, 0.25 * a15) or z.contains(m5.sweep_price, 0.25 * a15))
           and not (z.kind in ("BSL", "SSL") and edge(z) == m5.sweep_price)]  # the sweep wick itself
    hit.sort(key=lambda z: (z.kind in ("BSL", "SSL"), abs(edge(z) - m5.swept_level)))
    rep.location_zone = hit[0] if hit else None
    good_side = pd15 > 0.6 if direction == "SHORT" else pd15 < 0.4
    rep.location = f"(15M range {pd15:.0%} {'premium' if pd15 > 0.5 else 'discount'})"
    if 0.4 <= pd15 <= 0.6 and not hit:
        why.append("giá ở giữa range")
    elif not hit and not good_side:
        why.append("chưa ở location 15M")

    sign = 1 if direction == "LONG" else -1
    age = len(closed["5m"]) - 1 - m5.mss_i
    if age > FRESH_5M_BARS:
        why.append(f"setup 5M đã cũ ({age * 5} phút sau MSS)")
    if sign * (price - m5.mss_level) > MAX_CHASE_ATR15 * a15:
        why.append(f"giá đã chạy quá xa location (> {MAX_CHASE_ATR15} ATR 15M từ MSS 5M)")

    # 5) 1M trigger: sweep -> MSS -> retest -----------------------------------------
    m1c = closed["1m"]
    m1 = find_mss(m1c, lookback=45)
    rep.mss1 = m1
    if m1 is None:
        why.append("1M chưa có sweep + MSS")
    elif m1.direction != direction:
        why.append("1M và 5M structure mâu thuẫn")
    else:
        rep.retest = check_retest(m1c + [data["1m"][-1]], m1)
        status = rep.retest.status
        if status == "PENDING":
            why.append(f"break {m1.mss_level:,.2f} nhưng chưa retest fail/hold")
        elif status == "TOO_FAR":
            why.append("giá đã chạy quá xa khỏi retest")
        elif status == "INVALID":
            why.append("structure bị phá ngược — setup invalid")

    # 6) momentum ----------------------------------------------------------------
    confirm = None
    if confirm_1m:
        cm = find_mss(confirm_1m[:-1], lookback=45)
        sign = 1 if direction == "LONG" else -1
        confirm = bool(cm and cm.direction == direction) or (
            len(confirm_1m) >= 6 and sign * (confirm_1m[-1].c - confirm_1m[-6].o) >= atr(confirm_1m))
    rep.score, rep.checks = momentum(data["1m"], direction, m1 if m1 and m1.direction == direction
                                     else None, confirm)
    if rep.score < min_score:
        why.append(f"momentum {rep.score}/5 {MOMENTUM_LABELS[rep.score]}")

    # 7-9) entry, structural SL, liquidity targets ------------------------------------
    a1 = atr(m1c)
    rep.entry = price
    sl_src = m1 if m1 and m1.direction == direction else m5
    if direction == "SHORT":
        since = max([x.h for x in m1c[sl_src.mss_i:]] or [sl_src.sweep_price]) \
            if sl_src is m1 else sl_src.sweep_price
        rep.sl = max(sl_src.sweep_price, since) + 0.1 * a1
        kind: Literal["H", "L"] = "L"
    else:
        since = min([x.l for x in m1c[sl_src.mss_i:]] or [sl_src.sweep_price]) \
            if sl_src is m1 else sl_src.sweep_price
        rep.sl = min(sl_src.sweep_price, since) - 0.1 * a1
        kind = "H"
    session = []
    if prev_day:
        session.append(prev_day.l if direction == "SHORT" else prev_day.h)
    tiers = [
        unswept_levels(m1c, kind) + unswept_levels(closed["5m"], kind),
        unswept_levels(m15, kind) + session,
        unswept_levels(h1, kind) + unswept_levels(closed["4h"], kind),
        unswept_levels(closed["1d"], kind),
    ]
    risk = abs(price - rep.sl)
    rep.targets = _targets(direction, price, tiers, max(0.25 * a1, TP1_MIN_R * risk))
    if rep.targets[0] is None:
        why.append(f"không có liquidity target ≥ {TP1_MIN_R}R phía trước")

    if not why:
        rep.decision = direction
    elif rep.score == 2 and len(why) == 1:
        rep.decision = "WAIT (ARMED)"
    return rep
