from __future__ import annotations

import math
from typing import Any
import numpy as np
import pandas as pd


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-20.0, min(20.0, x))))


def atr(df: pd.DataFrame, period: int = 14) -> float:
    if df is None or df.empty or len(df) < 3:
        return 0.0
    high, low, close = df["high"], df["low"], df["close"]
    prev = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    val = tr.rolling(period, min_periods=min(5, period)).mean().iloc[-1]
    return float(val) if pd.notna(val) else 0.0


def session_vwap(df: pd.DataFrame) -> float:
    if df is None or df.empty or "close" not in df or "volume" not in df:
        return 0.0
    vol = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    if vol.sum() <= 0:
        return float(df["close"].iloc[-1])
    if all(c in df.columns for c in ["high", "low", "close"]):
        typical = (df["high"] + df["low"] + df["close"]) / 3.0
    else:
        typical = df["close"]
    return float((typical * vol).sum() / vol.sum())


def _ret(closes: pd.Series, n: int) -> float:
    if len(closes) < n + 1 or float(closes.iloc[-n - 1]) <= 0:
        return 0.0
    return (float(closes.iloc[-1]) / float(closes.iloc[-n - 1]) - 1.0) * 100.0


def _bounded_momentum(ret_pct: float, scale: float) -> float:
    return clamp01(sigmoid(ret_pct / max(0.1, scale)))


def compute_features(
    quote: dict[str, Any],
    daily: pd.DataFrame,
    foreign_net_krw: float = 0.0,
    institution_net_krw: float = 0.0,
    intraday: pd.DataFrame | None = None,
    news_catalyst: float = 0.5,
    broker_report_signal: float = 0.5,
    event_positive_strength: float = 0.5,
    event_risk_inverse: float = 0.5,
) -> dict[str, float]:
    p = float(quote.get("price", 0) or 0)
    change = float(quote.get("change_pct", 0) or 0)
    turnover = float(quote.get("turnover_krw", 0) or 0)

    # 10B KRW ~= neutral, 200B+ strongly liquid.
    liquidity = clamp01((math.log10(max(turnover, 1.0)) - 10.0) / 1.3)

    turnover_acc = 0.5
    m1 = change
    m5 = m10 = m20 = 0.0
    trend_quality = 0.5
    overheat = 0.5
    volatility_quality = 0.5
    atr_pct_quality = 0.5
    near_20d_high = 0.5
    range_position_20d = 0.5
    close_location_value = 0.5
    volume_pressure = 0.5

    if daily is not None and not daily.empty and "close" in daily:
        d = daily.copy()
        for c in ["open", "high", "low", "close", "volume", "turnover"]:
            if c in d.columns:
                d[c] = pd.to_numeric(d[c], errors="coerce")
        closes = d["close"].astype(float).dropna()
        if len(closes) >= 2 and closes.iloc[-2] > 0:
            m1 = (closes.iloc[-1] / closes.iloc[-2] - 1.0) * 100.0
        m5 = _ret(closes, 5)
        m10 = _ret(closes, 10)
        m20 = _ret(closes, 20)

        ma5 = closes.tail(5).mean() if len(closes) >= 5 else closes.mean()
        ma20 = closes.tail(20).mean() if len(closes) >= 20 else closes.mean()
        slope = (ma5 / ma20 - 1.0) * 100.0 if ma20 else 0.0
        trend_quality = clamp01(sigmoid(slope / 2.0) * 0.65 + sigmoid(m5 / 8.0) * 0.35)

        overheat_penalty = max(abs(m1) / 15.0, max(0.0, m5) / 30.0, max(0.0, m10) / 45.0)
        overheat = clamp01(1.0 - overheat_penalty)

        returns = closes.pct_change().dropna() * 100.0
        vol20 = float(returns.tail(20).std(ddof=0)) if len(returns) >= 5 else 0.0
        # For intraday trading, too little volatility gives no edge and too much raises gap/tail risk.
        # Peak desirability around ~2.4% daily volatility.
        volatility_quality = clamp01(math.exp(-((vol20 - 2.4) / 2.2) ** 2)) if vol20 > 0 else 0.4

        a = atr(d, 14)
        atr_pct = (a / float(closes.iloc[-1]) * 100.0) if len(closes) and closes.iloc[-1] > 0 else 0.0
        atr_pct_quality = clamp01(math.exp(-((atr_pct - 3.0) / 2.5) ** 2)) if atr_pct > 0 else 0.4

        if all(c in d.columns for c in ["high", "low"]):
            high20 = float(d["high"].tail(20).max())
            low20 = float(d["low"].tail(20).min())
            last = float(closes.iloc[-1])
            if high20 > 0:
                distance = max(0.0, (high20 / last - 1.0) * 100.0)
                near_20d_high = clamp01(1.0 - distance / 12.0)
            if high20 > low20:
                range_position_20d = clamp01((last - low20) / (high20 - low20))
            last_row = d.iloc[-1]
            rh, rl, rc = float(last_row.get("high") or 0), float(last_row.get("low") or 0), float(last_row.get("close") or 0)
            if rh > rl:
                close_location_value = clamp01((rc - rl) / (rh - rl))

        if "turnover" in d.columns and len(d) >= 6:
            avg_turn = float(d["turnover"].iloc[-6:-1].replace(0, np.nan).mean() or 0)
            cur_turn = float(d["turnover"].iloc[-1] or 0)
            ratio = cur_turn / avg_turn if avg_turn > 0 else 1.0
            turnover_acc = clamp01(sigmoid((ratio - 1.2) * 1.5))
        if "volume" in d.columns and len(d) >= 6:
            hist = d["volume"].iloc[-21:-1].replace(0, np.nan).dropna()
            curv = float(d["volume"].iloc[-1] or 0)
            if len(hist) >= 5 and float(hist.mean()) > 0:
                ratio = curv / float(hist.mean())
                volume_pressure = clamp01(sigmoid((ratio - 1.0) * 1.4))

    momentum_1d = _bounded_momentum(m1, 3.0)
    momentum_5d = _bounded_momentum(m5, 7.0)
    momentum_10d = _bounded_momentum(m10, 10.0)

    # Flows are scaled by turnover so large caps and small caps are comparable.
    flow_scale = max(turnover, 10_000_000_000.0)
    foreign_flow = clamp01(0.5 + 0.5 * math.tanh(foreign_net_krw / (flow_scale * 0.08)))
    institution_flow = clamp01(0.5 + 0.5 * math.tanh(institution_net_krw / (flow_scale * 0.06)))

    intraday_strength = 0.5
    if intraday is not None and not intraday.empty:
        vwap = session_vwap(intraday)
        last = float(intraday["close"].iloc[-1]) if "close" in intraday else p
        if vwap > 0:
            premium = (last / vwap - 1.0) * 100.0
            intraday_strength = clamp01(sigmoid(premium / 0.7))

    result = {
        "liquidity": liquidity,
        "turnover_acceleration": turnover_acc,
        "momentum_1d": momentum_1d,
        "momentum_5d": momentum_5d,
        "momentum_10d": momentum_10d,
        "trend_quality": trend_quality,
        "foreign_flow": foreign_flow,
        "institution_flow": institution_flow,
        "intraday_strength": intraday_strength,
        "news_catalyst": clamp01(news_catalyst),
        "broker_report_signal": clamp01(broker_report_signal),
        "event_positive_strength": clamp01(event_positive_strength),
        "event_risk_inverse": clamp01(event_risk_inverse),
        "overheating_inverse": overheat,
        "volatility_quality": volatility_quality,
        "atr_pct_quality": atr_pct_quality,
        "near_20d_high": near_20d_high,
        "range_position_20d": range_position_20d,
        "close_location_value": close_location_value,
        "volume_pressure": volume_pressure,
    }



def historical_bar_features(daily: pd.DataFrame) -> dict[str, float]:
    """Raw daily-bar features matching the V9 10-year model schema.

    Only information present in `daily` up to its last row is used.
    """
    if daily is None or daily.empty or len(daily) < 60:
        return {}
    d = daily.copy()
    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    if "date" in d.columns:
        d = d.sort_values("date")
    d = d.dropna(subset=["open", "high", "low", "close"])
    if len(d) < 60:
        return {}
    c = d["close"].astype(float)
    prev = c.shift(1)
    r = c.pct_change()
    tr = pd.concat([(d["high"]-d["low"]), (d["high"]-prev).abs(), (d["low"]-prev).abs()], axis=1).max(axis=1)
    vol = d["volume"].astype(float) if "volume" in d else pd.Series(0.0, index=d.index)
    turnover = d["turnover"].astype(float) if "turnover" in d else c * vol
    high20 = d["high"].rolling(20).max(); low20 = d["low"].rolling(20).min()
    ma5 = c.rolling(5).mean(); ma20 = c.rolling(20).mean(); ma60 = c.rolling(60).mean()
    tmean = turnover.rolling(20).mean(); tstd = turnover.rolling(20).std().replace(0, np.nan)
    day_range = (d["high"]-d["low"]).replace(0, np.nan)
    out = {
        "ret_1d": r.iloc[-1], "ret_5d": c.pct_change(5).iloc[-1], "ret_10d": c.pct_change(10).iloc[-1], "ret_20d": c.pct_change(20).iloc[-1],
        "volatility_10d": r.rolling(10).std().iloc[-1], "volatility_20d": r.rolling(20).std().iloc[-1],
        "atr_14_pct": (tr.rolling(14).mean()/c).iloc[-1],
        "volume_ratio_5_20": (vol.rolling(5).mean()/vol.rolling(20).mean().replace(0,np.nan)).iloc[-1],
        "turnover_proxy_z20": ((turnover-tmean)/tstd).iloc[-1],
        "close_location": ((c-d["low"])/day_range).iloc[-1],
        "range_pos_20": ((c-low20)/(high20-low20).replace(0,np.nan)).iloc[-1],
        "dist_ma5": (c/ma5-1).iloc[-1], "dist_ma20": (c/ma20-1).iloc[-1], "dist_ma60": (c/ma60-1).iloc[-1],
        "ma5_over_20": (ma5/ma20-1).iloc[-1], "ma20_over_60": (ma20/ma60-1).iloc[-1],
        "drawdown_20": (c/high20-1).iloc[-1], "breakout_20": (c/high20.shift(1)-1).iloc[-1],
        "gap_1d": (d["open"]/prev-1).iloc[-1], "up_days_10": (r>0).astype(float).rolling(10).mean().iloc[-1],
    }
    return {k: float(v) for k,v in out.items() if pd.notna(v) and np.isfinite(v)}


def add_cross_sectional_features(items: list[Any]) -> None:
    """Attach within-morning percentile ranks without using any future data."""
    if not items:
        return

    def getf(obj: Any, key: str) -> float:
        feats = getattr(obj, "features", None) or (obj.get("features", {}) if isinstance(obj, dict) else {})
        try:
            return float(feats.get(key, 0.5))
        except Exception:
            return 0.5

    rank_defs = {
        "xs_liquidity_rank": ["liquidity", "turnover_acceleration", "volume_pressure"],
        "xs_momentum_rank": ["momentum_1d", "momentum_5d", "momentum_10d"],
        "xs_flow_rank": ["foreign_flow", "institution_flow"],
        "xs_trend_rank": ["trend_quality", "near_20d_high", "range_position_20d"],
        "xs_broker_rank": ["broker_report_signal"],
        "xs_news_rank": ["news_catalyst"],
    }
    n = len(items)
    for out_key, source_keys in rank_defs.items():
        vals = np.asarray([sum(getf(x, k) for k in source_keys) / len(source_keys) for x in items], dtype=float)
        order = np.argsort(np.argsort(vals))
        ranks = (order + 1) / max(1, n)
        for obj, rank in zip(items, ranks):
            feats = getattr(obj, "features", None)
            if feats is None and isinstance(obj, dict):
                feats = obj.setdefault("features", {})
            if feats is not None:
                feats[out_key] = float(rank)
