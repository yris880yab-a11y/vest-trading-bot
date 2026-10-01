import pytest

from vestbot.strategy import (
    Position, crossover_signal, ema, exit_reason, parse_closes, slippage_price,
)


def test_ema_basic():
    assert ema([1, 2, 3], 3) == [2.0]
    assert ema([1, 2], 3) == []
    out = ema([1, 2, 3, 4], 3)
    assert out[0] == 2.0 and out[1] == pytest.approx(3.0)


def test_crossover_long_and_short():
    # downtrend then sharp rally -> fast crosses above slow on the last closed candle
    down = [100 - i for i in range(30)]
    closes = down + [200, 999]  # 999 = still-forming candle, ignored
    assert crossover_signal(closes, 3, 10) == "LONG"
    up = [100 + i for i in range(30)]
    assert crossover_signal(up + [0, 999], 3, 10) == "SHORT"
    assert crossover_signal(up + [131, 999], 3, 10) == "HOLD"


def test_crossover_not_enough_data():
    assert crossover_signal([1, 2, 3], 3, 10) == "HOLD"


def test_exit_reason():
    long = Position("LONG", 1, 100)
    assert exit_reason(long, 98, 1.5, 3) .startswith("stop-loss")
    assert exit_reason(long, 104, 1.5, 3).startswith("take-profit")
    assert exit_reason(long, 101, 1.5, 3) is None
    short = Position("SHORT", 1, 100)
    assert exit_reason(short, 102, 1.5, 3).startswith("stop-loss")
    assert exit_reason(short, 96, 1.5, 3).startswith("take-profit")


def test_parse_closes_formats():
    assert parse_closes([[0, "1", "2", "0.5", "1.5", "10"]]) == [1.5]
    assert parse_closes({"data": [{"close": "3"}]}) == [3.0]


def test_slippage_price():
    assert slippage_price(100, True, 0.5) == "100.50"
    assert slippage_price(100, False, 0.5) == "99.50"
