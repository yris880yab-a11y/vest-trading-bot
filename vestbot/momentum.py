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
  enter on the break of the pause high; stop under the pause.
"""
from __future__ import annotations

from dataclasses import dataclass, field

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
    max_per_candle: float = 1  # entries allowed in the same 5M candle
    # how to get in after the 5M impulse: fomo | close | pullback | breakout
    entry_mode: str = "fomo"
    close_max_retrace: float = 0.3  # close: skip if price gave back more of the candle body
    pb_min: float = 0.25  # pullback: retrace at least this share of the impulse body ...
    pb_max: float = 0.6   # ... but not more (deeper = reversal, not a pullback)
    cons_max: float = 0.4  # breakout: 1M pause no wider than this share of the impulse body

    @classmethod
    def from_cfg(cls, cfg) -> "MomoParams":
        """Read MOMO_* settings from the config's extra map (set in .env)."""
        values = {}
        for name in cls.__dataclass_fields__:
            raw = getattr(cfg, "momo", {}).get(name)
            if raw not in (None, ""):
                values[name] = str(raw).lower() if name == "entry_mode" else float(raw)
        return cls(**values)


# Gold moves about 1/7 as many points as Nasdaq on 5M, so its thresholds are scaled.
PRESETS = {
    "NQ": {},
    "GC": {"min_5m_move": 2.0, "min_1m_move": 1.2, "min_atr5": 1.2, "min_tp": 1.5,
           "max_tp": 7.0, "scalp_tp": 1.5, "min_sl": 1.0, "max_sl": 3.0, "sl_buffer": 0.3,
           "trail": 1.2, "fade_body": 1.0, "tick": 0.1},
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


def _finish(price: float, sl_raw: float, x: Candle, a5: float, speed: float,
            before: list[Candle], c15: list[Candle], p: MomoParams,
            why: list[str]) -> MomoSignal | None:
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
    score = sum(parts.values()) / len(parts)
    tp_dist = p.min_tp + (p.max_tp - p.min_tp) * score
    notes = [f"{k} {v:.0%}" for k, v in parts.items()]

    above = [lv for lv in unswept_levels(before + [x], "H") + unswept_levels(c15[:-1], "H")
             if lv > price]
    if above:
        room = min(above) - price - p.tick
        if room < p.min_tp:
            why.append(f"liquidity chặn ngay {min(above):,.2f} ({room:.1f} điểm)")
            return None
        if room < tp_dist:
            tp_dist = room
            notes.append(f"mục tiêu dừng trước liquidity {min(above):,.2f}")
    if tp_dist < p.min_rr * dist:
        why.append(f"lời/rủi ro thấp ({tp_dist:.1f}/{dist:.1f})")
        return None
    scalp = price + p.scalp_tp if tp_dist > p.scalp_tp * 1.5 else None
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

    raise ValueError(f"MOMO_ENTRY_MODE không hợp lệ: {mode!r} (fomo|close|pullback|breakout)")


def _flip(sig: MomoSignal) -> MomoSignal:
    neg = lambda x: None if x is None else -x  # noqa: E731
    return MomoSignal("SHORT", -sig.price, -sig.sl, -sig.tp, neg(sig.scalp_tp), sig.score,
                      sig.candle_key, sig.notes)


def momentum_signal(c1: list[Candle], c5: list[Candle], c15: list[Candle],
                    p: MomoParams = MomoParams()) -> tuple[MomoSignal | None, list[str]]:
    """Signal (or None) plus the reasons to wait. Last candle of each list = forming."""
    if len(c5) < 20 or len(c1) < 8:
        return None, ["chưa đủ dữ liệu"]
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
