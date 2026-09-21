from __future__ import annotations

from pathlib import Path

from stockbot.cloud_state import SupabaseStorage
from stockbot.config import Settings, load_settings
from stockbot.global_macro import DEFAULT_SERIES


def test_retired_fred_gold_series_removed_and_replaced():
    assert "GOLDAMGBD228NLBM" not in DEFAULT_SERIES.values()
    assert DEFAULT_SERIES.get("gold_usd_risk_on") == "KCROROG"


def test_cloud_config_uses_15_minute_monitoring():
    root = Path(__file__).resolve().parents[1]
    s = load_settings(root)
    assert s.get("cloud.enabled") is True
    assert s.get("monitoring.active_interval_minutes") == 15
    assert s.get("us_market.scheduler.active_interval_minutes") == 15


def test_supabase_secret_key_uses_storage_auth_headers(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_example")
    s = Settings(tmp_path, {"cloud": {"http_timeout_seconds": 3}})
    c = SupabaseStorage(s)
    h = c._headers("application/json")
    assert h["apikey"] == "sb_secret_example"
    assert h["Authorization"] == "Bearer sb_secret_example"


def test_supabase_legacy_service_role_keeps_bearer(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "eyJlegacy")
    s = Settings(tmp_path, {"cloud": {"http_timeout_seconds": 3}})
    c = SupabaseStorage(s)
    h = c._headers()
    assert h["apikey"] == "eyJlegacy"
    assert h["Authorization"] == "Bearer eyJlegacy"


def test_pwa_assets_are_relative_for_storage_hosting():
    root = Path(__file__).resolve().parents[1] / "web"
    html = (root / "index.html").read_text(encoding="utf-8")
    manifest = (root / "manifest.webmanifest").read_text(encoding="utf-8")
    js = (root / "app.js").read_text(encoding="utf-8")
    assert 'href="./styles.css"' in html
    assert 'src="./runtime-config.js"' in html
    assert '"start_url": "./index.html"' in manifest
    assert "window.KRX_CLOUD_MODE" in js

def test_personal_settings_can_come_from_environment(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv('EXISTING_POSITIONS_JSON', '[{"code":"028300","name":"HLB","quantity":221,"average_price":52800,"risk_class":"high"}]')
    monkeypatch.setenv('DAY_TRADE_CAPITAL_KRW', '7000000')
    s = load_settings(root)
    assert s.get('existing_positions.0.code') is None  # dotted getter does not index lists
    assert s.get('existing_positions')[0]['quantity'] == 221
    assert s.get('risk.day_trade_capital_krw') == 7000000


def _resp(status: int, body: str):
    import requests
    r = requests.Response()
    r.status_code = status
    r._content = body.encode("utf-8")
    r.reason = "Bad Request" if status == 400 else "Not Found"
    return r


def test_supabase_400_nosuchbucket_is_treated_as_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_example")
    s = Settings(tmp_path, {"cloud": {"http_timeout_seconds": 3}})
    c = SupabaseStorage(s)
    r = _resp(400, '{"statusCode":"404","error":"Bucket not found","message":"Bucket not found","code":"NoSuchBucket"}')
    assert c._is_not_found(r) is True


def test_supabase_real_400_is_not_silenced(monkeypatch, tmp_path):
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_example")
    s = Settings(tmp_path, {"cloud": {"http_timeout_seconds": 3}})
    c = SupabaseStorage(s)
    r = _resp(400, '{"statusCode":"400","error":"Bad Request","message":"invalid payload"}')
    assert c._is_not_found(r) is False
