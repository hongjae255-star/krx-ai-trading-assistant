from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from .cloud_state import CloudStateManager
from .config import load_settings
from .jobs import TradingAssistant
from .us_market import USMarketAssistant
from .market_pulse import MarketPulseService
from .webapp import DashboardStore

log = logging.getLogger(__name__)


def _publish(settings, cloud: CloudStateManager, kis=None, db=None) -> dict:
    if kis is not None and db is not None:
        try:
            MarketPulseService(settings, kis, db).snapshot(force=False)
        except Exception as exc:
            log.warning("market pulse refresh failed: %s", exc)
    store = DashboardStore(settings)
    dashboard = store.dashboard()
    dashboard.pop("positions", None)
    dashboard.pop("risk", None)
    dashboard["monitor_interval_minutes"] = 15
    dashboard["app_refresh_seconds"] = 60
    if isinstance(dashboard.get("us"), dict): dashboard["us"]["monitor_interval_minutes"] = 15
    dashboard["cloud"] = {
        "provider": "github-actions+supabase",
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stale_after_minutes": 30,
    }
    history = {"items": store.history(30), "generated_at": dashboard["cloud"]["generated_at"]}
    cloud.storage.upload_json("dashboard.json", dashboard, public=True)
    cloud.storage.upload_json("history.json", history, public=True)
    return {"dashboard_url": cloud.storage.public_url("dashboard.json"), "history_url": cloud.storage.public_url("history.json")}


def run_cloud_job(job: str, full_scan: bool = False) -> dict:
    settings = load_settings()
    cloud = CloudStateManager(settings)
    if not cloud.configured:
        raise RuntimeError("Set SUPABASE_URL and SUPABASE_SECRET_KEY in GitHub Secrets")
    cloud.restore()

    bot = TradingAssistant(settings)
    us = USMarketAssistant(settings, bot.kis, bot.macro)
    result = None
    success = False
    try:
        if job == "research": result = bot.research_only()
        elif job == "kr-premarket": result = [x.__dict__ for x in bot.premarket()]
        elif job == "kr-intraday": result = bot.intraday(scan_replacement=full_scan, force_summary=full_scan)
        elif job == "kr-close": result = bot.close()
        elif job == "us-premarket": result = [x.__dict__ for x in us.premarket()]
        elif job == "us-intraday": result = us.intraday(force_summary=full_scan, scan_replacement=full_scan)
        elif job == "us-close": result = us.close()
        elif job == "macro": result = bot.macro.snapshot(force=True)
        elif job == "refresh-kr":
            macro = bot.macro.snapshot(force=True)
            live = bot.macro.live_snapshot(force=True)
            pulse = MarketPulseService(settings, bot.kis, bot.db).snapshot(force=True)
            market = bot.intraday(scan_replacement=True, force_summary=True)
            result = {
                "market": "KR",
                "macro": {
                    "source": macro.get("source"),
                    "fresh": macro.get("fresh_series_count"),
                    "stale": macro.get("stale_series_count"),
                    "hard_failures": macro.get("hard_failure_count", len(macro.get("failures", []))),
                },
                "live_proxy_failures": len(live.get("failures", [])),
                "index_failures": len(pulse.get("failures", [])),
                "intraday": market,
            }
        elif job == "refresh-us":
            macro = bot.macro.snapshot(force=True)
            live = bot.macro.live_snapshot(force=True)
            pulse = MarketPulseService(settings, bot.kis, bot.db).snapshot(force=True)
            market = us.intraday(force_summary=True, scan_replacement=True)
            result = {
                "market": "US",
                "macro": {
                    "source": macro.get("source"),
                    "fresh": macro.get("fresh_series_count"),
                    "stale": macro.get("stale_series_count"),
                    "hard_failures": macro.get("hard_failure_count", len(macro.get("failures", []))),
                },
                "live_proxy_failures": len(live.get("failures", [])),
                "index_failures": len(pulse.get("failures", [])),
                "intraday": market,
            }
        elif job == "publish": result = {"published_only": True}
        else: raise ValueError(f"Unknown cloud job: {job}")
        success = True
    finally:
        if success:
            urls = _publish(settings, cloud, bot.kis, bot.db)
            cloud.backup()
            log.info("Published mobile dashboard: %s", urls["dashboard_url"])
    return {"job": job, "full_scan": full_scan, "result": result}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("job", choices=["research","kr-premarket","kr-intraday","kr-close","us-premarket","us-intraday","us-close","macro","refresh-kr","refresh-us","publish"])
    p.add_argument("--full-scan", action="store_true")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    print(json.dumps(run_cloud_job(args.job, args.full_scan), ensure_ascii=False, default=str, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
