"""Opsiyonel Telegram bildirimi. Yoksa sessizce devre disi kalir."""

from __future__ import annotations

import logging
import os
from typing import Optional

import requests

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self) -> None:
        self.token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.enabled = bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text[:4000]},
                timeout=8,
            )
        except Exception:  # bildirim hatasi islemi durdurmamali
            log.warning("Telegram bildirimi gonderilemedi", exc_info=True)
