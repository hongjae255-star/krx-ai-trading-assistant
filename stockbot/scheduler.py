from __future__ import annotations

import logging
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import Settings
from .jobs import TradingAssistant
from .us_market import USMarketAssistant

log = logging.getLogger(__name__)


def _minutes(hm: str) -> int:
    h, m = [int(x) for x in hm.split(":", 1)]
    return h * 60 + m


def _is_interval_time(hm: str, start: str, end: str, interval: int) -> bool:
    cur = _minutes(hm); s, e = _minutes(start), _minutes(end)
    return s <= cur <= e and (cur - s) % max(1, interval) == 0


def run_scheduler(settings: Settings) -> None:
    """24h dual-market scheduler.

    KRX jobs use Asia/Seoul clock. US jobs use America/New_York so DST is
    automatically handled by zoneinfo. Read-only analysis; no order endpoints.
    """
    kr_tz = ZoneInfo(settings.timezone)
    us_tz = ZoneInfo(str(settings.get("us_market.timezone", "America/New_York")))
    kr = TradingAssistant(settings)
    us = USMarketAssistant(settings, kr.kis, kr.macro)

    research_t = settings.get("scheduler.research", "07:45")
    pre = settings.get("scheduler.premarket", "08:30")
    close_t = settings.get("scheduler.closing", "15:45")
    active_start = settings.get("monitoring.active_start", "09:10")
    active_end = settings.get("monitoring.active_end", "15:20")
    active_interval = int(settings.get("monitoring.active_interval_minutes", 10))
    replacement_interval = int(settings.get("monitoring.replacement_interval_minutes", 30))
    summary_interval = int(settings.get("monitoring.telegram_summary_interval_minutes", 30))

    us_enabled = bool(settings.get("us_market.enabled", True))
    us_pre = settings.get("us_market.scheduler.premarket_et", "09:15")
    us_start = settings.get("us_market.scheduler.active_start_et", "09:40")
    us_end = settings.get("us_market.scheduler.active_end_et", "15:50")
    us_interval = int(settings.get("us_market.scheduler.active_interval_minutes", 10))
    us_close = settings.get("us_market.scheduler.closing_et", "16:15")

    executed: set[str] = set()
    log.info("Dual scheduler started. KRX=%s US=%s (DST-aware)", settings.timezone, us_tz.key)

    def run_once(key: str, fn) -> None:
        if key in executed:
            return
        try:
            fn()
        except Exception:
            log.exception("Scheduled job failed: %s", key)
        finally:
            executed.add(key)

    while True:
        kr_now = datetime.now(kr_tz)
        us_now = datetime.now(us_tz)

        if kr_now.weekday() < 5:
            hm = kr_now.strftime("%H:%M"); d = kr_now.date().isoformat()
            if hm == research_t:
                run_once(f"KR-{d}-{hm}-research", kr.research_only)
            if hm == pre:
                run_once(f"KR-{d}-{hm}-premarket", kr.premarket)
            if _is_interval_time(hm, active_start, active_end, active_interval):
                cur = _minutes(hm); elapsed = max(0, cur - _minutes(active_start))
                scan = elapsed % max(1, replacement_interval) == 0
                summary = elapsed % max(1, summary_interval) == 0
                run_once(f"KR-{d}-{hm}-intraday", lambda: kr.intraday(scan_replacement=scan, force_summary=summary))
            if hm == close_t:
                run_once(f"KR-{d}-{hm}-close", kr.close)

        if us_enabled and us_now.weekday() < 5:
            hm = us_now.strftime("%H:%M"); d = us_now.date().isoformat()
            if hm == us_pre:
                run_once(f"US-{d}-{hm}-premarket", us.premarket)
            if _is_interval_time(hm, us_start, us_end, us_interval):
                # Quotes every 10 minutes; full US market re-scan and summary every 30.
                elapsed = max(0, _minutes(hm) - _minutes(us_start))
                scan = elapsed % int(settings.get("us_market.scheduler.replacement_interval_minutes", 30)) == 0
                summary = elapsed % int(settings.get("us_market.scheduler.telegram_summary_interval_minutes", 30)) == 0
                run_once(f"US-{d}-{hm}-intraday", lambda: us.intraday(force_summary=summary, scan_replacement=scan))
            if hm == us_close:
                run_once(f"US-{d}-{hm}-close", us.close)

        if len(executed) > 800:
            kr_today = kr_now.date().isoformat(); us_today = us_now.date().isoformat()
            executed = {k for k in executed if kr_today in k or us_today in k}
        time.sleep(10)
