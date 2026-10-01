"""Replay 1-minute history through the real SMCBot, minute by minute, without look-ahead.

Data: CSV files ``data/{SYMBOL}_{tf}.csv`` with columns time,open,high,low,close[,volume]
(time = UTC "YYYY-MM-DDTHH:MM", bar open time) for tf in 1m 5m 15m 1h 4h 1d.

    python scripts/backtest.py NQ --confirm ES
    python scripts/backtest.py GC

At each 1M close T the bot sees only bars that had closed by T, plus a "forming"
bar for every timeframe rebuilt from the 1M/5M bars up to T. Orders fill at the
1M close (no slippage, no fees). Daily bars follow the CME session (22:00-21:00 UTC).
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import vestbot.smc_bot as smc_bot  # noqa: E402
from vestbot.config import Config  # noqa: E402
from vestbot.smc import Candle  # noqa: E402
from vestbot.smc_bot import KLINE_LIMITS, SMCBot  # noqa: E402

UTC = timezone.utc
VN = timezone(timedelta(hours=7))
TF_MIN = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "4h": 240}


def load(data_dir: Path, sym: str, tf: str) -> list[tuple[datetime, datetime, Candle]]:
    """Bars as (start, end, candle); end = min(start + tf, next bar start)."""
    with open(data_dir / f"{sym}_{tf}.csv") as f:
        rows = list(csv.DictReader(f))
    starts = [datetime.fromisoformat(r["time"]).replace(tzinfo=UTC) for r in rows]
    out = []
    for i, (r, s) in enumerate(zip(rows, starts)):
        if tf == "1d":  # label = session date; session runs 22:00 (prev day) -> 21:00 UTC
            day = s.replace(hour=0, minute=0)
            start, end = day - timedelta(hours=2), day + timedelta(hours=21)
        else:
            start = s
            end = s + timedelta(minutes=TF_MIN[tf])
            if i + 1 < len(starts):
                end = min(end, starts[i + 1])
        c = Candle(int(start.timestamp() * 1000), float(r["open"]), float(r["high"]),
                   float(r["low"]), float(r["close"]))
        out.append((start, end, c))
    return out


def aggregate(parts: list[Candle], t: int) -> Candle:
    return Candle(t, parts[0].o, max(x.h for x in parts), min(x.l for x in parts), parts[-1].c)


class Market:
    """Serves klines exactly as they looked at time ``now``."""

    def __init__(self, data_dir: Path, symbol: str, confirm: str | None = None):
        self.bars = {(symbol, tf): load(data_dir, symbol, tf)
                     for tf in ("1m", "5m", "15m", "1h", "4h", "1d")}
        if confirm:  # confirmation symbol only needs 1M
            self.bars[(confirm, "1m")] = load(data_dir, confirm, "1m")
        self.now: datetime | None = None

    def view(self, sym: str, tf: str, limit: int) -> list[Candle]:
        now = self.now
        bars = self.bars[(sym, tf)]
        closed = [c for s, e, c in bars if e <= now][-limit:]
        cur = [(s, e) for s, e, _ in bars if s <= now < e]
        if tf == "1m" or not cur:
            last = closed[-1]
            return closed + [Candle(int(now.timestamp() * 1000), last.c, last.c, last.c, last.c)]
        start = cur[0][0]
        # rebuild the forming bar from finished 5M bars, then finished 1M bars
        five = [(s, e, c) for s, e, c in self.bars[(sym, "5m")] if s >= start and e <= now]
        cut = five[-1][1] if five else start
        ones = [c for s, e, c in self.bars[(sym, "1m")] if s >= cut and e <= now]
        parts = [c for _, _, c in five] + ones
        if not parts:
            parts = [closed[-1]]
        return closed + [aggregate(parts, int(start.timestamp() * 1000))]


class BacktestClient:
    def __init__(self, market: Market):
        self.market = market
        self.orders: list[dict] = []
        self.price = 0.0

    def klines(self, symbol, interval, limit=200):
        return [[x.t, x.o, x.h, x.l, x.c, 0] for x in self.market.view(symbol, interval, limit)]

    def place_order(self, **kw):
        self.orders.append({**kw, "fill": self.price, "time": self.market.now})
        return {"id": str(len(self.orders))}


def vn(t: datetime) -> str:
    return t.astimezone(VN).strftime("%d/%m %H:%M")


def run(sym: str, confirm: str | None, data_dir: Path, min_score: int) -> dict:
    market = Market(data_dir, sym, confirm)
    client = BacktestClient(market)
    cfg = Config.from_env()
    cfg.symbol, cfg.confirm_symbol = sym, confirm
    cfg.dry_run, cfg.order_size, cfg.size_decimals, cfg.min_momentum = False, "1", 2, min_score
    bot = SMCBot(cfg, client)

    reports = []
    real_analyze = smc_bot.analyze

    def spy(*a, **k):
        rep = real_analyze(*a, **k)
        reports.append(rep)
        return rep

    smc_bot.analyze = spy
    exit_log: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record):
            msg = record.getMessage()
            if msg.startswith("EXIT"):
                exit_log.append(msg.split("(", 1)[-1].rstrip(")"))

    lg = logging.getLogger("vestbot.smc")
    lg.setLevel(logging.INFO)
    lg.propagate = False
    lg.addHandler(Grab())
    one_min = market.bars[(sym, "1m")]
    warmup = 30  # minutes of 1M history before the first decision
    decisions, reasons = Counter(), Counter()
    trades, open_trade = [], None
    try:
        for s, e, c in one_min[warmup:]:
            market.now, client.price = e, c.c
            before = len(client.orders)
            bot.tick()
            rep = reports[-1]
            decisions[rep.decision.split()[0]] += 1
            for r in rep.reasons:
                reasons[re.sub(r"[\d][\d,.]*", "#", r)] += 1
            for o in client.orders[before:]:
                if not o["reduce_only"]:
                    open_trade = {"side": "LONG" if o["is_buy"] else "SHORT", "entry": o["fill"],
                                  "time": e, "sl": bot.trade.sl, "targets": bot.trade.targets,
                                  "report": rep.render(), "exits": []}
                else:
                    open_trade["exits"].append((e, float(o["size"]), o["fill"], exit_log.pop(0)))
            if open_trade and bot.trade is None:
                trades.append(open_trade)
                open_trade = None
    finally:
        smc_bot.analyze = real_analyze
    if open_trade:
        open_trade["open_at_end"] = client.price
        trades.append(open_trade)
    return {"symbol": sym, "start": one_min[warmup][0], "end": one_min[-1][1],
            "trades": trades, "decisions": decisions, "reasons": reasons, "last": reports[-1]}


def summarize(res: dict) -> None:
    print(f"\n######## {res['symbol']}  {vn(res['start'])} → {vn(res['end'])} (giờ VN) ########")
    total_pts = total_r = 0.0
    for n, t in enumerate(res["trades"], 1):
        sign = 1 if t["side"] == "LONG" else -1
        risk = abs(t["entry"] - t["sl"])
        pnl = sum(sign * (px - t["entry"]) * q for _, q, px, _ in t["exits"])
        rest = 1 - sum(q for _, q, _, _ in t["exits"])
        if rest > 1e-9 and "open_at_end" in t:
            pnl += sign * (t["open_at_end"] - t["entry"]) * rest
        r = pnl / risk if risk else 0.0
        total_pts += pnl
        total_r += r
        print(f"\n--- Lệnh {n}: {t['side']} lúc {vn(t['time'])} @ {t['entry']:,.2f} ---")
        print(t["report"])
        for when, q, px, why in t["exits"]:
            print(f"   thoát {q:.2f} @ {px:,.2f} lúc {vn(when)}  ({sign * (px - t['entry']):+.2f} điểm)"
                  f" — {why}")
        if rest > 1e-9 and "open_at_end" in t:
            print(f"   còn mở {rest:.2f}, giá cuối {t['open_at_end']:,.2f}")
        print(f"   => P&L {pnl:+.2f} điểm/1 hợp đồng = {r:+.2f}R (rủi ro {risk:.2f} điểm)")
    print(f"\nTổng: {len(res['trades'])} lệnh, {total_pts:+.2f} điểm, {total_r:+.2f}R")
    print("Quyết định theo phút:", dict(res["decisions"]))
    print("Lý do WAIT hay gặp nhất:", res["reasons"].most_common(6))


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("symbol")
    p.add_argument("--confirm")
    p.add_argument("--data", default="data")
    p.add_argument("--min-momentum", type=int, default=3)
    a = p.parse_args()
    logging.basicConfig(level=logging.WARNING)
    summarize(run(a.symbol, a.confirm, Path(a.data), a.min_momentum))


if __name__ == "__main__":
    main()
