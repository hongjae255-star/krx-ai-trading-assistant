from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import pandas as pd

from .config import Settings
from .db import Database
from .kis import KISClient, KISNetworkError

log = logging.getLogger(__name__)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _pct(a: float, b: float) -> float:
    return (a / b - 1.0) * 100.0 if b else 0.0


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MarketPulseService:
    """Compact market-index layer for the mobile dashboard.

    Primary sources are read-only KIS index APIs. For US indices only, ETF proxies
    are used when a specific index symbol is unavailable. Previous good state is
    retained on temporary KIS/network failures so one bad request cannot blank the UI.
    """

    KR_INDEXES = {
        "kospi": {"name": "KOSPI", "code": "0001"},
        "kosdaq": {"name": "KOSDAQ", "code": "1001"},
    }
    US_INDEXES = {
        "nasdaq": {"name": "NASDAQ", "symbol": ".IXIC", "proxy": ("QQQ", "NAS")},
        "sp500": {"name": "S&P 500", "symbol": ".SPX", "proxy": ("SPY", "NYS")},
        "dow": {"name": "Dow", "symbol": ".DJI", "proxy": ("DIA", "NYS")},
        "russell2000": {"name": "Russell 2000", "symbol": ".RUT", "proxy": ("IWM", "NYS")},
    }

    def __init__(self, settings: Settings, kis: KISClient, db: Database):
        self.settings = settings
        self.kis = kis
        self.db = db
        self.state_key = "market_pulse_latest"
        self.ttl_minutes = int(settings.get("market_dashboard.index_refresh_minutes", 30))
        self.days = int(settings.get("market_dashboard.index_chart_days", 30))

    @staticmethod
    def _age_minutes(ts: str | None) -> float:
        if not ts:
            return 1e9
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 60.0)
        except Exception:
            return 1e9

    def _series(self, df: pd.DataFrame, name: str, source: str, proxy: bool = False) -> dict[str, Any]:
        if df is None or df.empty or "close" not in df.columns:
            raise ValueError(f"empty index data: {name}")
        x = df.copy()
        x["close"] = pd.to_numeric(x["close"], errors="coerce")
        x = x.dropna(subset=["close"])
        if x.empty:
            raise ValueError(f"no close values: {name}")
        if "date" in x.columns:
            x = x.sort_values("date")
        x = x.tail(max(22, self.days)).reset_index(drop=True)
        closes = x["close"].astype(float).tolist()
        latest = closes[-1]
        prev = closes[-2] if len(closes) >= 2 else latest
        base5 = closes[-6] if len(closes) >= 6 else closes[0]
        base20 = closes[-21] if len(closes) >= 21 else closes[0]
        ma20 = sum(closes[-20:]) / min(20, len(closes))
        points = []
        for _, r in x.tail(self.days).iterrows():
            raw_date = str(r.get("date", ""))
            if len(raw_date) == 8 and raw_date.isdigit():
                date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
            else:
                date = raw_date[:10]
            points.append({"date": date, "close": round(_f(r.get("close")), 4)})
        ret5 = _pct(latest, base5)
        dist = _pct(latest, ma20)
        trend = "상승" if ret5 > 0.7 and dist > 0 else "하락" if ret5 < -0.7 and dist < 0 else "중립"
        return {
            "name": name,
            "source": source,
            "proxy": bool(proxy),
            "latest": round(latest, 4),
            "change_1d_pct": round(_pct(latest, prev), 3),
            "return_5d_pct": round(ret5, 3),
            "return_20d_pct": round(_pct(latest, base20), 3),
            "ma20": round(ma20, 4),
            "distance_ma20_pct": round(dist, 3),
            "above_ma20": bool(latest >= ma20),
            "trend": trend,
            "points": points,
            "stale": False,
        }

    def _kr(self, key: str, cfg: dict[str, str]) -> dict[str, Any]:
        df = self.kis.domestic_index_daily(cfg["code"], days=self.days)
        return self._series(df, cfg["name"], "KIS index", proxy=False)

    def _us(self, key: str, cfg: dict[str, Any]) -> dict[str, Any]:
        try:
            df = self.kis.overseas_index_daily(cfg["symbol"], days=self.days)
            return self._series(df, cfg["name"], "KIS index", proxy=False)
        except KISNetworkError:
            raise
        except Exception as direct_exc:
            sym, ex = cfg["proxy"]
            log.warning("US index direct failed %s/%s; using %s proxy: %s", key, cfg["symbol"], sym, direct_exc)
            df = self.kis.overseas_daily_chart(sym, ex, days=self.days)
            return self._series(df, cfg["name"], f"{sym} ETF proxy", proxy=True)

    @staticmethod
    def _regime(rows: dict[str, dict[str, Any]]) -> dict[str, Any]:
        valid = [r for r in rows.values() if r and r.get("latest")]
        if not valid:
            return {"score": 50, "label": "데이터 대기", "above_ma20": 0, "positive_5d": 0, "total": 0}
        above = sum(1 for r in valid if r.get("above_ma20"))
        pos5 = sum(1 for r in valid if _f(r.get("return_5d_pct")) > 0)
        avg5 = sum(_f(r.get("return_5d_pct")) for r in valid) / len(valid)
        score = 50 + 18 * ((above / len(valid)) - 0.5) * 2 + 14 * ((pos5 / len(valid)) - 0.5) * 2
        score += max(-18, min(18, avg5 * 4.0))
        score = int(round(max(0, min(100, score))))
        label = "Risk-on" if score >= 65 else "Risk-off" if score <= 35 else "Mixed"
        return {"score": score, "label": label, "above_ma20": above, "positive_5d": pos5, "total": len(valid), "avg_5d_pct": round(avg5, 2)}

    def snapshot(self, force: bool = False) -> dict[str, Any]:
        cached = self.db.get_state(self.state_key, {}) or {}
        if not force and cached and self._age_minutes(cached.get("updated_at")) < self.ttl_minutes:
            return cached

        old_indexes = cached.get("indexes", {}) if isinstance(cached, dict) else {}
        indexes: dict[str, dict[str, Any]] = {}
        failures: list[str] = []
        network_down = False

        for key, cfg in self.KR_INDEXES.items():
            try:
                indexes[key] = self._kr(key, cfg)
            except KISNetworkError as exc:
                failures.append(f"{key}: network")
                network_down = True
                log.warning("market pulse KIS network unavailable at %s: %s", key, exc)
                break
            except Exception as exc:
                failures.append(f"{key}: {type(exc).__name__}")
                log.warning("market pulse failed %s: %s", key, exc)
                if key in old_indexes:
                    indexes[key] = {**old_indexes[key], "stale": True}

        if not network_down:
            for key, cfg in self.US_INDEXES.items():
                try:
                    indexes[key] = self._us(key, cfg)
                except KISNetworkError as exc:
                    failures.append(f"{key}: network")
                    network_down = True
                    log.warning("market pulse KIS network unavailable at %s: %s", key, exc)
                    break
                except Exception as exc:
                    failures.append(f"{key}: {type(exc).__name__}")
                    log.warning("market pulse failed %s: %s", key, exc)
                    if key in old_indexes:
                        indexes[key] = {**old_indexes[key], "stale": True}

        # Fill every missing key from the last good snapshot rather than blanking charts.
        for key, old in old_indexes.items():
            if key not in indexes and isinstance(old, dict):
                indexes[key] = {**old, "stale": True}

        kr = {k: indexes[k] for k in ("kospi", "kosdaq") if k in indexes}
        us = {k: indexes[k] for k in ("nasdaq", "sp500", "dow", "russell2000") if k in indexes}
        kospi5 = _f(indexes.get("kospi", {}).get("return_5d_pct"))
        kosdaq5 = _f(indexes.get("kosdaq", {}).get("return_5d_pct"))
        nas5 = _f(indexes.get("nasdaq", {}).get("return_5d_pct"))
        sp5 = _f(indexes.get("sp500", {}).get("return_5d_pct"))
        stale = sum(1 for v in indexes.values() if v.get("stale"))
        out = {
            "updated_at": _iso_now(),
            "indexes": indexes,
            "kr": {
                "regime": self._regime(kr),
                "growth_lead_5d_pct": round(kosdaq5 - kospi5, 2),
                "leadership": "KOSDAQ 우위" if kosdaq5 - kospi5 > 0.5 else "KOSPI 우위" if kosdaq5 - kospi5 < -0.5 else "균형",
            },
            "us": {
                "regime": self._regime(us),
                "growth_lead_5d_pct": round(nas5 - sp5, 2),
                "leadership": "NASDAQ 우위" if nas5 - sp5 > 0.5 else "S&P 우위" if nas5 - sp5 < -0.5 else "균형",
            },
            "failures": failures,
            "stale_count": stale,
            "network_down": network_down,
        }
        # Do not replace a useful cache with an empty outage snapshot.
        if not indexes and cached:
            return {**cached, "failures": failures, "network_down": network_down, "stale_count": len(old_indexes)}
        self.db.set_state(self.state_key, out)
        return out
