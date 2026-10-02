"""Check the TopstepX connection: login, accounts, contracts and the latest bar.

    python scripts/check_topstep.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from vestbot.bot import make_client  # noqa: E402
from vestbot.config import Config  # noqa: E402


def main() -> None:
    cfg = Config.from_env()
    if cfg.broker != "topstep":
        sys.exit("BOT_BROKER chưa phải topstep: copy .env.topstep.example thành .env trước")
    client = make_client(cfg)
    print("1) Đăng nhập + tài khoản:")
    for a in client.accounts():
        print(f"   - {a.get('name')}  (id {a.get('id')}, số dư {a.get('balance')})")
    print("   -> Nếu có nhiều dòng, copy đúng tên tài khoản Combine vào TOPSTEP_ACCOUNT=")
    print(f"2) Tài khoản bot sẽ dùng: id {client.resolve_account()}")
    for sym in filter(None, (cfg.symbol, cfg.confirm_symbol)):
        bar = client.klines(sym, "1m", limit=1)
        last = bar[-1] if bar else None
        print(f"3) {sym}: {client.contract_id(sym)} | nến 1m mới nhất: {last}")
    print("OK — kết nối TopstepX hoạt động.")


if __name__ == "__main__":
    main()
