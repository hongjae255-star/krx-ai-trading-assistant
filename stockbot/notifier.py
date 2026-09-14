from __future__ import annotations

import logging
import requests

from .config import env

log = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self):
        self.token = env("TELEGRAM_BOT_TOKEN")
        self.chat_id = env("TELEGRAM_CHAT_ID")

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.configured:
            print(text)
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)] or [""]
        ok = True
        for chunk in chunks:
            try:
                r = requests.post(
                    url,
                    json={"chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": True},
                    timeout=15,
                )
                r.raise_for_status()
                ok = ok and bool(r.json().get("ok"))
            except Exception as exc:
                log.error("Telegram send failed: %s", exc)
                ok = False
        return ok
