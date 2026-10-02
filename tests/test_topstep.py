from datetime import datetime, timezone

import pytest
from scenario import short_setup_1m
from test_bot import make_cfg
from test_smc_bot import FakeClient

from vestbot.smc_bot import SMCBot
from vestbot.topstep import TopstepClient


class FakeResp:
    def __init__(self, data, status=200):
        self.data, self.status_code, self.text = data, status, str(data)

    def json(self):
        return self.data


class FakeSession:
    """Records calls and answers like the ProjectX Gateway API."""

    def __init__(self, accounts=None):
        self.calls = []
        self.accounts = accounts or [{"id": 7, "name": "50KTC-1"}]

    def post(self, url, json, headers, timeout):
        path = url.split(".com", 1)[1]
        self.calls.append((path, json, headers))
        ok = {"success": True, "errorCode": 0}
        if path == "/api/Auth/loginKey":
            return FakeResp({**ok, "token": "tok"})
        if path == "/api/Account/search":
            return FakeResp({**ok, "accounts": self.accounts})
        if path == "/api/Contract/search":
            return FakeResp({**ok, "contracts": [
                {"id": "CON.F.US.MNQ.Z26", "name": "MNQZ6", "activeContract": True}]})
        if path == "/api/History/retrieveBars":
            return FakeResp({**ok, "bars": [
                {"t": "2026-10-02T14:01:00Z", "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 1},
                {"t": "2026-10-02T14:00:00Z", "o": 1, "h": 2, "l": 0, "c": 2, "v": 1}]})
        if path == "/api/Position/searchOpen":
            return FakeResp({**ok, "positions": [
                {"contractId": "CON.F.US.MNQ.Z26", "type": 2, "size": 3, "averagePrice": 30000}]})
        return FakeResp({**ok, "orderId": 1})


def client(**kw):
    c = TopstepClient("https://api.topstepx.com", "me", "key", **kw)
    c.session = FakeSession()
    return c


def test_bars_are_sorted_oldest_first_and_authenticated():
    c = client()
    bars = c.klines("MNQ", "5m", limit=10)
    assert [b["t"] for b in bars] == ["2026-10-02T14:00:00Z", "2026-10-02T14:01:00Z"]
    path, body, headers = c.session.calls[-1]
    assert body["contractId"] == "CON.F.US.MNQ.Z26" and (body["unit"], body["unitNumber"]) == (2, 5)
    assert headers["Authorization"] == "Bearer tok"


def test_market_entry_and_partial_close():
    c = client()
    c.place_order(symbol="MNQ", is_buy=False, size="3", limit_price="0")
    path, body, _ = c.session.calls[-1]
    assert path == "/api/Order/place"
    assert body == {"accountId": 7, "contractId": "CON.F.US.MNQ.Z26", "type": 2, "side": 1, "size": 3}
    c.place_order(symbol="MNQ", is_buy=True, size="1", reduce_only=True)
    path, body, _ = c.session.calls[-1]
    assert path == "/api/Position/partialCloseContract" and body["size"] == 1


def test_account_must_be_chosen_when_several():
    c = client(account=None)
    c.session.accounts = [{"id": 1, "name": "A"}, {"id": 2, "name": "B"}]
    with pytest.raises(RuntimeError, match="TOPSTEP_ACCOUNT"):
        c.resolve_account()
    c.account_ref = "B"
    assert c.resolve_account() == 2


def test_positions_mapped_for_the_bot():
    c = client()
    c.contract_id("MNQ")
    pos = c.account()["positions"][0]
    assert pos == {"symbol": "MNQ", "isLong": False, "size": 3, "entryPrice": 30000}


def topstep_bot(**over):
    cfg = dict(dry_run=False, broker="topstep", size_decimals=0, risk_usd=200.0,
               max_notional_usd=None, point_value=2.0, max_contracts=50,
               account_size=50000.0, prop_daily_loss_pct=0, prop_daily_loss_usd=None,
               prop_max_loss_usd=2000.0, prop_trailing="eod", profit_target_usd=3000.0,
               flatten_time_ct="15:08", day_reset="cme")
    cfg.update(over)
    return SMCBot(make_cfg(**cfg), FakeClient(short_setup_1m(push=(30560,))))


def test_contracts_from_dollar_risk_and_point_value():
    bot = topstep_bot()
    bot.now = lambda: datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)  # 10:00 CT
    bot.tick()
    t = bot.trade
    assert t is not None and t.size == int(t.size)          # whole contracts
    assert t.size * t.risk * 2.0 <= 200.0                    # never more than $200 at SL


def test_trailing_eod_floor_caps_at_start():
    bot = topstep_bot()
    assert bot.floor_usd == 48000
    bot.peak_eod_balance = 51000
    assert bot.floor_usd == 49000
    bot.peak_eod_balance = 53000
    assert bot.floor_usd == 50000


def test_flat_before_3_10_pm_chicago():
    bot = topstep_bot()
    bot.now = lambda: datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)  # 10:00 CT
    bot.tick()
    assert bot.trade is not None
    bot.now = lambda: datetime(2026, 10, 2, 20, 9, tzinfo=timezone.utc)  # 3:09 PM CT
    bot.tick()
    assert bot.trade is None and bot.client.orders[-1]["reduce_only"]


def test_topstep_live_is_allowed_unlike_vest_capital():
    bot = topstep_bot(topstep_username="me", topstep_api_key="key")
    bot.client.set_leverage = lambda *a: None
    bot.client.account = lambda: {"positions": []}
    bot.start()  # no RuntimeError
