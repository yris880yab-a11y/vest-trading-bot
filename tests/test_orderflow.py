import json

from vestbot.flow_signal import handle, replay, run
from vestbot.orderflow import (BUY, DOM_ASK, DOM_BID, DOM_RESET, SELL, FlowEngine, FlowParams,
                               OrderBook)

T = 0.25


def book(eng, mid=20000.0, size=10, ts=0.0, bid_wall=None, ask_wall=None):
    """20 levels each side of ``mid``; optional (price, size) walls."""
    for i in range(1, 41):
        eng.on_depth(ts, DOM_BID, mid - i * T, size)
        eng.on_depth(ts, DOM_ASK, mid + (i - 1) * T + T, size)
    for wall, dom in ((bid_wall, DOM_BID), (ask_wall, DOM_ASK)):
        if wall:
            eng.on_depth(ts, dom, wall[0], wall[1])


def test_book_drops_crossed_levels_and_resets():
    b = OrderBook()
    b.apply(DOM_ASK, 100.25, 5)
    b.apply(DOM_BID, 100.0, 5)
    b.apply(DOM_BID, 100.25, 7)  # bid moved up through the old ask
    assert b.best_bid == 100.25 and b.best_ask is None
    b.apply(DOM_BID, 100.25, 0)
    assert b.best_bid == 100.0
    b.apply(DOM_RESET, 0, 0)
    assert not b.bids and not b.asks


def test_same_ms_prints_make_one_bubble():
    eng = FlowEngine(FlowParams(big_trade=30))
    book(eng)
    for _ in range(3):
        eng.on_trade(1.0, 20000.0, 12, BUY)
    assert len(eng.bubbles) == 1 and eng.bubbles[0][2] == 36


def absorbed_long(eng, level=19990.0):
    """Sellers hit ``level`` for 20s (200 lots), price holds and bounces 5 ticks."""
    book(eng, mid=19992.0, bid_wall=(level, 400), ts=0.0)
    eng.on_trade(1.0, 19992.0, 1, BUY)
    eng.evaluate(1.0)  # wall first seen
    sig = None
    for i in range(20):
        sig = sig or eng.on_trade(15.0 + i, level, 10, SELL)
    for i, px in enumerate((level + T, level + 2 * T, level + 4 * T, level + 5 * T)):
        sig = sig or eng.on_trade(36.0 + i, px, 5, BUY)
    return sig


def test_absorption_at_bid_wall_gives_long():
    sig = absorbed_long(FlowEngine())
    assert sig is not None
    assert sig.direction == "LONG" and sig.level == 19990.0
    assert sig.sl < 19990.0 < sig.entry < sig.tp
    assert sig.score >= 3


def test_no_long_when_price_breaks_the_level():
    eng = FlowEngine()
    book(eng, mid=19992.0, ts=0.0)
    sig = None
    for i in range(30):  # steady selling, every print lower: no level holds
        sig = sig or eng.on_trade(10.0 + i, 19992.0 - i * T, 10, SELL)
    assert sig is None or sig.direction != "LONG"


def test_tp_stops_before_ask_wall_and_skips_when_too_close():
    eng = FlowEngine()
    book(eng, mid=19992.0, bid_wall=(19990.0, 400), ask_wall=(19996.0, 400), ts=0.0)
    eng.on_trade(1.0, 19992.0, 1, BUY)
    eng.evaluate(1.0)
    sig = None
    for i in range(20):
        sig = sig or eng.on_trade(15.0 + i, 19990.0, 10, SELL)
    for i, px in enumerate((19990.25, 19990.5, 19991.0, 19991.25)):
        sig = sig or eng.on_trade(36.0 + i, px, 5, BUY)
    assert sig is not None and sig.tp == 19995.75

    eng = FlowEngine()  # wall 1 point above entry: not enough room
    book(eng, mid=19992.0, bid_wall=(19990.0, 400), ask_wall=(19992.25, 400), ts=0.0)
    eng.on_trade(1.0, 19992.0, 1, BUY)
    eng.evaluate(1.0)
    sig = None
    for i in range(20):
        sig = sig or eng.on_trade(15.0 + i, 19990.0, 10, SELL)
    for i, px in enumerate((19990.25, 19990.5, 19991.0, 19991.25)):
        sig = sig or eng.on_trade(36.0 + i, px, 5, BUY)
    assert sig is None


def test_buyers_absorbed_at_ask_wall_give_short():
    eng = FlowEngine()
    book(eng, mid=20008.0, ask_wall=(20010.0, 400), ts=0.0)
    eng.on_trade(1.0, 20008.0, 1, SELL)
    eng.evaluate(1.0)
    sig = None
    for i in range(20):
        sig = sig or eng.on_trade(15.0 + i, 20010.0, 10, BUY)
    for i, px in enumerate((20009.75, 20009.25, 20009.0, 20008.75)):
        sig = sig or eng.on_trade(36.0 + i, px, 5, SELL)
    assert sig is not None and sig.direction == "SHORT"
    assert sig.tp < sig.entry < 20010.0 < sig.sl


def test_eaten_ask_wall_gives_breakout_long_and_pulled_wall_is_logged():
    eng = FlowEngine(FlowParams(min_score=3))
    book(eng, mid=20000.0, ask_wall=(20002.0, 200), bid_wall=(19995.0, 200), ts=0.0)
    eng.on_trade(1.0, 20000.0, 1, BUY)
    eng.evaluate(1.0)
    eng.on_depth(20.0, DOM_BID, 19995.0, 10)  # bid wall pulled, never traded
    sig = None
    for i in range(14):  # buyers lift the ask wall
        sig = sig or eng.on_trade(20.0 + i, 20002.0, 10, BUY)
        eng.on_depth(20.0 + i, DOM_ASK, 20002.0, max(0, 200 - 15 * (i + 1)))
    eng.on_depth(34.0, DOM_ASK, 20002.0, 0)
    sig = sig or eng.on_trade(35.0, 20002.5, 40, BUY)
    assert any("PULLED" in e for e in eng.events)
    assert any("EATEN" in e for e in eng.events)
    assert sig is not None and sig.direction == "LONG" and sig.kind == "wall_eaten"


def test_cooldown_blocks_repeat_signal():
    eng = FlowEngine()
    assert absorbed_long(eng) is not None
    assert eng.on_trade(41.0, 19991.5, 5, BUY) is None


def test_params_from_env_style_dict():
    p = FlowParams.from_dict({"big_trade": "20", "rr": "1.5", "unknown": "x"})
    assert p.big_trade == 20 and isinstance(p.big_trade, int) and p.rr == 1.5


def test_replay_runs_projectx_payloads(tmp_path):
    rows = [{"k": "depth", "d": [{"timestamp": "2026-10-15T13:00:00Z", "type": 2,
                                  "price": 19990.0, "volume": 10}]},
            {"k": "trade", "d": [{"timestamp": "2026-10-15T13:00:01.500Z", "price": 19990.25,
                                  "volume": 3, "type": 0}]}]
    path = tmp_path / "flow.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows))
    eng, got = FlowEngine(), []
    run(eng, replay(str(path)), got.append, status_every=0)
    assert eng.book.best_bid == 19990.0 and eng.cvd == 3
    assert handle(eng, "trade", {"timestamp": 1.0, "price": 1, "volume": 1}) == []


def test_market_hub_handshake_and_dispatch(monkeypatch):
    import sys
    import types

    from vestbot.flow_signal import RS, MarketHub

    sent = []
    frames = [json.dumps({}) + RS,  # handshake ack
              json.dumps({"type": 1, "target": "GatewayDepth",
                          "arguments": ["CON.F.US.MNQ.Z26", [{"type": 2, "price": 1.0, "volume": 5}]]})
              + RS + json.dumps({"type": 6}) + RS,
              json.dumps({"type": 7, "error": "bye"}) + RS]

    class WS:
        def send(self, m): sent.append(m)
        def recv(self): return frames.pop(0)
        def close(self): pass

    fake = types.SimpleNamespace(create_connection=lambda url, timeout: WS(),
                                 WebSocketTimeoutException=TimeoutError)
    monkeypatch.setitem(sys.modules, "websocket", fake)
    hub = MarketHub("wss://x/hubs/market", "tok", "CON.F.US.MNQ.Z26")
    got = []
    try:
        for msg in hub.messages():
            got.append(msg)
    except ConnectionError as e:
        assert "bye" in str(e)
    assert got == [("depth", [{"type": 2, "price": 1.0, "volume": 5}])]
    assert '"protocol": "json"' in sent[0]
    assert any("SubscribeContractMarketDepth" in m for m in sent)
    assert any("SubscribeContractTrades" in m for m in sent)


def test_paper_tracker_scores_tp_sl_and_time():
    from vestbot.orderflow import FlowSignal, PaperTracker

    p = PaperTracker(max_hold=60)
    p.on_signal(FlowSignal(0, "LONG", "absorption", 99, 100.0, 98.0, 104.0, 3))
    p.on_signal(FlowSignal(0, "SHORT", "wall_bounce", 101, 100.0, 102.0, 96.0, 3))
    short = p.on_price(1, 104.0)              # SHORT stopped; LONG TP needs a tick through
    assert [t.outcome for t in short] == ["SL"] and short[0].exit == 102.25 and short[0].r < -1
    done = p.on_price(2, 104.25)
    assert [t.outcome for t in done] == ["TP"]
    assert round(done[0].r, 2) == round(3.75 / 2.25, 2)   # 1 tick slip on entry
    p.on_signal(FlowSignal(10, "LONG", "absorption", 99, 100.0, 98.0, 110.0, 3))
    assert p.on_price(80, 101.0)[0].outcome == "TIME"
    assert "3 lệnh" in p.summary()
