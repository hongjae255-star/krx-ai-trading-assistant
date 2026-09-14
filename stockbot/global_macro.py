from __future__ import annotations

import io
import json
import logging
import math
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any

import numpy as np
import pandas as pd
import requests

from .config import Settings, env
from .db import Database

log = logging.getLogger(__name__)


DEFAULT_SERIES = {
    # Policy / Treasury curve
    "fed_funds": "DFF",
    "ust_3m": "DGS3MO",
    "ust_2y": "DGS2",
    "ust_10y": "DGS10",
    "ust_30y": "DGS30",
    "real_10y": "DFII10",
    "breakeven_5y": "T5YIE",
    "breakeven_10y": "T10YIE",
    # Risk / credit
    "vix": "VIXCLS",
    "ig_oas": "BAMLC0A0CM",
    "hy_oas": "BAMLH0A0HYM2",
    "nfci": "NFCI",
    # FX
    "usd_broad": "DTWEXBGS",
    "usdkrw": "DEXKOUS",
    # Liquidity
    "fed_assets": "WALCL",
    "reverse_repo": "RRPONTSYD",
    "treasury_general_account": "WTREGEN",
    # Commodities
    "wti": "DCOILWTICO",
    # GOLDAMGBD228NLBM was retired from FRED. Use the Kansas City Fed
    # Gold & USD risk-on/off component instead; live gold price comes from GLD.
    "gold_usd_risk_on": "KCROROG",
}


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, float(x)))))


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b else 0.0


class FREDClient:
    """Free macro reader.

    If FRED_API_KEY exists, use the official API. Otherwise use FRED's public
    graph CSV export for current/live snapshots. Vintage-safe historical
    backfills require an API key; the trading model remains leakage-safe by
    learning from snapshots captured prospectively by this program.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = env("FRED_API_KEY")
        self.timeout = float(settings.get("global_macro.http_timeout_seconds", 15))
        self.connect_timeout = float(settings.get("global_macro.http_connect_timeout_seconds", 5))
        self.max_retries = int(settings.get("global_macro.max_retries", 2))
        self.retry_backoff = float(settings.get("global_macro.retry_backoff_seconds", 1.5))
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "KRX-AI-Trading-Assistant/7.4"})

    @property
    def api_mode(self) -> bool:
        return bool(self.api_key)

    def _get_with_retry(self, url: str, *, params: dict[str, Any]) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._session.get(
                    url,
                    params=params,
                    timeout=(self.connect_timeout, self.timeout),
                )
                if r.status_code in {429, 500, 502, 503, 504}:
                    raise requests.HTTPError(
                        f"retryable HTTP {r.status_code}: {r.text[:250]}", response=r
                    )
                r.raise_for_status()
                return r
            except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                sleep_s = self.retry_backoff * (2 ** attempt) + random.uniform(0.0, 0.4)
                time.sleep(sleep_s)
        assert last_exc is not None
        raise last_exc

    def _official_api_series(self, series_id: str, start: str, as_of: str | None) -> pd.DataFrame:
        params: dict[str, Any] = {
            "series_id": series_id,
            "api_key": self.api_key,
            "file_type": "json",
            "observation_start": start,
            "sort_order": "asc",
        }
        if as_of:
            params["realtime_start"] = as_of
            params["realtime_end"] = as_of
        r = self._get_with_retry(
            "https://api.stlouisfed.org/fred/series/observations", params=params
        )
        rows = r.json().get("observations", [])
        return pd.DataFrame({
            "date": [x.get("date") for x in rows],
            "value": [x.get("value") for x in rows],
        })

    def _graph_csv_series(self, series_id: str, start: str) -> pd.DataFrame:
        r = self._get_with_retry(
            "https://fred.stlouisfed.org/graph/fredgraph.csv",
            params={"id": series_id, "cosd": start},
        )
        raw = pd.read_csv(io.StringIO(r.text))
        if raw.empty:
            return pd.DataFrame(columns=["date", "value"])
        date_col = raw.columns[0]
        value_col = raw.columns[-1]
        return raw.rename(columns={date_col: "date", value_col: "value"})[["date", "value"]]

    def series(self, series_id: str, days: int = 120, as_of: str | None = None) -> pd.DataFrame:
        start = (datetime.now(timezone.utc).date() - timedelta(days=max(days * 2, 180))).isoformat()
        if self.api_key:
            try:
                df = self._official_api_series(series_id, start, as_of)
            except Exception:
                # For current snapshots only, fall back to public graph CSV if the
                # official API is temporarily unavailable. Historical as-of queries
                # must never fall back because that would lose vintage safety.
                if as_of is not None:
                    raise
                df = self._graph_csv_series(series_id, start)
        else:
            df = self._graph_csv_series(series_id, start)
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        return df.dropna(subset=["value"]).tail(days).reset_index(drop=True)


class GlobalMacroEngine:
    """Compress rates/FX/credit/liquidity/volatility into stable model features."""

    STATE_KEY = "global_macro_latest"

    def __init__(self, settings: Settings, db: Database, kis: Any | None = None):
        self.settings = settings
        self.db = db
        self.kis = kis
        self.fred = FREDClient(settings)
        self.series_map = dict(DEFAULT_SERIES)
        self.series_map.update(settings.get("global_macro.series", {}) or {})
        self.cache_seconds = int(settings.get("global_macro.cache_seconds", 900))
        self._cached: dict[str, Any] | None = None
        self._cached_at = 0.0
        self.live_cache_seconds = int(settings.get("global_macro.live_cache_seconds", 600))
        self._live_cached: dict[str, Any] | None = None
        self._live_cached_at = 0.0

    @staticmethod
    def _stats(df: pd.DataFrame) -> dict[str, float]:
        if df is None or df.empty:
            return {"value": 0.0, "chg_1": 0.0, "chg_5": 0.0, "pct_1": 0.0, "pct_5": 0.0, "z60": 0.0}
        vals = pd.to_numeric(df["value"], errors="coerce").dropna()
        if vals.empty:
            return {"value": 0.0, "chg_1": 0.0, "chg_5": 0.0, "pct_1": 0.0, "pct_5": 0.0, "z60": 0.0}
        cur = float(vals.iloc[-1])
        p1 = float(vals.iloc[-2]) if len(vals) >= 2 else cur
        p5 = float(vals.iloc[-6]) if len(vals) >= 6 else float(vals.iloc[0])
        hist = vals.tail(60)
        sd = float(hist.std(ddof=0)) if len(hist) >= 5 else 0.0
        z = (cur - float(hist.mean())) / sd if sd > 1e-12 else 0.0
        return {
            "value": cur,
            "chg_1": cur - p1,
            "chg_5": cur - p5,
            "pct_1": _pct(cur, p1),
            "pct_5": _pct(cur, p5),
            "z60": max(-5.0, min(5.0, z)),
        }

    def snapshot(self, force: bool = False, as_of: str | None = None) -> dict[str, Any]:
        now = time.monotonic()
        if not force and as_of is None and self._cached and now - self._cached_at < self.cache_seconds:
            return self._cached
        rows: dict[str, dict[str, float]] = {}
        failures: list[str] = []
        stale_series: list[str] = []
        previous = self.db.get_state(self.STATE_KEY, {}) if as_of is None else {}
        previous_series = previous.get("series", {}) if isinstance(previous, dict) else {}

        # Fetch independently so one slow FRED endpoint cannot stall ~20 series
        # sequentially for several minutes on a short-lived GitHub Actions runner.
        workers = max(1, min(int(self.settings.get("global_macro.max_workers", 6)), len(self.series_map)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="fred") as pool:
            futures = {
                pool.submit(self.fred.series, str(sid), 120, as_of): (name, sid)
                for name, sid in self.series_map.items()
            }
            for fut in as_completed(futures):
                name, sid = futures[fut]
                try:
                    rows[name] = self._stats(fut.result())
                except Exception as exc:
                    log.warning("macro series failed %s/%s: %s", name, sid, exc)
                    failures.append(name)
                    old_stats = previous_series.get(name) if isinstance(previous_series, dict) else None
                    if isinstance(old_stats, dict) and old_stats:
                        rows[name] = dict(old_stats)
                        stale_series.append(name)
                    else:
                        rows[name] = {
                            "value": 0.0, "chg_1": 0.0, "chg_5": 0.0,
                            "pct_1": 0.0, "pct_5": 0.0, "z60": 0.0,
                        }

        def v(name: str, field: str = "value") -> float:
            return float(rows.get(name, {}).get(field, 0.0) or 0.0)

        # Rate pressure: daily yield increases are a headwind to duration/growth assets.
        rate_easing = _clip01(0.5 + 0.25 * math.tanh(-v("ust_2y", "chg_1") / 0.08) + 0.25 * math.tanh(-v("ust_10y", "chg_1") / 0.08))
        curve_2s10s = v("ust_10y") - v("ust_2y")
        curve_health = _clip01(_sigmoid((curve_2s10s + 0.15) / 0.35))
        real_rate_safety = _clip01(_sigmoid((2.2 - v("real_10y")) / 0.45)) if v("real_10y") else 0.5
        be10 = v("breakeven_10y")
        inflation_stability = _clip01(math.exp(-((be10 - 2.30) / 0.65) ** 2)) if be10 else 0.5
        volatility_safety = _clip01(_sigmoid((22.0 - v("vix")) / 4.5)) if v("vix") else 0.5
        credit_safety = _clip01(0.55 * _sigmoid((4.4 - v("hy_oas")) / 0.9) + 0.45 * _sigmoid((1.35 - v("ig_oas")) / 0.35)) if (v("hy_oas") or v("ig_oas")) else 0.5
        financial_conditions_safety = _clip01(_sigmoid((0.30 - v("nfci")) / 0.35)) if v("nfci") else 0.5
        usd_safety = _clip01(0.5 + 0.5 * math.tanh(-v("usd_broad", "pct_5") / 1.8)) if v("usd_broad") else 0.5
        krw_safety = _clip01(0.5 + 0.5 * math.tanh(-v("usdkrw", "pct_5") / 2.0)) if v("usdkrw") else 0.5

        # Liquidity impulse: Fed assets up / RRP and TGA down is generally more supportive.
        fed = v("fed_assets", "pct_5")
        rrp = v("reverse_repo", "pct_5")
        tga = v("treasury_general_account", "pct_5")
        liquidity_support = _clip01(_sigmoid((0.8 * fed - 0.25 * rrp - 0.25 * tga) / 1.5))
        oil_stability = _clip01(math.exp(-((abs(v("wti", "pct_5")) - 1.0) / 7.0) ** 2)) if v("wti") else 0.5
        # KCROROG is a daily gold+USD risk-appetite component. The actual
        # intraday gold-price proxy is GLD in live_snapshot(), so this avoids
        # relying on the retired FRED gold-price series.
        gold_risk_signal = _clip01(0.5 + 0.5 * math.tanh(v("gold_usd_risk_on") / 1.5)) if v("gold_usd_risk_on") else 0.5

        global_risk_on = _clip01(
            0.19 * volatility_safety
            + 0.17 * credit_safety
            + 0.14 * rate_easing
            + 0.10 * real_rate_safety
            + 0.10 * usd_safety
            + 0.10 * financial_conditions_safety
            + 0.10 * liquidity_support
            + 0.05 * curve_health
            + 0.05 * inflation_stability
        )
        macro_stress = _clip01(1.0 - global_risk_on)
        features = {
            "macro_global_risk_on": global_risk_on,
            "macro_rate_easing": rate_easing,
            "macro_curve_health": curve_health,
            "macro_real_rate_safety": real_rate_safety,
            "macro_inflation_stability": inflation_stability,
            "macro_volatility_safety": volatility_safety,
            "macro_credit_safety": credit_safety,
            "macro_financial_conditions_safety": financial_conditions_safety,
            "macro_usd_safety": usd_safety,
            "macro_krw_safety": krw_safety,
            "macro_liquidity_support": liquidity_support,
            "macro_oil_stability": oil_stability,
            "macro_gold_risk_signal": gold_risk_signal,
            "macro_stress": macro_stress,
        }
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": "FRED_API" if self.fred.api_mode else "FRED_GRAPH_CSV",
            "vintage_safe": bool(self.fred.api_mode and as_of),
            "as_of": as_of,
            "failures": failures,
            "stale_series": stale_series,
            "fresh_series_count": len(self.series_map) - len(failures),
            "stale_series_count": len(stale_series),
            "series": rows,
            "features": features,
            "summary": (
                f"risk-on {global_risk_on:.2f} | VIX {v('vix'):.2f} | UST2Y {v('ust_2y'):.2f}% | "
                f"UST10Y {v('ust_10y'):.2f}% | 2s10s {curve_2s10s:+.2f}%p | USD/KRW {v('usdkrw'):.2f} | HY OAS {v('hy_oas'):.2f}%"
            ),
        }
        if as_of is None:
            self._cached, self._cached_at = snapshot, now
            self.db.set_state(self.STATE_KEY, snapshot)
        return snapshot

    def live_snapshot(self, force: bool = False) -> dict[str, Any]:
        """Intraday cross-asset proxy layer from read-only KIS overseas quotes.

        If KIS is temporarily unreachable from a cloud runner, preserve the last good
        cross-asset snapshot instead of spending minutes retrying every ETF and then
        collapsing the factor layer to zeros.
        """
        now = time.monotonic()
        if not force and self._live_cached and now - self._live_cached_at < self.live_cache_seconds:
            return self._live_cached
        proxies = self.settings.get("global_macro.live_proxies", {}) or {
            "spy": {"symbol": "SPY", "exchange": "NYS"},
            "qqq": {"symbol": "QQQ", "exchange": "NAS"},
            "semis": {"symbol": "SMH", "exchange": "NAS"},
            "smallcaps": {"symbol": "IWM", "exchange": "NYS"},
            "long_bonds": {"symbol": "TLT", "exchange": "NAS"},
            "high_yield": {"symbol": "HYG", "exchange": "NYS"},
            "dollar": {"symbol": "UUP", "exchange": "NYS"},
            "gold": {"symbol": "GLD", "exchange": "NYS"},
            "oil": {"symbol": "USO", "exchange": "NYS"},
        }
        previous = self.db.get_state("global_macro_live_latest", {}) or {}
        previous_rows = dict(previous.get("rows", {}) or {})
        rows: dict[str, Any] = {}
        failures: list[str] = []
        network_unavailable = False

        if self.kis is not None:
            for name, spec in proxies.items():
                symbol = str(spec["symbol"])
                preferred = str(spec.get("exchange", "NAS"))
                exchanges = [preferred] + [x for x in ("NAS", "NYS", "AMS") if x != preferred]
                last_exc = None
                for exchange in exchanges:
                    try:
                        q = self.kis.overseas_current_price(symbol, exchange)
                        price = float(q.get("price", 0) or 0)
                        if price <= 0:
                            continue
                        rows[name] = {
                            "symbol": symbol,
                            "exchange": exchange,
                            "price": price,
                            "change_pct": float(q.get("change_pct", 0) or 0),
                        }
                        break
                    except KISNetworkError as exc:
                        last_exc = exc
                        network_unavailable = True
                        # Exchange fallbacks cannot fix a TCP/connectivity outage.
                        break
                    except Exception as exc:
                        last_exc = exc
                        # API-level errors may still be exchange-code specific, so try the next exchange.
                        continue
                if name not in rows:
                    failures.append(name)
                    if name in previous_rows:
                        rows[name] = dict(previous_rows[name])
                        rows[name]["stale"] = True
                    log.warning("live macro proxy failed %s/%s: %s", name, symbol, last_exc or "no quote")
                if network_unavailable:
                    # Reuse previous values for all remaining proxies and stop hammering KIS.
                    for remaining_name in proxies:
                        if remaining_name not in rows and remaining_name in previous_rows:
                            rows[remaining_name] = dict(previous_rows[remaining_name])
                            rows[remaining_name]["stale"] = True
                        if remaining_name not in rows and remaining_name not in failures:
                            failures.append(remaining_name)
                    break

        def chg(name: str) -> float:
            return float(rows.get(name, {}).get("change_pct", 0.0) or 0.0)

        live = {
            "market_spy_strength": _clip01(0.5 + 0.5 * math.tanh(chg("spy") / 1.5)),
            "market_nasdaq_strength": _clip01(0.5 + 0.5 * math.tanh(chg("qqq") / 1.8)),
            "market_semis_strength": _clip01(0.5 + 0.5 * math.tanh(chg("semis") / 2.2)),
            "market_smallcap_strength": _clip01(0.5 + 0.5 * math.tanh(chg("smallcaps") / 1.8)),
            "market_bond_bid": _clip01(0.5 + 0.5 * math.tanh(chg("long_bonds") / 1.4)),
            "market_credit_bid": _clip01(0.5 + 0.5 * math.tanh(chg("high_yield") / 0.9)),
            "market_dollar_weakness": _clip01(0.5 + 0.5 * math.tanh(-chg("dollar") / 0.8)),
            "market_gold_bid": _clip01(0.5 + 0.5 * math.tanh(chg("gold") / 1.4)),
            "market_oil_impulse": _clip01(0.5 + 0.5 * math.tanh(chg("oil") / 2.5)),
        }
        live_risk_on = _clip01(
            0.24*live["market_spy_strength"] + 0.22*live["market_nasdaq_strength"]
            + 0.16*live["market_semis_strength"] + 0.10*live["market_smallcap_strength"]
            + 0.10*live["market_credit_bid"] + 0.08*live["market_bond_bid"]
            + 0.10*live["market_dollar_weakness"]
        )
        live["market_live_risk_on"] = live_risk_on
        snap = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rows": rows,
            "features": live,
            "failures": failures,
            "stale": bool(network_unavailable),
            "network_unavailable": bool(network_unavailable),
            "source": "KIS" if not network_unavailable else "KIS_PREVIOUS_SNAPSHOT_FALLBACK",
        }
        self._live_cached, self._live_cached_at = snap, now
        # Do not overwrite the last good persistent snapshot with a full network-outage fallback.
        if not network_unavailable or not previous:
            self.db.set_state("global_macro_live_latest", snap)
        return snap

    def latest(self) -> dict[str, Any]:
        if self._cached:
            return self._cached
        saved = self.db.get_state(self.STATE_KEY, {}) or {}
        return saved if saved else self.snapshot()

    def equity_features(self, market: str = "KR") -> dict[str, float]:
        snap = self.latest()
        f = dict(snap.get("features", {}) or {})
        live = self.live_snapshot() if self.kis is not None else (self.db.get_state("global_macro_live_latest", {}) or {})
        live_f = dict(live.get("features", {}) or {})
        f.update(live_f)
        if "market_live_risk_on" in live_f:
            f["macro_global_risk_on"] = _clip01(0.65 * float(f.get("macro_global_risk_on", 0.5)) + 0.35 * float(live_f["market_live_risk_on"]))
        # Keep the legacy features for backward compatibility while adding richer macro fields.
        out = dict(f)
        out["global_risk_on"] = float(f.get("macro_global_risk_on", 0.5))
        out["macro_event_safety"] = 1.0 - float(f.get("macro_stress", 0.5))
        if market.upper() == "KR":
            out["macro_domestic_fx_support"] = float(f.get("macro_krw_safety", 0.5))
        else:
            out["macro_domestic_fx_support"] = float(f.get("macro_usd_safety", 0.5))
        return out
