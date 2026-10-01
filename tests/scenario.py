"""Synthetic candles for a textbook short: sweep high -> bearish MSS -> retest fail."""
from vestbot.smc import Candle


def bar(i, o, c, wick=1.0, h=None, l=None):
    return Candle(i, o, max(o, c) + wick if h is None else h, min(o, c) - wick if l is None else l, c)


def short_setup_1m(push=(30560, 30550, 30538, 30525), retest=True):
    c, p = [], 30500.0
    # grind up with small pullbacks -> swing low ~30560, swing high ~30600
    for k in range(20):
        step = (6, 6, -5, -5)[k % 4]
        c.append(bar(len(c), p, p + step)); p += step
    # p ~ 30556..; build a swing high at 30600
    for target in (30525, 30545, 30565, 30585, 30600, 30590, 30580):  # high 30601
        c.append(bar(len(c), p, target)); p = target
    for target in (30575, 30585, 30595):                   # swing low ~30574 (30575-1)
        c.append(bar(len(c), p, target)); p = target
    c.append(bar(len(c), p, 30598, h=30612))               # sweep: wick 30612 > 30601, close below
    p = 30598
    for target in (30590, 30580, 30565):                   # displacement down, close < swing low
        c.append(bar(len(c), p, target, wick=0.5)); p = target
    if retest:
        c.append(bar(len(c), p, 30570, h=30577))           # retest into MSS level, close below
        p = 30570
    for target in push:                                    # sustained push lower
        c.append(bar(len(c), p, target, wick=0.5)); p = target
    c.append(bar(len(c), p, p - 2))                        # forming candle
    return c
