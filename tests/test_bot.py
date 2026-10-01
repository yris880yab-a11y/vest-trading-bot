from vestbot.bot import Bot, _find_position
from vestbot.config import Config


class FakeClient:
    def __init__(self, closes):
        self.closes = closes
        self.orders = []

    def klines(self, symbol, interval, limit):
        return [[i, c, c, c, c, 0] for i, c in enumerate(self.closes)]

    def place_order(self, **kw):
        self.orders.append(kw)
        return {"id": "1"}

    def account(self):
        return {"positions": []}

    def set_leverage(self, *a):
        return {}


def make_cfg(**over):
    cfg = Config.from_env()
    cfg.dry_run = True
    cfg.fast_ema, cfg.slow_ema = 3, 10
    for k, v in over.items():
        setattr(cfg, k, v)
    return cfg


def test_dry_run_opens_then_stops_out():
    client = FakeClient([100 - i for i in range(30)] + [200, 200])
    bot = Bot(make_cfg(), client)
    bot.tick()
    assert bot.position and bot.position.side == "LONG"
    assert client.orders == []  # dry-run never sends orders

    client.closes = client.closes[:-1] + [150]  # -25% -> stop-loss
    bot.tick()
    assert bot.position is None
    bot.tick()  # same candle's LONG signal must not re-open the trade
    assert bot.position is None


def test_live_sends_signed_market_order():
    client = FakeClient([100 - i for i in range(30)] + [200, 200])
    bot = Bot(make_cfg(dry_run=False), client)
    bot.tick()
    order = client.orders[0]
    assert order["is_buy"] is True and order["order_type"] == "MARKET"
    assert order["limit_price"] == "201.00"


def test_find_position():
    acc = {"positions": [{"symbol": "BTC-PERP", "isLong": False, "size": "0.01",
                          "entryPrice": "60000"}]}
    pos = _find_position(acc, "BTC-PERP")
    assert pos.side == "SHORT" and pos.size == 0.01
    assert _find_position(acc, "ETH-PERP") is None
