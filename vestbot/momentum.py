"""Momentum scalp: jump on a fast 5M candle, take 10-50 points depending on its strength.

Entry (long; short is the mirror):
* the forming 5M candle is up at least ``min_5m_move`` points and at least
  ``min_atr_mult`` x ATR(5M): it is expanding, not drifting;
* it closes near its high (wick on top <= ``max_wick``) and the last two 1M candles
  push up together by at least ``min_1m_move``: the move is happening now;
* the market is awake (ATR(5M) >= ``min_atr5``) and not already stretched
  (last three 5M candles <= ``max_ext_atr`` x ATR);
* there is room: the nearest untaken swing high on 5M/15M is far enough for the target.

Target = ``min_tp`` .. ``max_tp`` scaled by a 0-1 strength score (candle vs ATR, 1M speed,
5M follow-through, 15M trend), capped just before that liquidity.
Stop = under the last two 1M lows, between ``min_sl`` and ``max_sl`` (wider = too late, skip).

``entry_mode`` chooses how to get in once a 5M impulse candle exists:
* ``fomo``: at market while the impulse candle is still forming (the rules above);
* ``close``: after the impulse candle closes, if price has not given back > ``close_max_retrace``;
* ``pullback``: wait for a 1M pullback of ``pb_min``-``pb_max`` of the impulse, enter when a
  1M candle breaks back above the pullback candle; stop under the pullback low;
* ``breakout``: wait for a tight 1M pause (<= ``cons_max`` of the impulse) in the top half,
  enter on the break of the pause high; stop under the pause;
* ``sweep``: a 1M candle takes out the lowest low of the last ``sw_lookback`` candles by at
  most ``sw_max_depth``, price holds back above that low, then breaks the high of the
  ``sw_mss_bars`` candles before the sweep (market structure shift); enter on the break,
  stop under the sweep low;
* ``rejection``: no impulse needed. A 1M candle wicks into a high-volume level of the recent
  volume profile (``vp_*``), closes back above it, and price runs ``fast_move`` points away
  from the wick low within a few minutes; stop under the wick.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .smc import Candle, _mirror, atr, unswept_levels


@dataclass(frozen=True)
class MomoParams:
    min_5m_move: float = 15.0
    min_atr_mult: float = 0.8
    max_wick: float = 0.3
    min_1m_move: float = 8.0
    min_atr5: float = 8.0
    max_ext_atr: float = 4.0
    min_tp: float = 10.0
    max_tp: float = 50.0
    scalp_tp: float = 10.0
    min_sl: float = 6.0
    max_sl: float = 20.0
    sl_buffer: float = 2.0
    min_rr: float = 1.0
    trail: float = 8.0
    fade_body: float = 6.0
    max_hold_min: float = 15.0
    tick: float = 0.25
    max_per_candle: float = 1  # entries allowed in the same 5M candle (0 = no limit)
    # how to get in after the 5M impulse: fomo | close | pullback | breakout
    entry_mode: str = "fomo"
    close_max_retrace: float = 0.3  # close: skip if price gave back more of the candle body
    pb_min: float = 0.25  # pullback: retrace at least this share of the impulse body ...
    pb_max: float = 0.6   # ... but not more (deeper = reversal, not a pullback)
    cons_max: float = 0.4  # breakout: 1M pause no wider than this share of the impulse body
    trend_filter: float = 0  # 1 = only trade with the 15M EMA20
    scalp_r: float = 0  # >0: take half at this many R instead of scalp_tp points
    trail_r: float = 0  # >0: trail this many R behind the best price instead of trail points
    sessions: str = ""  # UTC hours to open trades, e.g. "7-11,13-17"; empty = always
    # New York times with no trading (news, cash open), e.g. "08:28-08:40,09:28-09:35";
    # an open trade is closed when a window starts
    blackout: str = ""
    # rejection: 1M wick off a high-volume level, then a fast move away
    vp_bin: float = 5.0        # price bucket for the volume profile (points)
    vp_lookback: float = 480   # minutes of 1M bars in the profile
    vp_pct: float = 0.8        # a level must be in the top 20% of buckets by volume
    zone_w: float = 4.0        # how close the wick must get to the level (points)
    wick_ratio: float = 0.5    # rejection wick >= this share of the 1M candle
    vol_mult: float = 0.0      # >0: rejection candle volume >= this x average 1M volume
    fast_move: float = 6.0     # points away from the wick low that confirm the rejection
    bounce_vol_mult: float = 0.0  # >0: candles after the wick trade >= this x average 1M volume
    active_vol_mult: float = 0.0  # >0: last 30 min of volume >= this x the baseline average
    active_vol_base: float = 0.0  # baseline length in minutes (0 = vp_lookback); 1440 = 24h
    rej_swings: float = 0      # 1 = 1M swing highs/lows (old support/resistance) are levels too
    swing_lookback: float = 120  # minutes of 1M candles searched for those swings
    # sweep: 1M takes out the recent low, holds back above it, then breaks the small high
    sw_lookback: float = 20    # 1M candles that set the low to be swept
    sw_window: float = 3       # the sweep happened within this many 1M candles
    sw_max_depth: float = 10.0  # deeper than this below the low = breakdown, not a sweep
    sw_mss_bars: float = 2     # 1M candles before the sweep whose high must break
    sw_max_sl: float = 25.0    # the stop sits under the sweep, so allow a wider one
    sw_max_chase: float = 0.5  # skip if price ran past the break by more than this x range
    sw_room: float = 1         # 1 = stop the target before nearby highs, 0 = ignore them
    sw_tp_r: float = 1.5       # target = this many times the stop distance (0 = strength score)

    @classmethod
    def from_cfg(cls, cfg) -> "MomoParams":
        """Read MOMO_* settings from the config's extra map (set in .env)."""
        values = {}
        for name in cls.__dataclass_fields__:
            raw = getattr(cfg, "momo", {}).get(name)
            if raw not in (None, ""):
                values[name] = (str(raw).lower() if name in STR_PARAMS
                                else float(raw))
        return cls(**values)


STR_PARAMS = ("entry_mode", "sessions", "blackout")


# Gold moves about 1/7 as many points as Nasdaq on 5M, so its thresholds are scaled.
PRESETS = {
    "NQ": {},
    "GC": {"min_5m_move": 2.0, "min_1m_move": 1.2, "min_atr5": 1.2, "min_tp": 1.5,
           "max_tp": 7.0, "scalp_tp": 1.5, "min_sl": 1.0, "max_sl": 3.0, "sl_buffer": 0.3,
           "trail": 1.2, "fade_body": 1.0, "tick": 0.1, "sw_max_depth": 1.5, "sw_max_sl": 3.5,
           "vp_bin": 0.5, "zone_w": 0.4, "fast_move": 0.8},
}


@dataclass
class MomoSignal:
    direction: str
    price: float
    sl: float
    tp: float
    scalp_tp: float | None
    score: float
    candle_key: object
    notes: list[str] = field(default_factory=list)

    def render(self, symbol: str = "") -> str:
        f = lambda x: "—" if x is None else f"{x:,.2f}"  # noqa: E731
        return (f"=== MOMENTUM {self.direction} {symbol} @ {f(self.price)} ===\n"
                f"Độ mạnh: {self.score:.0%}  ({'; '.join(self.notes)})\n"
                f"SL: {f(self.sl)} ({abs(self.price - self.sl):.2f} điểm)\n"
                f"Chốt 1/2: {f(self.scalp_tp)} | Mục tiêu: {f(self.tp)}"
                f" ({abs(self.tp - self.price):.2f} điểm)")


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def _ema(values: list[float], n: int) -> float:
    k, e = 2 / (n + 1), values[0]
    for v in values[1:]:
        e = v * k + e * (1 - k)
    return e


def _impulse(x: Candle, before: list[Candle], p: MomoParams, why: list[str]) -> float | None:
    """ATR(5M) if ``x`` is a valid bullish impulse candle, else None (reason appended)."""
    a5 = atr(before)
    body = x.c - x.o
    if a5 < p.min_atr5:
        why.append(f"thị trường chậm (ATR5 {a5:.1f} < {p.min_atr5:g})")
        return None
    if body < p.min_5m_move or body < p.min_atr_mult * a5:
        why.append(f"nến 5M chưa đủ nhanh ({body:+.1f} điểm, cần {max(p.min_5m_move, p.min_atr_mult * a5):.1f})")
        return None
    rng = x.h - x.l
    if rng and (x.h - x.c) / rng > p.max_wick:
        why.append("nến 5M đã bị đạp lại (râu dài)")
        return None
    ext = x.c - min(c.o for c in (before[-2:] + [x]))
    if ext > p.max_ext_atr * a5:
        why.append(f"đã chạy quá xa ({ext:.0f} điểm > {p.max_ext_atr:g} ATR)")
        return None
    return a5


def swing_levels(c1: list[Candle], n: int = 2) -> list[float]:
    """Confirmed 1M swing lows and highs (n candles each side): support/resistance that
    price often respects again after breaking it."""
    out = []
    for i in range(n, len(c1) - n):
        side = c1[i - n:i + n + 1]
        if c1[i].l <= min(c.l for c in side):
            out.append(c1[i].l)
        if c1[i].h >= max(c.h for c in side):
            out.append(c1[i].h)
    return out


def volume_levels(c1: list[Candle], bin_size: float, pct: float) -> list[float]:
    """High-volume price levels: local peaks of the volume profile in its top ``1 - pct``."""
    if not c1 or bin_size <= 0 or not any(x.v for x in c1):
        return []
    lo = min(x.l for x in c1)
    vol: dict[int, float] = {}
    for x in c1:
        a, b = int((x.l - lo) // bin_size), int((x.h - lo) // bin_size)
        share = x.v / (b - a + 1)
        for k in range(a, b + 1):
            vol[k] = vol.get(k, 0.0) + share
    ranked = sorted(vol.values())
    cut = ranked[min(len(ranked) - 1, int(len(ranked) * pct))]
    peaks = [k for k, v in vol.items()
             if v >= cut and v >= vol.get(k - 1, 0) and v >= vol.get(k + 1, 0)]
    return sorted(lo + (k + 0.5) * bin_size for k in peaks)


def _finish(price: float, sl_raw: float, x: Candle, a5: float, speed: float,
            before: list[Candle], c15: list[Candle], p: MomoParams,
            why: list[str], extra_above: list[float] | None = None,
            room_check: bool = True, tp_r: float = 0.0) -> MomoSignal | None:
    """Turn an entry into a signal: clamp the stop, score strength, size the target."""
    dist = price - sl_raw
    if dist > p.max_sl:
        why.append(f"SL cần {dist:.1f} điểm > {p.max_sl:g}: vào quá trễ")
        return None
    dist = max(dist, p.min_sl)
    run = 0
    for c in reversed(before[-3:]):
        if c.c > c.o:
            run += 1
        else:
            break
    trend = _ema([c.c for c in c15[:-1]], 20) if len(c15) > 21 else None
    parts = {
        "nến/ATR": _clamp(((x.c - x.o) / a5 - p.min_atr_mult) / 1.5),
        "tốc độ 1M": _clamp(speed),
        "chuỗi 5M": _clamp(run / 2),
        "xu hướng 15M": 1.0 if trend is not None and price > trend else 0.0,
    }
    if p.trend_filter and trend is not None and price <= trend:
        why.append("ngược xu hướng 15M (EMA20)")
        return None
    score = sum(parts.values()) / len(parts)
    tp_dist = p.min_tp + (p.max_tp - p.min_tp) * score
    if tp_r:  # target as a multiple of the stop distance, within min_tp..max_tp
        tp_dist = min(p.max_tp, max(p.min_tp, tp_r * dist))
    notes = [f"{k} {v:.0%}" for k, v in parts.items()]

    above = [lv for lv in unswept_levels(before + [x], "H") + unswept_levels(c15[:-1], "H")
             + (extra_above or []) if lv > price] if room_check else []
    if above:
        room = min(above) - price - p.tick
        if room < p.min_tp:
            why.append(f"liquidity chặn ngay {abs(min(above)):,.2f} ({room:.1f} điểm)")
            return None
        if room < tp_dist:
            tp_dist = room
            notes.append(f"mục tiêu dừng trước liquidity {abs(min(above)):,.2f}")
    if tp_dist < p.min_rr * dist:
        why.append(f"lời/rủi ro thấp ({tp_dist:.1f}/{dist:.1f})")
        return None
    first = p.scalp_r * dist if p.scalp_r else p.scalp_tp
    scalp = price + first if tp_dist > first * 1.5 else None
    notes.insert(0, f"vào kiểu {p.entry_mode}")
    return MomoSignal("LONG", price, price - dist, price + tp_dist, scalp, score, x.t, notes)


def _long_signal(c1: list[Candle], c5: list[Candle], c15: list[Candle],
                 p: MomoParams, why: list[str]) -> MomoSignal | None:
    price = c1[-1].c
    mode = p.entry_mode

    if mode == "fomo":
        x, before = c5[-1], c5[:-1]
        a5 = _impulse(x, before, p, why)
        if a5 is None:
            return None
        m1a, m1b = c1[-2], c1[-1]
        push = (m1a.c - m1a.o) + (m1b.c - m1b.o)
        if m1a.c <= m1a.o or m1b.c < m1b.o or push < p.min_1m_move:
            why.append(f"1M không còn đẩy ({push:+.1f} điểm)")
            return None
        speed = (push - p.min_1m_move) / (2 * p.min_1m_move)
        return _finish(price, min(m1a.l, m1b.l) - p.sl_buffer, x, a5, speed, before, c15, p, why)

    if mode == "rejection":
        a5 = atr(c5[:-1])
        if a5 < p.min_atr5:
            why.append(f"thị trường chậm (ATR5 {a5:.1f} < {p.min_atr5:g})")
            return None
        hist = c1[:-4][-int(p.vp_lookback):]
        levels = volume_levels(hist, p.vp_bin, p.vp_pct)
        if p.rej_swings:
            levels += swing_levels(c1[:-4][-int(p.swing_lookback):])
        if not levels:
            why.append("chưa có dữ liệu volume")
            return None
        avg_v = sum(x.v for x in hist[-30:]) / max(1, len(hist[-30:]))
        hit = None
        for x in c1[-4:-1]:  # the last three closed 1M candles
            rng = x.h - x.l
            if rng <= 0 or (min(x.o, x.c) - x.l) / rng < p.wick_ratio:
                continue
            if p.vol_mult and x.v < p.vol_mult * avg_v:
                continue
            near = [lv for lv in levels if lv - 2 * p.zone_w <= x.l <= lv + p.zone_w and x.c > lv]
            if near:
                hit = (x, max(near))
        if hit is None:
            why.append("chưa có râu 1M từ chối tại vùng volume")
            return None
        x, level = hit
        after = [c for c in c1[-4:-1] if c.t != x.t and c1.index(c) > c1.index(x)]
        if p.bounce_vol_mult and (not after or sum(c.v for c in after) / len(after)
                                  < p.bounce_vol_mult * avg_v):
            why.append("nhịp bật sau râu thiếu volume")
            return None
        low = min(c.l for c in c1[-4:])
        move = price - low
        if move < p.fast_move:
            why.append(f"chưa bật đủ nhanh ({move:.1f}/{p.fast_move:g} điểm từ râu)")
            return None
        if move > 2.5 * p.fast_move:
            why.append("đã bật quá xa khỏi râu")
            return None
        speed = move / p.fast_move - 1
        others = [lv for lv in levels if lv > price + p.zone_w]
        sig = _finish(price, low - p.sl_buffer, c5[-1], a5, speed, c5[:-1], c15, p, why, others)
        if sig:
            sig.notes.insert(1, f"vùng giá {abs(level):,.2f}")
        return sig

    if mode == "sweep":
        a5 = atr(c5[:-1])
        if a5 < p.min_atr5:
            why.append(f"thị trường chậm (ATR5 {a5:.1f} < {p.min_atr5:g})")
            return None
        w, n, m = int(p.sw_window), int(p.sw_lookback), int(p.sw_mss_bars)
        if len(c1) < w + n + 1:
            why.append("chưa đủ nến 1M")
            return None
        recent = c1[-w - 1:]                  # sweep window, forming candle included
        ref_low = min(c.l for c in c1[-w - 1 - n:-w - 1])
        k = min(range(len(recent)), key=lambda i: recent[i].l)
        swept = recent[k].l
        if not ref_low - p.sw_max_depth <= swept < ref_low:
            why.append("chưa quét đáy 1M" if swept >= ref_low else "quét quá sâu (gãy xuống)")
            return None
        if price <= ref_low:
            why.append(f"chưa giữ lại trên đáy bị quét {ref_low:,.2f}")
            return None
        pos = len(c1) - len(recent) + k       # index of the sweep candle
        mss = max(c.h for c in c1[max(0, pos - m):pos + 1])
        if price <= mss:
            why.append(f"chờ phá đỉnh {mss:,.2f} (MSS)")
            return None
        if price - mss > p.sw_max_chase * (mss - swept):
            why.append("đã chạy quá xa khỏi điểm phá")
            return None
        speed = (price - swept) / max(p.fast_move, 1e-9) / 3
        sig = _finish(price, swept - p.sl_buffer, c5[-1], a5, speed, c5[:-1], c15,
                      replace(p, max_sl=p.sw_max_sl), why, room_check=bool(p.sw_room), tp_r=p.sw_tp_r)
        if sig:
            sig.notes.insert(1, f"quét {abs(ref_low):,.2f} → {abs(swept):,.2f}, phá {abs(mss):,.2f}")
        return sig

    if mode == "close":
        x, before = c5[-2], c5[:-2]
        a5 = _impulse(x, before, p, why)
        if a5 is None:
            return None
        body = x.c - x.o
        if x.c - price > p.close_max_retrace * body:
            why.append(f"giá đã hồi quá {p.close_max_retrace:.0%} nến 5M")
            return None
        if price - x.c > 0.5 * body:
            why.append("giá đã chạy xa sau khi nến 5M đóng")
            return None
        low = min(c1[-2].l, c1[-1].l) - p.sl_buffer
        return _finish(price, low, x, a5, 0.5, before, c15, p, why)

    # pullback / breakout: the impulse is the forming or the last closed 5M candle
    for x, before in ((c5[-1], c5[:-1]), (c5[-2], c5[:-2])):
        sub: list[str] = []
        a5 = _impulse(x, before, p, sub)
        if a5 is not None:
            break
    else:
        why.extend(sub)
        return None
    body = x.c - x.o
    now, prev = c1[-1], c1[-2]

    if mode == "pullback":
        top = max(c.h for c in c1[-8:-1])
        low = min(c1[-3].l, prev.l)
        depth = top - low
        if prev.c >= prev.o:
            why.append("chưa có nến 1M hồi")
            return None
        if not p.pb_min * body <= depth <= p.pb_max * body or low <= x.o:
            why.append(f"nhịp hồi {depth:.1f} điểm ngoài vùng {p.pb_min:.0%}-{p.pb_max:.0%} nến 5M")
            return None
        if now.c <= prev.h:
            why.append(f"chờ nến 1M bật qua {prev.h:,.2f}")
            return None
        speed = (now.c - now.o) / max(p.min_1m_move, 1e-9)
        return _finish(price, low - p.sl_buffer, x, a5, speed, before, c15, p, why)

    if mode == "breakout":
        pause = c1[-4:-1]
        hi, lo = max(c.h for c in pause), min(c.l for c in pause)
        if hi - lo > p.cons_max * body:
            why.append(f"1M chưa đi ngang ({hi - lo:.1f} điểm > {p.cons_max:.0%} nến 5M)")
            return None
        if lo < x.o + 0.5 * body:
            why.append("nhịp đi ngang đã rơi xuống nửa dưới nến 5M")
            return None
        if now.c <= hi:
            why.append(f"chờ phá {hi:,.2f}")
            return None
        speed = (now.c - hi) / max(p.min_1m_move, 1e-9)
        return _finish(price, lo - p.sl_buffer, x, a5, speed, before, c15, p, why)

    raise ValueError(f"MOMO_ENTRY_MODE không hợp lệ: {mode!r}"
                     " (fomo|close|pullback|breakout|rejection|sweep)")


def _flip(sig: MomoSignal) -> MomoSignal:
    neg = lambda x: None if x is None else -x  # noqa: E731
    return MomoSignal("SHORT", -sig.price, -sig.sl, -sig.tp, neg(sig.scalp_tp), sig.score,
                      sig.candle_key, sig.notes)


def in_sessions(hour: int, sessions: str) -> bool:
    """True if ``hour`` (UTC) falls in a "7-11,13-17" style list; empty list = always."""
    if not sessions:
        return True
    for part in sessions.split(","):
        a, b = (int(x) for x in part.split("-"))
        if a <= hour < b if a <= b else (hour >= a or hour < b):
            return True
    return False


def in_blackout(now_ny, blackout: str) -> bool:
    """True if the New York wall time ``now_ny`` falls in a "08:28-08:40,09:28-09:35" list."""
    if not blackout:
        return False
    minute = now_ny.hour * 60 + now_ny.minute
    for part in blackout.split(","):
        a, b = ((int(h) * 60 + int(m)) for h, m in (x.split(":") for x in part.strip().split("-")))
        if a <= minute < b:
            return True
    return False


def momentum_signal(c1: list[Candle], c5: list[Candle], c15: list[Candle],
                    p: MomoParams = MomoParams()) -> tuple[MomoSignal | None, list[str]]:
    """Signal (or None) plus the reasons to wait. Last candle of each list = forming."""
    if len(c5) < 20 or len(c1) < 8:
        return None, ["chưa đủ dữ liệu"]
    if p.active_vol_mult:  # trade only while the market is busy
        hist = c1[:-1][-int(p.active_vol_base or p.vp_lookback):]
        recent = hist[-30:]
        base = sum(x.v for x in hist) / max(1, len(hist))
        if base and sum(x.v for x in recent) / max(1, len(recent)) < p.active_vol_mult * base:
            return None, ["thị trường ít volume (30 phút gần nhất thấp hơn trung bình)"]
    if "," in p.entry_mode:  # several entry modes: the first one with a signal wins
        reasons: list[str] = []
        for mode in p.entry_mode.split(","):
            sig, why = momentum_signal(c1, c5, c15, replace(p, entry_mode=mode.strip()))
            if sig:
                return sig, []
            reasons += why
        return None, list(dict.fromkeys(reasons))
    why_long: list[str] = []
    sig = _long_signal(c1, c5, c15, p, why_long)
    if sig:
        return sig, []
    why_short: list[str] = []
    sig = _long_signal(_mirror(c1), _mirror(c5), _mirror(c15), p, why_short)
    if sig:
        return _flip(sig), []
    if p.entry_mode == "fomo":
        f5 = c5[-1]
        return None, (why_long if f5.c >= f5.o else why_short)
    return None, list(dict.fromkeys(why_long + why_short))  # the impulse may be either side
