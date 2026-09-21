from __future__ import annotations

from pathlib import Path
import pandas as pd

from stockbot.config import load_settings
from stockbot.global_macro import GlobalMacroEngine
from stockbot.kis import KISClient
from stockbot.predictor import DEFAULT_FEATURES
from stockbot.scheduler import _is_interval_time


class DummyDB:
    def __init__(self): self.state = {}
    def set_state(self, key, value): self.state[key] = value
    def get_state(self, key, default=None): return self.state.get(key, default)


class DummySettings:
    def get(self, key, default=None):
        vals = {
            "global_macro.cache_seconds": 900,
            "global_macro.live_cache_seconds": 600,
            "global_macro.series": {},
            "global_macro.live_proxies": {
                "spy": {"symbol":"SPY","exchange":"NYS"},
                "qqq": {"symbol":"QQQ","exchange":"NAS"},
                "semis": {"symbol":"SMH","exchange":"NAS"},
                "smallcaps": {"symbol":"IWM","exchange":"NYS"},
                "long_bonds": {"symbol":"TLT","exchange":"NAS"},
                "high_yield": {"symbol":"HYG","exchange":"NYS"},
                "dollar": {"symbol":"UUP","exchange":"NYS"},
                "gold": {"symbol":"GLD","exchange":"NYS"},
                "oil": {"symbol":"USO","exchange":"NYS"},
            },
        }
        return vals.get(key, default)


class DummyKIS:
    CHG = {"SPY":1.2,"QQQ":1.5,"SMH":2.0,"IWM":0.8,"TLT":0.4,"HYG":0.3,"UUP":-0.2,"GLD":0.1,"USO":-0.5}
    def overseas_current_price(self, symbol, exchange):
        return {"price":100.0,"change_pct":self.CHG[symbol]}


def test_macro_stats_and_live_proxy_features_are_bounded():
    df = pd.DataFrame({"value": [100,101,102,103,104,105]})
    st = GlobalMacroEngine._stats(df)
    assert st["value"] == 105
    assert st["chg_1"] == 1
    eng = GlobalMacroEngine.__new__(GlobalMacroEngine)
    eng.settings = DummySettings(); eng.db = DummyDB(); eng.kis = DummyKIS()
    eng.live_cache_seconds = 600; eng._live_cached = None; eng._live_cached_at = 0.0
    live = eng.live_snapshot(force=True)
    assert live["features"]["market_live_risk_on"] > 0.5
    assert all(0 <= x <= 1 for x in live["features"].values())


def test_v6_config_enables_us_and_dst_aware_schedule():
    root = Path(__file__).resolve().parents[1]
    s = load_settings(root)
    assert s.get("us_market.enabled") is True
    assert s.get("us_market.timezone") == "America/New_York"
    assert s.get("us_market.scheduler.active_interval_minutes") == 15
    assert _is_interval_time("09:40", "09:40", "15:50", 10)
    assert _is_interval_time("15:50", "09:40", "15:50", 10)
    assert not _is_interval_time("09:45", "09:40", "15:50", 10)


def test_predictor_includes_cross_asset_features():
    needed = {"macro_global_risk_on","macro_rate_easing","macro_credit_safety","market_live_risk_on","market_semis_strength","market_bond_bid","market_credit_bid","market_dollar_weakness"}
    assert needed.issubset(set(DEFAULT_FEATURES))


def test_kis_has_read_only_us_market_methods():
    for name in ["overseas_current_price","overseas_turnover_rank","overseas_daily_chart","overseas_intraday_chart"]:
        assert hasattr(KISClient, name)


def test_no_order_endpoints_in_v6():
    root = Path(__file__).resolve().parents[1] / "stockbot"
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*.py"))
    forbidden = ["order-cash", "order-rvsecncl", "/uapi/overseas-stock/v1/trading/order"]
    assert all(x not in text for x in forbidden)
