"""Bookmap-style NQ signal bot: live L2 depth + trades from TopstepX → alerts.

    python -m vestbot.flow_signal                 # symbol from FLOW_SYMBOL (default MNQ)
    python -m vestbot.flow_signal NQ --record data/nq_flow.jsonl
    python -m vestbot.flow_signal --replay data/nq_flow.jsonl   # re-run a recording offline

Signals only — no orders. Each signal is printed and sent to Telegram when
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are set. Data comes from the ProjectX market hub
(SignalR over a websocket): SubscribeContractMarketDepth + SubscribeContractTrades.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import time
from datetime import datetime
from typing import Callable, Iterator

from .orderflow import FlowEngine, FlowParams, FlowSignal, PaperTracker

log = logging.getLogger("vestbot.flow")
RS = "\x1e"  # SignalR JSON record separator


def parse_ts(value) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return time.time()


def handle(engine: FlowEngine, kind: str, data, paper: PaperTracker | None = None,
           on_close: Callable | None = None) -> list[FlowSignal]:
    """Feed one GatewayDepth / GatewayTrade payload (a dict or a list of dicts).

    Open paper trades are scored on each print before that print can open a new one.
    """
    out = []
    for item in data if isinstance(data, list) else [data]:
        ts = parse_ts(item.get("timestamp"))
        if kind == "depth":
            engine.on_depth(ts, int(item.get("type", 0)), float(item["price"]),
                            float(item.get("volume") or 0))
        elif kind == "trade":
            side = item.get("type")
            price = float(item["price"])
            for t in paper.on_price(ts, price) if paper else []:
                if on_close:
                    on_close(t)
            sig = engine.on_trade(ts, price, float(item.get("volume") or 0),
                                  int(side) if side in (0, 1) else None)
            if sig:
                out.append(sig)
    return out


class MarketHub:
    """Minimal SignalR JSON-protocol client for the ProjectX market hub."""

    def __init__(self, url: str, token: str, contract_id: str):
        self.url, self.token, self.contract_id = url, token, contract_id

    def messages(self) -> Iterator[tuple[str, object]]:
        import websocket  # websocket-client

        ws = websocket.create_connection(f"{self.url}?access_token={self.token}", timeout=30)
        try:
            ws.send(json.dumps({"protocol": "json", "version": 1}) + RS)
            for i, target in enumerate(("SubscribeContractMarketDepth", "SubscribeContractTrades")):
                ws.send(json.dumps({"type": 1, "invocationId": str(i), "target": target,
                                    "arguments": [self.contract_id]}) + RS)
            last_ping = time.time()
            while True:
                if time.time() - last_ping > 10:
                    ws.send(json.dumps({"type": 6}) + RS)
                    last_ping = time.time()
                try:
                    raw = ws.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                for frame in filter(None, raw.split(RS)):
                    msg = json.loads(frame)
                    if msg.get("type") == 7:
                        raise ConnectionError(f"hub closed: {msg.get('error')}")
                    if msg.get("type") == 3 and msg.get("error"):
                        raise ConnectionError(f"subscribe failed: {msg['error']}")
                    if msg.get("type") != 1:
                        continue
                    kind = {"GatewayDepth": "depth", "GatewayTrade": "trade"}.get(msg.get("target"))
                    args = msg.get("arguments") or []
                    if kind and len(args) >= 2:
                        yield kind, args[1]
        finally:
            ws.close()


def run(engine: FlowEngine, source: Iterator[tuple[str, object]], on_signal: Callable,
        record=None, status_every: float = 30.0, paper: PaperTracker | None = None,
        on_close: Callable | None = None) -> None:
    last_status = 0.0
    for kind, data in source:
        if record:
            record.write(json.dumps({"k": kind, "d": data}) + "\n")
        for sig in handle(engine, kind, data, paper, on_close):
            on_signal(sig)
            if paper:
                paper.on_signal(sig)
        now = engine.trades[-1][0] if engine.trades else 0.0
        if status_every and now - last_status >= status_every:
            last_status = now
            log.info(engine.status(now))
            while engine.events:
                log.info("  %s", engine.events.popleft())


def replay(path: str) -> Iterator[tuple[str, object]]:
    with open(path) as f:
        for line in f:
            if line.strip():
                msg = json.loads(line)
                yield msg["k"], msg["d"]


def main() -> None:
    from .config import Config
    from .notify import Notifier

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("symbol", nargs="?", default=os.getenv("FLOW_SYMBOL", "MNQ"))
    ap.add_argument("--record", help="append raw depth/trade messages to this JSONL file")
    ap.add_argument("--replay", help="run a recorded JSONL file instead of the live feed")
    ap.add_argument("--paper-log", default="flow_paper.csv",
                    help="CSV of paper results: each signal followed to TP / SL / time stop")
    ap.add_argument("--max-hold", type=float, default=900, help="paper time stop, seconds")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = Config.from_env()
    params = FlowParams.from_dict({k[5:].lower(): v for k, v in os.environ.items()
                                   if k.startswith("FLOW_")})
    notifier = Notifier(cfg.telegram_token, cfg.telegram_chat_id)

    def on_signal(sig: FlowSignal) -> None:
        when = datetime.fromtimestamp(sig.ts).strftime("%H:%M:%S")
        text = f"[{when}] " + sig.text(args.symbol)
        log.warning("SIGNAL %s", text.replace("\n", " | "))
        notifier.send(text)

    paper = PaperTracker(params.tick, max_hold=args.max_hold)
    new_log = not os.path.exists(args.paper_log)
    paper_csv = open(args.paper_log, "a", newline="")
    writer = csv.writer(paper_csv)
    if new_log:
        writer.writerow(["signal_time", "direction", "kind", "score", "level", "entry", "sl", "tp",
                         "exit_time", "exit", "outcome", "r", "reasons"])

    def on_close(t) -> None:
        s = t.sig
        fmt = lambda x: datetime.fromtimestamp(x).isoformat(timespec="seconds")
        writer.writerow([fmt(s.ts), s.direction, s.kind, s.score, s.level, t.entry, s.sl, s.tp,
                         fmt(t.exit_ts), t.exit, t.outcome, round(t.r, 2), " · ".join(s.reasons)])
        paper_csv.flush()
        log.warning("PAPER %s %s %s → %s %+.2fR | %s", fmt(s.ts), s.direction, s.kind,
                    t.outcome, t.r, paper.summary())

    if args.replay:
        try:
            run(FlowEngine(params), replay(args.replay), on_signal, paper=paper, on_close=on_close)
        finally:
            log.info(paper.summary())
            paper_csv.close()
        return

    from .topstep import TopstepClient

    if not (cfg.topstep_username and cfg.topstep_api_key):
        raise SystemExit("Cần TOPSTEP_USERNAME / TOPSTEP_API_KEY trong .env (dữ liệu L2 lấy từ TopstepX)")
    client = TopstepClient(cfg.topstep_url, cfg.topstep_username, cfg.topstep_api_key)
    hub_url = os.getenv("TOPSTEP_MARKET_HUB", "wss://rtc.topstepx.com/hubs/market")
    record = open(args.record, "a") if args.record else None
    try:
        while True:  # reconnect forever; the book is rebuilt from the fresh snapshot
            engine = FlowEngine(params)
            try:
                client._ensure_token()
                hub = MarketHub(hub_url, client.token, client.contract_id(args.symbol))
                log.info("Đang nghe order flow %s (%s)", args.symbol, hub.contract_id)
                run(engine, hub.messages(), on_signal, record, paper=paper, on_close=on_close)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                log.warning("Mất kết nối market hub: %s — thử lại sau 5s", e)
                time.sleep(5)
    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        log.info(paper.summary())
        paper_csv.close()
        if record:
            record.close()


if __name__ == "__main__":
    main()
