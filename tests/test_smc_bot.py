from scenario import bar, short_setup_1m
from test_bot import make_cfg

from vestbot.smc_bot import SMCBot


class FakeClient:
    def __init__(self, candles):
        self.candles = candles
        self.orders = []

    def klines(self, symbol, interval, limit):
        return [[x.t, x.o, x.h, x.l, x.c, 0] for x in self.candles]

    def place_order(self, **kw):
        self.orders.append(kw)
        return {"id": str(len(self.orders))}


def move_to(client, price):
    last = client.candles[-1]
    client.candles = client.candles[:-1] + [bar(last.t, last.o, price)]


def test_enters_once_then_scales_out_and_protects_profit():
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=False, order_size="1", size_decimals=2), client)
    bot.tick()
    t = bot.trade
    assert t and t.side == "SHORT" and len(client.orders) == 1
    assert client.orders[0]["is_buy"] is False and client.orders[0]["reduce_only"] is False

    bot.trade.targets = [30540.0, 30520.0, 30500.0, None]
    move_to(client, 30539)  # TP1
    bot.tick()
    assert client.orders[-1]["reduce_only"] and client.orders[-1]["size"] == "0.50"
    assert bot.trade.sl == bot.trade.entry  # break-even after TP1
    assert len([o for o in client.orders if not o["reduce_only"]]) == 1  # no add, no chase

    move_to(client, 30519)  # TP2
    bot.tick()
    assert client.orders[-1]["size"] == "0.25" and bot.trade.sl == 30540.0

    move_to(client, 30499)  # TP3, no runner target -> flat
    bot.tick()
    assert bot.trade is None and client.orders[-1]["size"] == "0.25"


def test_stop_loss_closes_everything_and_setup_not_retaken():
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=False, order_size="1", size_decimals=2), client)
    bot.tick()
    bot.trade.sl = 30565
    move_to(client, 30566)
    bot.tick()
    assert bot.trade is None and client.orders[-1]["size"] == "1.00"
    move_to(client, 30558)
    bot.tick()  # same sweep/MSS -> must not re-enter
    assert bot.trade is None and len(client.orders) == 2


def test_reclaim_trims_once_then_exits_runner():
    from vestbot.smc import MSS, Report
    from vestbot.smc_bot import Trade

    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=False, order_size="1", size_decimals=2), client)
    bot.trade = Trade("SHORT", 30558, 30613, [30500.0, None, None, None], 1.0, ("k",))
    up = MSS("LONG", 30540, 30535, 0, 30565, 1, True)
    rep = Report("X", 30562, mss5=None, mss1=up)

    bot.manage(rep)  # first reclaim before TP1 -> cut half, keep SL
    assert client.orders[-1]["size"] == "0.50" and bot.trade.remaining == 0.5
    bot.manage(rep)  # same MSS again -> nothing new
    assert len(client.orders) == 1
    rep.mss1 = MSS("LONG", 30545, 30540, 2, 30570, 3, True)
    bot.manage(rep)  # a fresh reclaim -> exit the rest
    assert bot.trade is None and client.orders[-1]["size"] == "0.50"


def test_opposite_5m_mss_exits_everything():
    from vestbot.smc import MSS, Report
    from vestbot.smc_bot import Trade

    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=False, order_size="1", size_decimals=2), client)
    bot.trade = Trade("SHORT", 30558, 30613, [30500.0, None, None, None], 1.0, ("k",))
    bot.manage(Report("X", 30562, mss5=MSS("LONG", 30540, 30535, 0, 30565, 1, True)))
    assert bot.trade is None and client.orders[-1]["size"] == "1.00"


def test_state_survives_restart(tmp_path):
    state = tmp_path / "state.json"
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=False, order_size="1", size_decimals=2,
                          state_file=str(state)), client)
    bot.tick()
    assert bot.trade and state.exists()

    again = SMCBot(make_cfg(dry_run=False, order_size="1", size_decimals=2,
                            state_file=str(state)), client)
    assert again.trade == bot.trade and again.last_setup == bot.last_setup
    assert again.day_trades == 1


def test_daily_loss_limit_blocks_new_entries():
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=False, order_size="1", size_decimals=2,
                          max_daily_loss_r=2), client)
    bot._roll_day()
    bot.day_r = -2.0
    bot.tick()
    assert bot.trade is None and client.orders == []
    bot.day_r, bot.day_trades = 0.0, bot.cfg.max_trades_per_day
    bot.tick()
    assert bot.trade is None and client.orders == []


def test_realized_r_is_tracked():
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=False, order_size="1", size_decimals=2), client)
    bot.tick()
    risk = bot.trade.risk
    move_to(client, bot.trade.entry + risk)  # short goes against us by exactly 1R -> SL area
    bot.trade.sl = bot.trade.entry + risk
    bot.tick()
    assert bot.trade is None and abs(bot.day_r + 1) < 0.05


def test_risk_based_size_makes_1r_a_fixed_dollar_amount():
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=False, size_decimals=4, risk_usd=50.0,
                          max_notional_usd=None), client)
    bot.tick()
    t = bot.trade
    assert abs(t.size * t.risk - 50.0) < 0.01  # losing at SL costs $50
    assert client.orders[0]["size"] == f"{t.size:.4f}"


def test_notional_cap_limits_size():
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=False, size_decimals=4, risk_usd=50.0,
                          max_notional_usd=10000.0), client)
    bot.tick()
    assert bot.trade.size * bot.trade.entry <= 10000.0


def funded_bot(client, **over):
    cfg = dict(dry_run=False, size_decimals=4, risk_usd=25.0, max_notional_usd=None,
               account_size=5000.0, prop_daily_loss_pct=4.0, prop_max_dd_pct=6.0,
               prop_safety=0.75, profit_target_usd=500.0)
    cfg.update(over)
    return SMCBot(make_cfg(**cfg), client)


def test_funded_trade_allowed_within_limits():
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = funded_bot(client)
    bot.tick()
    assert bot.trade is not None  # $25 risk vs $150 (75% of $200) daily budget


def test_funded_blocks_trade_that_could_break_daily_limit():
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = funded_bot(client)
    bot._roll_day()
    bot.day_pnl_usd = bot.total_pnl_usd = -130.0  # $130 lost today; +$25 > $150 budget
    bot.tick()
    assert bot.trade is None and client.orders == []


def test_funded_blocks_near_static_floor_and_after_target():
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = funded_bot(client, max_daily_loss_r=99)
    bot._roll_day()
    bot.total_pnl_usd = -240.0  # balance 4760, floor 4700 + 75 buffer
    bot.tick()
    assert bot.trade is None
    bot.total_pnl_usd = 500.0
    bot.tick()
    assert bot.trade is None and client.orders == []


def test_funded_flattens_before_daily_breach():
    from vestbot.smc import Report
    from vestbot.smc_bot import Trade

    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = funded_bot(client)
    bot._roll_day()  # daily limit $200
    bot.trade = Trade("SHORT", 30000, 30500, [29000.0, None, None, None], 1.0, ("k",),
                      risk=500, size=1.0)
    bot.manage(Report("X", 30150))  # -$150 unrealized: under 90% of $200
    assert bot.trade is not None
    bot.manage(Report("X", 30185))  # -$185 >= $180
    assert bot.trade is None and client.orders[-1]["reduce_only"]
    assert abs(bot.day_pnl_usd + 185) < 1e-6


def test_day_resets_at_8pm_new_york():
    from datetime import datetime, timezone
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = funded_bot(client)
    bot.now = lambda: datetime(2026, 10, 1, 23, 59, tzinfo=timezone.utc)  # 7:59 PM EDT
    before = bot.session_day()
    bot.now = lambda: datetime(2026, 10, 2, 0, 1, tzinfo=timezone.utc)    # 8:01 PM EDT
    assert bot.session_day() != before


def test_telegram_alerts_for_manual_trading(monkeypatch):
    sent = []
    monkeypatch.setattr("vestbot.notify.Notifier.send", lambda self, text: sent.append(text))
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = SMCBot(make_cfg(dry_run=True, order_size="1", size_decimals=2,
                          telegram_token="t", telegram_chat_id="c"), client)
    bot.tick()
    assert client.orders == []  # alert mode places nothing
    assert sent and sent[0].startswith("SHORT") and "SL:" in sent[0] and "TP1/TP2/TP3" in sent[0]
    bot.trade.targets = [30540.0, None, None, None]
    move_to(client, 30539)
    bot.tick()
    assert any("ĐÓNG 0.50" in m for m in sent) and any("DỜI SL" in m for m in sent)


def test_notifier_never_raises(monkeypatch):
    import requests
    from vestbot.notify import Notifier

    def boom(*a, **k):
        raise requests.ConnectionError("offline")
    monkeypatch.setattr(requests, "post", boom)
    Notifier("t", "c").send("hi")  # logs a warning, does not raise


def test_funded_account_never_trades_live():
    import pytest
    client = FakeClient(short_setup_1m(push=(30560,)))
    bot = funded_bot(client)  # account_size set, dry_run False
    with pytest.raises(RuntimeError, match="Vest Capital"):
        bot.start()
    assert client.orders == []
