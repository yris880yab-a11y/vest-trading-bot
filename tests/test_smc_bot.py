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
