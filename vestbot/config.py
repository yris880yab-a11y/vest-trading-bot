"""Load bot settings from environment variables (or a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()

ENDPOINTS = {
    "prod": {
        "rest": "https://server-prod.hz.vestmarkets.com/v2",
        "ws": "wss://ws-prod.hz.vestmarkets.com/ws-api",
        "verifying_contract": "0x919386306C47b2Fe1036e3B4F7C40D22D2461a23",
    },
    "dev": {
        "rest": "https://server-dev.hz.vestmarkets.com/v2",
        "ws": "wss://ws-dev.hz.vestmarkets.com/ws-api",
        "verifying_contract": "0x8E4D87AEf4AC4D5415C35A12319013e34223825B",
    },
}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    return value if value not in ("", None) else default


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Config:
    env: str
    rest_url: str
    ws_url: str
    verifying_contract: str

    api_key: str | None
    account_group: int | None
    signing_private_key: str | None

    symbol: str
    strategy: str
    confirm_symbol: str | None
    min_momentum: int
    size_decimals: int
    max_daily_loss_r: float
    risk_usd: float | None
    max_notional_usd: float | None
    max_trades_per_day: int
    day_reset: str
    account_size: float | None
    prop_daily_loss_pct: float
    prop_max_dd_pct: float
    prop_safety: float
    profit_target_usd: float | None
    telegram_token: str | None
    broker: str
    topstep_url: str
    topstep_username: str | None
    topstep_api_key: str | None
    topstep_account: str | None
    point_value: float
    max_contracts: float | None
    prop_daily_loss_usd: float | None
    prop_max_loss_usd: float | None
    prop_trailing: str
    flatten_time_ct: str | None
    telegram_chat_id: str | None
    state_file: str | None
    interval: str
    fast_ema: int
    slow_ema: int
    order_size: str
    leverage: int
    stop_loss_pct: float
    take_profit_pct: float
    max_slippage_pct: float
    poll_seconds: int
    dry_run: bool

    @classmethod
    def from_env(cls) -> "Config":
        env = (_env("VEST_ENV", "prod") or "prod").lower()
        if env not in ENDPOINTS:
            raise ValueError(f"VEST_ENV must be one of {list(ENDPOINTS)}, got {env!r}")
        ep = ENDPOINTS[env]
        group = _env("VEST_ACCOUNT_GROUP")
        return cls(
            env=env,
            rest_url=_env("VEST_REST_URL", ep["rest"]),
            ws_url=_env("VEST_WS_URL", ep["ws"]),
            verifying_contract=_env("VEST_VERIFYING_CONTRACT", ep["verifying_contract"]),
            api_key=_env("VEST_API_KEY"),
            account_group=int(group) if group is not None else None,
            signing_private_key=_env("VEST_SIGNING_PRIVATE_KEY"),
            symbol=_env("BOT_SYMBOL", "BTC-PERP"),
            strategy=(_env("BOT_STRATEGY", "smc") or "smc").lower(),
            confirm_symbol=_env("BOT_CONFIRM_SYMBOL"),
            min_momentum=int(_env("BOT_MIN_MOMENTUM", "3")),
            size_decimals=int(_env("BOT_SIZE_DECIMALS", "4")),
            max_daily_loss_r=float(_env("BOT_MAX_DAILY_LOSS_R", "3")),
            risk_usd=float(v) if (v := _env("BOT_RISK_USD")) else None,
            max_notional_usd=float(v) if (v := _env("BOT_MAX_NOTIONAL_USD")) else None,
            max_trades_per_day=int(_env("BOT_MAX_TRADES_PER_DAY", "6")),
            day_reset=(_env("BOT_DAY_RESET", "vest") or "vest").lower(),
            account_size=float(v) if (v := _env("BOT_ACCOUNT_SIZE")) else None,
            prop_daily_loss_pct=float(_env("BOT_PROP_DAILY_LOSS_PCT", "4")),
            prop_max_dd_pct=float(_env("BOT_PROP_MAX_DD_PCT", "6")),
            prop_safety=float(_env("BOT_PROP_SAFETY", "0.75")),
            profit_target_usd=float(v) if (v := _env("BOT_PROFIT_TARGET_USD")) else None,
            telegram_token=_env("TELEGRAM_BOT_TOKEN"),
            broker=(_env("BOT_BROKER", "vest") or "vest").lower(),
            topstep_url=_env("TOPSTEP_API_URL", "https://api.topstepx.com"),
            topstep_username=_env("TOPSTEP_USERNAME"),
            topstep_api_key=_env("TOPSTEP_API_KEY"),
            topstep_account=_env("TOPSTEP_ACCOUNT"),
            point_value=float(_env("BOT_POINT_VALUE", "1")),
            max_contracts=float(v) if (v := _env("BOT_MAX_CONTRACTS")) else None,
            prop_daily_loss_usd=float(v) if (v := _env("BOT_PROP_DAILY_LOSS_USD")) else None,
            prop_max_loss_usd=float(v) if (v := _env("BOT_PROP_MAX_LOSS_USD")) else None,
            prop_trailing=(_env("BOT_PROP_TRAILING", "static") or "static").lower(),
            flatten_time_ct=_env("BOT_FLATTEN_TIME_CT"),
            telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
            state_file=_env("BOT_STATE_FILE", "bot_state.json"),
            interval=_env("BOT_INTERVAL", "15m"),
            fast_ema=int(_env("BOT_FAST_EMA", "9")),
            slow_ema=int(_env("BOT_SLOW_EMA", "21")),
            order_size=_env("BOT_ORDER_SIZE", "0.001"),
            leverage=int(_env("BOT_LEVERAGE", "2")),
            stop_loss_pct=float(_env("BOT_STOP_LOSS_PCT", "1.5")),
            take_profit_pct=float(_env("BOT_TAKE_PROFIT_PCT", "3.0")),
            max_slippage_pct=float(_env("BOT_MAX_SLIPPAGE_PCT", "0.5")),
            poll_seconds=int(_env("BOT_POLL_SECONDS", "30")),
            dry_run=_bool("BOT_DRY_RUN", True),
        )

    def require_credentials(self) -> None:
        if self.broker == "topstep":
            if not (self.topstep_username and self.topstep_api_key):
                raise RuntimeError("Missing TOPSTEP_USERNAME / TOPSTEP_API_KEY in .env")
            return
        missing = [
            name
            for name, value in (
                ("VEST_API_KEY", self.api_key),
                ("VEST_ACCOUNT_GROUP", self.account_group),
                ("VEST_SIGNING_PRIVATE_KEY", self.signing_private_key),
            )
            if value is None
        ]
        if missing:
            raise RuntimeError(
                "Missing credentials: " + ", ".join(missing)
                + ". Run `python scripts/register.py` first and put the result in .env"
            )
