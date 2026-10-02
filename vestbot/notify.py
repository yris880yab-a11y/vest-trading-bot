"""Telegram alerts: lets you trade the bot's signals by hand where no API is allowed."""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("vestbot.notify")


class Notifier:
    def __init__(self, token: str | None, chat_id: str | None, timeout: float = 10.0):
        self.token, self.chat_id, self.timeout = token, chat_id, timeout

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage",
                          json={"chat_id": self.chat_id, "text": text[:4000]},
                          timeout=self.timeout).raise_for_status()
        except requests.RequestException as e:  # an alert must never crash the bot
            log.warning("Telegram send failed: %s", e)
