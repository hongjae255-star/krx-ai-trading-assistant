from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from stockbot.config import load_settings
from stockbot.jobs import TradingAssistant
from stockbot.scheduler import run_scheduler


def configure_logging(root: Path) -> None:
    log_dir = root / "logs"
    log_dir.mkdir(exist_ok=True)
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    handlers = [logging.StreamHandler(sys.stdout), logging.FileHandler(log_dir / "stockbot.log", encoding="utf-8")]
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)


def doctor(bot: TradingAssistant) -> int:
    results = {"FreeMode": bool(bot.settings.get("free_mode", True))}
    try:
        results["KIS"] = bot.kis.current_price("005930").get("price", 0) > 0
    except Exception as e:
        results["KIS"] = f"FAIL: {e}"
    try:
        results["KIS_US"] = bot.kis.overseas_current_price("AAPL", "NAS").get("price", 0) > 0
    except Exception as e:
        results["KIS_US"] = f"FAIL: {e}"
    try:
        snap = bot.macro.snapshot(force=True)
        results["GlobalMacro"] = True if snap.get("series") else "no data"
    except Exception as e:
        results["GlobalMacro"] = f"WARN: {e}"
    try:
        results["DART"] = "configured" if bot.dart.configured else "not configured"
        if bot.dart.configured:
            bot.dart.recent_disclosures("005930", 3, 2)
            results["DART"] = True
    except Exception as e:
        results["DART"] = f"FAIL: {e}"
    try:
        results["AnalysisEngine"] = bot.analyst.ping() if bot.analyst.configured else "DISABLED"
        results["OpenAI"] = "DISABLED (Free Mode)"
    except Exception as e:
        results["AnalysisEngine"] = f"FAIL: {e}"
        results["OpenAI"] = "DISABLED (Free Mode)"
    try:
        # Index-only check: no PDFs and no AI calls. Zero rows on a quiet/holiday date is still a valid connection.
        bot.research.client.fetch_recent(bot.today(), fetch_details=False)
        results["BrokerResearchSource"] = True
    except Exception as e:
        results["BrokerResearchSource"] = f"FAIL: {e}"
    try:
        import sklearn
        results["PredictiveML"] = f"scikit-learn {sklearn.__version__}"
    except Exception as e:
        results["PredictiveML"] = f"FAIL: {e}"
    try:
        if bot.notifier.configured:
            results["Telegram"] = bot.notifier.send("✅ KRX AI Trading Assistant 연결 테스트")
        else:
            results["Telegram"] = "not configured"
    except Exception as e:
        results["Telegram"] = f"FAIL: {e}"
    print(json.dumps(results, ensure_ascii=False, indent=2))
    ok_strings = {"configured", "not configured", "DISABLED", "DISABLED (Free Mode)", "LOCAL RULE/ML ENGINE OK", "no data"}
    return 0 if all(v is True or v == "OK" or (isinstance(v, str) and (v.startswith("scikit-learn") or v.startswith("WARN:") or v in ok_strings)) for v in results.values()) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="KRX AI Trading Assistant - analysis/notification only")
    parser.add_argument("command", choices=["doctor", "research", "premarket", "intraday", "close", "status", "validate-model", "macro", "us-premarket", "us-intraday", "us-close", "us-status", "scheduler", "web", "app", "cloud-init", "cloud-deploy"])
    args = parser.parse_args()
    settings = load_settings()
    configure_logging(settings.root)
    bot = TradingAssistant(settings)

    if args.command == "doctor":
        return doctor(bot)
    if args.command == "research":
        print(json.dumps(bot.research_only(), ensure_ascii=False, indent=2))
    elif args.command == "premarket":
        bot.premarket()
    elif args.command == "intraday":
        bot.intraday()
    elif args.command == "close":
        bot.close()
    elif args.command == "status":
        print(json.dumps(bot.status(), ensure_ascii=False, indent=2))
    elif args.command == "validate-model":
        print(json.dumps(bot.predictor.train(bot.today()), ensure_ascii=False, indent=2))
    elif args.command == "macro":
        print(json.dumps(bot.macro.snapshot(force=True), ensure_ascii=False, indent=2))
    elif args.command in {"us-premarket", "us-intraday", "us-close", "us-status"}:
        from stockbot.us_market import USMarketAssistant
        us = USMarketAssistant(settings, bot.kis, bot.macro)
        if args.command == "us-premarket": us.premarket()
        elif args.command == "us-intraday": us.intraday(force_summary=True)
        elif args.command == "us-close": print(json.dumps(us.close(), ensure_ascii=False, indent=2))
        else: print(json.dumps(us.status(), ensure_ascii=False, indent=2))
    elif args.command == "cloud-init":
        from stockbot.cloud_state import CloudStateManager
        print(json.dumps(CloudStateManager(settings).init_remote(), ensure_ascii=False, indent=2))
    elif args.command == "cloud-deploy":
        from stockbot.cloud_deploy import deploy_pwa
        print(json.dumps(deploy_pwa(), ensure_ascii=False, indent=2))
    elif args.command == "scheduler":
        run_scheduler(settings)
    elif args.command == "web":
        from stockbot.webapp import run_web_server
        run_web_server(settings)
    elif args.command == "app":
        import threading
        from stockbot.webapp import run_web_server
        thread = threading.Thread(target=run_scheduler, args=(settings,), daemon=True, name="stockbot-scheduler")
        thread.start()
        run_web_server(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
