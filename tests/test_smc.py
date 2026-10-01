from scenario import bar, short_setup_1m

from vestbot.smc import (
    TIMEFRAMES, _mirror, analyze, check_retest, find_mss, momentum, parse_candles, structure,
)


def all_tf(c):
    return {tf: c for tf in TIMEFRAMES}


def test_parse_candles():
    c = parse_candles([[1, "10", "12", "9", "11", "100"]])[0]
    assert (c.o, c.h, c.l, c.c) == (10, 12, 9, 11)
    c = parse_candles({"data": [{"openTime": 1, "open": 1, "high": 3, "low": 0, "close": 2}]})[0]
    assert c.h == 3


def test_sweep_then_bearish_mss():
    m = find_mss(short_setup_1m()[:-1])
    assert m.direction == "SHORT"
    assert m.swept_level == 30601 and m.sweep_price == 30612
    assert m.mss_level == 30574  # exact MSS price, not "wait for MSS"
    assert m.displacement


def test_mirror_gives_bullish_mss():
    m = find_mss(_mirror(short_setup_1m()[:-1]))
    assert m.direction == "LONG"
    assert m.sweep_price == -30612 and m.mss_level == -30574


def test_retest_states():
    m = find_mss(short_setup_1m()[:-1])
    assert check_retest(short_setup_1m(push=(30560,)), m).status == "CONFIRMED"
    assert check_retest(short_setup_1m(), m).status == "TOO_FAR"
    assert check_retest(short_setup_1m(push=(), retest=False), m).status == "PENDING"
    reclaimed = short_setup_1m(push=(30560,))
    reclaimed.append(bar(len(reclaimed), 30560, 30620))  # close above the sweep high
    assert check_retest(reclaimed, m).status == "INVALID"


def test_momentum_score():
    c = short_setup_1m(push=(30560,))
    score, checks = momentum(c, "SHORT", find_mss(c[:-1]), confirm=True)
    assert checks["market confirm"] and checks["displacement"]
    assert 3 <= score <= 5


def test_structure_bull():
    assert structure(short_setup_1m()[:20]) == "BULL"


def test_full_short_setup():
    rep = analyze(all_tf(short_setup_1m(push=(30560,))), "NQ-PERP")
    assert rep.decision == "SHORT", rep.render()
    assert rep.mss1.mss_level == 30574
    assert rep.sl > 30612  # above the sweep high = structural invalidation
    tp1, tp2, tp3, _ = rep.targets
    assert rep.entry > tp1 > tp2 > tp3
    text = rep.render()
    for label in ("Bias", "Retest zone", "MSS 1M", "Entry", "SL", "TP1/TP2/TP3", "Momentum"):
        assert label in text


def test_full_long_setup_is_mirror():
    c = short_setup_1m(push=(30560,))
    flipped = [bar(x.t, 70000 - x.o, 70000 - x.c, h=70000 - x.l, l=70000 - x.h) for x in c]
    rep = analyze(all_tf(flipped), "GC-PERP")
    assert rep.decision == "LONG", rep.render()
    assert rep.sl < 70000 - 30612
    assert rep.entry < rep.targets[0]


def test_wait_reasons():
    rep = analyze(all_tf(short_setup_1m(push=(), retest=False)))
    assert rep.decision.startswith("WAIT")
    assert any("chưa retest" in r for r in rep.reasons)
    rep = analyze(all_tf(short_setup_1m()))
    assert any("quá xa" in r for r in rep.reasons)
    rep = analyze(all_tf(short_setup_1m(push=(30560,))), min_score=5)
    assert any("momentum" in r for r in rep.reasons)


def test_tp1_is_at_least_half_r():
    rep = analyze(all_tf(short_setup_1m(push=(30560,))))
    assert abs(rep.entry - rep.targets[0]) >= 0.5 * abs(rep.entry - rep.sl)


def test_no_chasing_far_from_5m_mss(monkeypatch):
    import vestbot.smc as smc
    monkeypatch.setattr(smc, "MAX_CHASE_ATR15", 0.0)
    rep = analyze(all_tf(short_setup_1m(push=(30560,))))
    assert rep.decision.startswith("WAIT")
    assert any("quá xa location" in r for r in rep.reasons)


def test_stale_5m_setup_waits(monkeypatch):
    import vestbot.smc as smc
    monkeypatch.setattr(smc, "FRESH_5M_BARS", 0)
    rep = analyze(all_tf(short_setup_1m(push=(30560,))))
    assert any("đã cũ" in r for r in rep.reasons)
