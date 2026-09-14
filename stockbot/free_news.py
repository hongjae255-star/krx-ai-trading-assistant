from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from .config import Settings

log = logging.getLogger(__name__)


class NaverFinanceNewsClient:
    """Best-effort public headline collector for Naver Finance stock news pages.

    No API key is required. Only headline metadata visible on the public list page is
    collected; full article bodies are intentionally not downloaded. Markup can change,
    so every failure is treated as optional and never stops the trading pipeline.
    """

    BASE = "https://finance.naver.com"
    LIST_URL = BASE + "/item/news_news.naver"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.timeout = int(settings.get("local_engine.news_http_timeout_seconds", 8))
        self.delay = float(settings.get("local_engine.news_request_delay_seconds", 0.10))
        self.max_items = int(settings.get("local_engine.news_max_items_per_stock", 8))
        self.lookback_days = int(settings.get("local_engine.max_event_age_days", 7))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KRX-Free-Assistant/4.0",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
        })

    @staticmethod
    def _parse_date(text: str) -> str:
        text = re.sub(r"\s+", " ", text.strip())
        for fmt in ("%Y.%m.%d %H:%M", "%Y.%m.%d", "%y.%m.%d %H:%M", "%y.%m.%d"):
            try:
                return datetime.strptime(text, fmt).isoformat(timespec="minutes")
            except Exception:
                pass
        return ""

    def fetch(self, code: str) -> list[dict[str, Any]]:
        if not bool(self.settings.get("local_engine.fetch_public_news", True)):
            return []
        try:
            r = self.session.get(self.LIST_URL, params={"code": code, "page": 1}, timeout=self.timeout)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() in {"iso-8859-1", "ascii"}:
                r.encoding = r.apparent_encoding or "euc-kr"
            soup = BeautifulSoup(r.text, "html.parser")
            time.sleep(max(0.0, self.delay))
        except Exception as exc:
            log.debug("free news fetch failed %s: %s", code, exc)
            return []

        cutoff = date.today() - timedelta(days=max(0, self.lookback_days))
        out: list[dict[str, Any]] = []
        seen: set[str] = set()
        for tr in soup.find_all("tr"):
            a = tr.find("a", href=re.compile(r"news_read\.naver", re.I))
            if a is None:
                continue
            title = re.sub(r"\s+", " ", a.get_text(" ", strip=True)).strip()
            if not title or title in seen:
                continue
            tds = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
            dt_text = next((x for x in reversed(tds) if re.search(r"\d{2,4}\.\d{2}\.\d{2}", x)), "")
            parsed = self._parse_date(dt_text)
            if parsed:
                try:
                    if datetime.fromisoformat(parsed).date() < cutoff:
                        continue
                except Exception:
                    pass
            source = ""
            for x in tds:
                if x and x != title and x != dt_text and len(x) <= 24:
                    source = x
                    break
            seen.add(title)
            out.append({
                "title": title,
                "date": parsed or dt_text,
                "source": source,
                "url": urljoin(self.BASE, a.get("href", "")),
            })
            if len(out) >= self.max_items:
                break
        return out
