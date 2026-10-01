"""Trading strategy: EMA crossover with stop-loss / take-profit.

Pure functions only, so they can be unit-tested and back-tested offline.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal

Signal = Literal["LONG", "SHORT", "HOLD"]


def ema(values: list[float], period: int) -> list[float]:
    if period <= 0:
        raise ValueError("period must be positive")
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    out = [sum(values[:period]) / period]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def parse_closes(klines: Any) -> list[float]:
    """Extract close prices from a Vest /klines response.

    Accepts Binance-style rows ``[openTime, open, high, low, close, ...]``
    or dicts with a ``close``/``c`` key, optionally wrapped in ``{"data": [...]}``.
    """
    rows: Iterable = klines.get("data", klines) if isinstance(klines, dict) else klines
    closes = []
    for row in rows:
        if isinstance(row, dict):
            closes.append(float(row.get("close", row.get("c"))))
        else:
            closes.append(float(row[4]))
    return closes


def last_closed_open_time(klines: Any) -> Any:
    """Open time of the last *closed* candle, used to act on each signal only once."""
    rows = klines.get("data", klines) if isinstance(klines, dict) else klines
    if len(rows) < 2:
        return None
    row = rows[-2]
    return row.get("openTime", row.get("t")) if isinstance(row, dict) else row[0]


def crossover_signal(closes: list[float], fast: int, slow: int) -> Signal:
    """Signal on the most recent *closed* candle crossing.

    The last element of ``closes`` is the still-forming candle, so it is ignored.
    """
    closed = closes[:-1]
    if fast >= slow:
        raise ValueError("fast EMA period must be smaller than slow EMA period")
    if len(closed) < slow + 1:
        return "HOLD"
    # Both series end on the same candle, so [-1] and [-2] line up.
    f, s = ema(closed, fast), ema(closed, slow)
    f_prev, f_now = f[-2], f[-1]
    s_prev, s_now = s[-2], s[-1]
    if f_prev <= s_prev and f_now > s_now:
        return "LONG"
    if f_prev >= s_prev and f_now < s_now:
        return "SHORT"
    return "HOLD"


@dataclass
class Position:
    side: Literal["LONG", "SHORT"]
    size: float
    entry_price: float


def exit_reason(pos: Position, price: float, stop_loss_pct: float,
                take_profit_pct: float) -> str | None:
    change = (price - pos.entry_price) / pos.entry_price * 100
    pnl_pct = change if pos.side == "LONG" else -change
    if pnl_pct <= -stop_loss_pct:
        return f"stop-loss ({pnl_pct:.2f}%)"
    if pnl_pct >= take_profit_pct:
        return f"take-profit ({pnl_pct:.2f}%)"
    return None


def slippage_price(price: float, is_buy: bool, slippage_pct: float, tick: int = 2) -> str:
    """Worst acceptable price for a market order."""
    factor = 1 + slippage_pct / 100 if is_buy else 1 - slippage_pct / 100
    return f"{price * factor:.{tick}f}"
