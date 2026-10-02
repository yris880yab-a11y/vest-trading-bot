"""Print one trade-structure report, no trading.

    python -m vestbot.analyze [SYMBOL] [--confirm SYMBOL]
"""
from __future__ import annotations

import argparse

from .bot import make_client
from .config import Config
from .smc import TIMEFRAMES, analyze, parse_candles
from .smc_bot import KLINE_LIMITS


def main() -> None:
    cfg = Config.from_env()
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("symbol", nargs="?", default=cfg.symbol)
    p.add_argument("--confirm", default=cfg.confirm_symbol,
                   help="symbol used for market confirmation, e.g. ES for NQ")
    args = p.parse_args()

    client = make_client(cfg)
    data = {tf: parse_candles(client.klines(args.symbol, tf, limit=KLINE_LIMITS[tf]))
            for tf in TIMEFRAMES}
    confirm = (parse_candles(client.klines(args.confirm, "1m", limit=60))
               if args.confirm else None)
    print(analyze(data, args.symbol, confirm, cfg.min_momentum).render())


if __name__ == "__main__":
    main()
