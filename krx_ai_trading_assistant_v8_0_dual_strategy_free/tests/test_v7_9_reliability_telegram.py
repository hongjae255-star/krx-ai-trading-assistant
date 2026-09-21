from __future__ import annotations

from pathlib import Path

from stockbot.global_macro import GlobalMacroEngine


class DummyDB:
    def __init__(self, state=None):
        self.state = state or {}
    def get_state(self, key, default=None):
        return self.state.get(key, default)
    def set_state(self, key, value):
        self.state[key] = value


class DummySettings:
    def get(self, key, default=None):
        vals = {
            "global_macro.cache_seconds": 0,
            "global_macro.live_cache_seconds": 0,
            "global_macro.max_workers_api": 2,
            "global_macro.max_workers_graph": 1,
        }
        return vals.get(key, default)


class DownFred:
    api_mode = True
    api_key_status = "valid"
    def __init__(self):
        self.series_calls = 0
    def probe(self):
        return {"status": "down", "transport": "down", "api_error": "timeout", "graph_error": "timeout"}
    def series(self, *args, **kwargs):
        self.series_calls += 1
        raise AssertionError("series fanout should not run when preflight is down")


def _engine(previous):
    eng = GlobalMacroEngine.__new__(GlobalMacroEngine)
    eng.settings = DummySettings()
    eng.db = DummyDB({"global_macro_latest": previous})
    eng.kis = None
    eng.fred = DownFred()
    eng.series_map = {"ust_2y": "DGS2", "vix": "VIXCLS"}
    eng.cache_seconds = 0
    eng._cached = None
    eng._cached_at = 0.0
    eng.live_cache_seconds = 0
    eng._live_cached = None
    eng._live_cached_at = 0.0
    return eng


def test_fred_preflight_down_skips_19_style_fanout_and_reuses_previous():
    previous = {
        "series": {
            "ust_2y": {"value": 4.2, "chg_1": 0.01, "chg_5": 0.03, "pct_1": 0.2, "pct_5": 0.7, "z60": 0.1},
            "vix": {"value": 18.0, "chg_1": -0.2, "chg_5": -0.5, "pct_1": -1.0, "pct_5": -2.7, "z60": -0.2},
        }
    }
    eng = _engine(previous)
    snap = eng.snapshot(force=True)
    assert eng.fred.series_calls == 0
    assert snap["endpoint_status"] == "down"
    assert snap["source"] == "FRED_PREVIOUS_SNAPSHOT"
    assert snap["fresh_series_count"] == 0
    assert snap["stale_series_count"] == 2
    assert snap["hard_failure_count"] == 0


def test_every_intraday_cloud_run_forces_telegram_heartbeat():
    root = Path(__file__).resolve().parents[1]
    cloud = (root / "stockbot/cloud_runner.py").read_text(encoding="utf-8")
    assert 'bot.intraday(scan_replacement=full_scan, force_summary=True)' in cloud
    assert 'us.intraday(force_summary=True, scan_replacement=full_scan)' in cloud

    jobs = (root / "stockbot/jobs.py").read_text(encoding="utf-8")
    us = (root / "stockbot/us_market.py").read_text(encoding="utf-8")
    assert "✅ 15분 모니터 정상 실행" in jobs
    assert "✅ 15분 모니터 정상 실행" in us
    assert "📭 현재 정식 추천 종목 없음" in jobs
    assert "📭 현재 정식 추천 종목 없음" in us


def test_intraday_workflows_include_fail_fast_and_failure_telegram_alerts():
    root = Path(__file__).resolve().parents[1]
    for rel in [".github/workflows/krx-intraday.yml", ".github/workflows/us-intraday.yml"]:
        text = (root / rel).read_text(encoding="utf-8")
        assert "KIS_MAX_RETRIES: '2'" in text
        assert "KIS_CONNECT_TIMEOUT: '6'" in text
        assert "Telegram failure alert" in text
        assert "timeout 600s python -m stockbot.cloud_runner" in text
        assert "TELEGRAM_BOT_TOKEN" in text and "TELEGRAM_CHAT_ID" in text


def test_manual_refresh_does_not_force_daily_fred_refetch():
    root = Path(__file__).resolve().parents[1]
    cloud = (root / "stockbot/cloud_runner.py").read_text(encoding="utf-8")
    # Both refresh branches should use the persisted 15-minute macro cache rather
    # than hammering all FRED series on every phone refresh.
    assert cloud.count("macro = bot.macro.snapshot(force=False)") >= 2
