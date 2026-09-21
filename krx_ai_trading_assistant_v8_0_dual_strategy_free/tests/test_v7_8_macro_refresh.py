from __future__ import annotations

from pathlib import Path

import pytest

from stockbot.config import Settings
from stockbot.global_macro import FREDClient, GlobalMacroEngine


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


class FailingFred:
    api_mode = True
    api_key_status = "valid"
    def series(self, *args, **kwargs):
        raise TimeoutError("temporary")


def test_macro_transport_failure_uses_previous_without_hard_failure():
    previous_series = {
        "ust_2y": {"value": 4.2, "chg_1": 0.01, "chg_5": 0.03, "pct_1": 0.2, "pct_5": 0.7, "z60": 0.1},
        "vix": {"value": 18.0, "chg_1": -0.2, "chg_5": -0.5, "pct_1": -1.0, "pct_5": -2.7, "z60": -0.2},
    }
    db = DummyDB({"global_macro_latest": {"series": previous_series}})
    eng = GlobalMacroEngine.__new__(GlobalMacroEngine)
    eng.settings = DummySettings()
    eng.db = db
    eng.kis = None
    eng.fred = FailingFred()
    eng.series_map = {"ust_2y": "DGS2", "vix": "VIXCLS"}
    eng.cache_seconds = 0
    eng._cached = None
    eng._cached_at = 0.0
    eng.live_cache_seconds = 0
    eng._live_cached = None
    eng._live_cached_at = 0.0

    snap = eng.snapshot(force=True)
    assert snap["hard_failure_count"] == 0
    assert snap["failures"] == []
    assert snap["stale_series_count"] == 2
    assert set(snap["stale_series"]) == {"ust_2y", "vix"}
    assert snap["transport_failure_count"] == 2
    assert snap["series"]["ust_2y"]["value"] == 4.2


def test_fred_key_validation(monkeypatch, tmp_path):
    s = Settings(tmp_path, {"global_macro": {}})
    monkeypatch.setenv("FRED_API_KEY", "A" * 32)
    bad = FREDClient(s)
    assert bad.api_mode is False
    assert bad.api_key_status == "invalid"

    monkeypatch.setenv("FRED_API_KEY", "a1" * 16)
    good = FREDClient(s)
    assert good.api_mode is True
    assert good.api_key_status == "valid"


def test_manual_refresh_files_and_runtime_bridge_exist():
    root = Path(__file__).resolve().parents[1]
    edge = (root / "supabase/functions/manual-refresh/index.ts").read_text(encoding="utf-8")
    app = (root / "web/app.js").read_text(encoding="utf-8")
    deploy = (root / ".github/workflows/deploy-pwa.yml").read_text(encoding="utf-8")
    manual = (root / ".github/workflows/cloud-manual.yml").read_text(encoding="utf-8")
    assert "GITHUB_ACTIONS_TOKEN" in edge
    assert "MANUAL_REFRESH_KEY" in edge
    assert "window.KRX_REFRESH_ENDPOINT" in deploy
    assert "manualRefresh" in app
    assert "refresh-kr" in manual and "refresh-us" in manual
    # Never expose the GitHub token in public PWA source/runtime config.
    public_text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in (root / "web").rglob("*") if p.is_file())
    assert "GITHUB_ACTIONS_TOKEN" not in public_text


def test_edge_function_disables_platform_jwt_and_uses_own_key():
    root = Path(__file__).resolve().parents[1]
    cfg = (root / "supabase/config.toml").read_text(encoding="utf-8")
    assert "[functions.manual-refresh]" in cfg
    assert "verify_jwt = false" in cfg
