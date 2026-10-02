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

    @classmethod
    def from_cfg(cls, cfg) -> "MomoParams":
        """Read MOMO_* settings from the config's extra map (set in .env)."""
        values = {}
        for name, f in cls.__dataclass_fields__.items():
            raw = getattr(cfg, "momo", {}).get(name)
            if raw not in (None, ""):
                values[name] = float(raw)
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


def _long_signal(c1: list[Candle], c5: list[Candle], c15: list[Candle],
                 p: MomoParams, why: list[str]) -> MomoSignal | None:
    f5, closed5 = c5[-1], c5[:-1]
    a5 = atr(closed5)
    body = f5.c - f5.o
    if a5 < p.min_atr5:
        why.append(f"thị trường chậm (ATR5 {a5:.1f} < {p.min_atr5:g})")
        return None
    if body < p.min_5m_move or body < p.min_atr_mult * a5:
        why.append(f"nến 5M chưa đủ nhanh ({body:+.1f} điểm, cần {max(p.min_5m_move, p.min_atr_mult * a5):.1f})")
        return None
    rng = f5.h - f5.l
    if rng and (f5.h - f5.c) / rng > p.max_wick:
        why.append("nến 5M đã bị đạp lại (râu dài)")
        return None
    m1a, m1b = c1[-2], c1[-1]
    push = (m1a.c - m1a.o) + (m1b.c - m1b.o)
    if m1a.c <= m1a.o or m1b.c < m1b.o or push < p.min_1m_move:
        why.append(f"1M không còn đẩy ({push:+.1f} điểm)")
        return None
    ext = f5.c - min(x.o for x in c5[-3:])
    if ext > p.max_ext_atr * a5:
        why.append(f"đã chạy quá xa ({ext:.0f} điểm > {p.max_ext_atr:g} ATR)")
        return None

    price = f5.c
    low = min(m1a.l, m1b.l) - p.sl_buffer
    dist = price - low
    if dist > p.max_sl:
        why.append(f"SL cần {dist:.1f} điểm > {p.max_sl:g}: vào quá trễ")
        return None
    dist = max(dist, p.min_sl)

    run = 0
    for x in reversed(closed5[-3:]):
        if x.c > x.o:
            run += 1
        else:
            break
    trend = _ema([x.c for x in c15[:-1]], 20) if len(c15) > 21 else None
    parts = {
        "nến/ATR": _clamp((body / a5 - p.min_atr_mult) / 1.5),
        "tốc độ 1M": _clamp((push - p.min_1m_move) / (2 * p.min_1m_move)),
        "chuỗi 5M": _clamp(run / 2),
        "xu hướng 15M": 1.0 if trend is not None and price > trend else 0.0,
    }
    score = sum(parts.values()) / len(parts)
    tp_dist = p.min_tp + (p.max_tp - p.min_tp) * score

    above = [x for x in unswept_levels(closed5, "H") + unswept_levels(c15[:-1], "H") if x > price]
    if above:
        room = min(above) - price - p.tick
        if room < p.min_tp:
            why.append(f"liquidity chặn ngay {min(above):,.2f} ({room:.1f} điểm)")
            return None
        if room < tp_dist:
            tp_dist = room
            parts_note = f"mục tiêu dừng trước liquidity {min(above):,.2f}"
        else:
            parts_note = ""
    else:
        parts_note = ""
    if tp_dist < p.min_rr * dist:
        why.append(f"lời/rủi ro thấp ({tp_dist:.1f}/{dist:.1f})")
        return None

    notes = [f"{k} {v:.0%}" for k, v in parts.items()]
    if parts_note:
        notes.append(parts_note)
    scalp = price + p.scalp_tp if tp_dist > p.scalp_tp * 1.5 else None
    return MomoSignal("LONG", price, price - dist, price + tp_dist, scalp, score, f5.t, notes)


def _flip(sig: MomoSignal) -> MomoSignal:
    neg = lambda x: None if x is None else -x  # noqa: E731
    return MomoSignal("SHORT", -sig.price, -sig.sl, -sig.tp, neg(sig.scalp_tp), sig.score,
                      sig.candle_key, sig.notes)


def momentum_signal(c1: list[Candle], c5: list[Candle], c15: list[Candle],
                    p: MomoParams = MomoParams()) -> tuple[MomoSignal | None, list[str]]:
    """Signal (or None) plus the reasons to wait. Last candle of each list = forming."""
    if len(c5) < 20 or len(c1) < 3:
        return None, ["chưa đủ dữ liệu"]
    f5 = c5[-1]
    if f5.c >= f5.o:
        why: list[str] = []
        return _long_signal(c1, c5, c15, p, why), why
    why = []
    sig = _long_signal(_mirror(c1), _mirror(c5), _mirror(c15), p, why)
    return (_flip(sig) if sig else None), why
