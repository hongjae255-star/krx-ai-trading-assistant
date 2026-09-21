from __future__ import annotations

"""Two-horizon stock selection engine.

This module intentionally separates:
  1) an intraday "+1% net target" *setup* lane, and
  2) a 5-10 trading-day swing lane.

The +1% value is a planning target, never a guaranteed return.  The engine keeps
WATCH candidates visible even when no stock clears the action threshold so the
mobile dashboard does not collapse to an uninformative "no recommendation".

The swing score combines complementary, published families of ideas rather than
blindly adding every indicator: trend/stage analysis, momentum/relative strength,
52-week-high proximity, supply-demand/volume, CAN-SLIM-inspired catalyst and
institutional-sponsorship proxies, quality/risk proxies, and market regime.
"""

from dataclasses import asdict
from datetime import datetime
import math
from typing import Any, Callable

import numpy as np
import pandas as pd

from .features import atr, clamp01
from .models import Candidate
from .historical_blend import HistoricalModelRegistry


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _ret(close: pd.Series, n: int) -> float:
    close = pd.to_numeric(close, errors="coerce").dropna()
    if len(close) <= n or _f(close.iloc[-n - 1]) <= 0:
        return 0.0
    return (_f(close.iloc[-1]) / _f(close.iloc[-n - 1]) - 1.0) * 100.0


def _ma(close: pd.Series, n: int) -> float:
    close = pd.to_numeric(close, errors="coerce").dropna()
    if close.empty:
        return 0.0
    return _f(close.tail(min(n, len(close))).mean())


def _regime_from_features(candidate: Candidate) -> str:
    f = candidate.features or {}
    # Prefer live/market risk-on signal when available, then slower global signal.
    r = _f(f.get("market_live_risk_on"), _f(f.get("macro_global_risk_on"), 0.5))
    if r >= 0.64:
        return "risk_on"
    if r <= 0.36:
        return "risk_off"
    return "mixed"


def _threshold(cfg: dict[str, Any], regime: str, default: float) -> float:
    by = cfg.get("actionable_score_by_regime", {}) if isinstance(cfg, dict) else {}
    return _f(by.get(regime), _f(cfg.get("actionable_score"), default))


def _explain_components(parts: dict[str, float], labels: dict[str, str], n: int = 4) -> list[str]:
    ranked = sorted(parts.items(), key=lambda kv: kv[1], reverse=True)
    return [f"{labels.get(k, k)} {100*v:.0f}" for k, v in ranked[:n]]


def _series_clean(daily: pd.DataFrame) -> pd.DataFrame:
    if daily is None or daily.empty:
        return pd.DataFrame()
    d = daily.copy()
    for c in ["open", "high", "low", "close", "volume", "turnover"]:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    if "date" in d.columns:
        d = d.sort_values("date")
    return d.dropna(subset=["close"]).reset_index(drop=True)


def _volatility_contraction(d: pd.DataFrame) -> float:
    """0..1: rewards recent tightening without requiring proprietary VCP detection."""
    if d.empty or len(d) < 30:
        return 0.5
    r = pd.to_numeric(d["close"], errors="coerce").pct_change().dropna() * 100.0
    if len(r) < 25:
        return 0.5
    v10 = _f(r.tail(10).std(ddof=0), 0.0)
    v30 = _f(r.tail(30).std(ddof=0), 0.0)
    if v30 <= 0:
        return 0.5
    ratio = v10 / v30
    # Best around 0.45-0.80; extreme deadness and expansion are less attractive.
    return clamp01(math.exp(-((ratio - 0.62) / 0.42) ** 2))


def _breakout_readiness(d: pd.DataFrame) -> float:
    if d.empty or len(d) < 20:
        return 0.5
    last = _f(d.iloc[-1].get("close"), 0.0)
    high20 = _f(pd.to_numeric(d["high"], errors="coerce").tail(20).max(), 0.0) if "high" in d else _f(d["close"].tail(20).max())
    if last <= 0 or high20 <= 0:
        return 0.5
    dist = max(0.0, (high20 / last - 1.0) * 100.0)
    return clamp01(1.0 - dist / 8.0)


def _stage2_metrics(d: pd.DataFrame, candidate: Candidate) -> dict[str, Any]:
    d = _series_clean(d)
    close = d["close"] if not d.empty else pd.Series(dtype=float)
    last = _f(close.iloc[-1]) if len(close) else candidate.price
    ma50, ma150, ma200 = _ma(close, 50), _ma(close, 150), _ma(close, 200)
    ma200_20ago = _f(close.iloc[:-20].tail(200).mean()) if len(close) >= 220 else 0.0
    high252 = _f(pd.to_numeric(d.get("high", close), errors="coerce").tail(252).max(), last) if len(close) else last
    low252 = _f(pd.to_numeric(d.get("low", close), errors="coerce").tail(252).min(), last) if len(close) else last
    f = candidate.features or {}
    rs_proxy = 0.55 * _f(f.get("xs_momentum_rank"), 0.5) + 0.45 * _f(f.get("xs_trend_rank"), 0.5)

    checks = {
        "price_above_150_200": bool(last > ma150 > 0 and last > ma200 > 0),
        "ma150_above_200": bool(ma150 > ma200 > 0),
        "ma200_rising": bool(ma200 > ma200_20ago > 0),
        "ma50_above_150_200": bool(ma50 > ma150 > 0 and ma50 > ma200 > 0),
        "price_above_50": bool(last > ma50 > 0),
        "well_above_52w_low": bool(low252 > 0 and last >= 1.25 * low252),
        "near_52w_high": bool(high252 > 0 and last >= 0.75 * high252),
        "relative_strength": bool(rs_proxy >= 0.70),
    }
    passed = sum(1 for v in checks.values() if v)
    return {
        "bars": len(d), "last": last, "ma50": ma50, "ma150": ma150, "ma200": ma200,
        "high252": high252, "low252": low252, "rs_proxy": rs_proxy,
        "checks": checks, "passed": passed, "total": len(checks), "score": passed / max(1, len(checks)),
        "ret20": _ret(close, 20), "ret60": _ret(close, 60), "ret120": _ret(close, 120),
        "contraction": _volatility_contraction(d), "breakout_readiness": _breakout_readiness(d),
    }


class DualStrategyEngine:
    def __init__(self, settings):
        self.settings = settings
        try:
            self.historical = HistoricalModelRegistry(
                settings.path("storage.model_dir"),
                float(settings.get("historical_training.historical_blend_weight", 0.30)),
            )
        except Exception:
            self.historical = None

    def _day_cfg(self) -> dict[str, Any]:
        return dict(self.settings.get("strategy_lanes.day_1pct", {}) or {})

    def _swing_cfg(self) -> dict[str, Any]:
        return dict(self.settings.get("strategy_lanes.swing", {}) or {})

    def day_lane(self, candidates: list[Candidate], daily_map: dict[str, pd.DataFrame], market: str) -> dict[str, Any]:
        cfg = self._day_cfg()
        if not cfg.get("enabled", True):
            return {"enabled": False, "items": []}
        picks = int(cfg.get("picks", 3))
        net_target = _f(cfg.get("net_target_pct"), 1.0)
        cost_bps = _f(cfg.get("estimated_round_trip_cost_bps"), _f(self.settings.get("prediction.estimated_round_trip_cost_bps", 25), 25))
        gross_target = net_target + cost_bps / 100.0
        min_atr = _f(cfg.get("minimum_atr_pct"), max(0.8, gross_target * 0.78))
        max_atr = _f(cfg.get("maximum_atr_pct"), 6.0)
        max_gain = _f(cfg.get("maximum_current_gain_pct"), 8.0)

        rows: list[dict[str, Any]] = []
        labels = {
            "liquidity": "유동성", "turnover": "거래대금 가속", "volume": "거래량 수요",
            "short_mom": "단기 모멘텀", "trend": "추세", "flow": "수급", "near_high": "고가 접근",
            "vol_reach": "목표 도달 여력", "market": "시장환경", "risk": "과열 억제",
        }
        for c in candidates:
            d = _series_clean(daily_map.get(c.code, pd.DataFrame()))
            if d.empty:
                continue
            f = c.features or {}
            a = atr(d, int(self.settings.get("technical.atr_period", 14)))
            atr_pct = 100.0 * a / max(c.price, 1e-9)
            # Reachability is highest when daily range is comfortably larger than the gross target,
            # but we penalise extreme-volatility names because +1% is not worth huge gap/tail risk.
            if atr_pct <= 0:
                reach = 0.25
            elif atr_pct < gross_target:
                reach = clamp01(atr_pct / max(gross_target, 0.1)) * 0.70
            else:
                reach = clamp01(math.exp(-((atr_pct - 2.6) / 2.4) ** 2))
            flow = 0.55 * _f(f.get("foreign_flow"), 0.5) + 0.45 * _f(f.get("institution_flow"), 0.5) if market == "KR" else _f(f.get("xs_flow_rank"), 0.5)
            short_mom = 0.50 * _f(f.get("momentum_1d"), 0.5) + 0.35 * _f(f.get("momentum_5d"), 0.5) + 0.15 * _f(f.get("intraday_strength"), 0.5)
            market_support = 0.55 * _f(f.get("market_live_risk_on"), 0.5) + 0.45 * _f(f.get("macro_global_risk_on"), 0.5)
            parts = {
                "liquidity": _f(f.get("liquidity"), 0.5),
                "turnover": _f(f.get("turnover_acceleration"), 0.5),
                "volume": _f(f.get("volume_pressure"), 0.5),
                "short_mom": short_mom,
                "trend": _f(f.get("trend_quality"), 0.5),
                "flow": flow,
                "near_high": 0.6 * _f(f.get("near_20d_high"), 0.5) + 0.4 * _f(f.get("range_position_20d"), 0.5),
                "vol_reach": reach,
                "market": market_support,
                "risk": 0.6 * _f(f.get("overheating_inverse"), 0.5) + 0.4 * _f(f.get("event_risk_inverse"), 0.5),
            }
            weights = {
                "liquidity": .13, "turnover": .13, "volume": .12, "short_mom": .14, "trend": .10,
                "flow": .08, "near_high": .07, "vol_reach": .12, "market": .06, "risk": .05,
            }
            score = 100.0 * sum(parts[k] * weights[k] for k in weights)
            hist = self.historical.predict(market, "day", f) if self.historical is not None else {"available": False}
            if hist.get("available"):
                hw = self.historical.blend_weight
                score = (1.0 - hw) * score + hw * (100.0 * float(hist.get("probability", 0.5)))
            reasons: list[str] = []
            if atr_pct < min_atr:
                score -= 8.0; reasons.append(f"ATR {atr_pct:.2f}% < 도달여력 기준 {min_atr:.2f}%")
            if atr_pct > max_atr:
                score -= 7.0; reasons.append(f"ATR {atr_pct:.2f}% > 변동성 상한 {max_atr:.1f}%")
            if c.change_pct > max_gain:
                score -= min(15.0, (c.change_pct - max_gain) * 2.0 + 5.0); reasons.append("당일 과열/추격 위험")
            if "overheated" in (c.risk_flags or []):
                score -= 5.0; reasons.append("과열 플래그")
            score = max(0.0, min(100.0, score))
            regime = _regime_from_features(c)
            required = _threshold(cfg, regime, 62.0)
            actionable = score >= required and atr_pct >= min_atr and atr_pct <= max_atr and c.change_pct <= max_gain
            stop_pct = min(_f(cfg.get("max_stop_pct"), 0.90), max(_f(cfg.get("min_stop_pct"), 0.55), 0.34 * max(atr_pct, 1.0)))
            target_price = c.price * (1.0 + gross_target / 100.0)
            stop_price = c.price * (1.0 - stop_pct / 100.0)
            rr = gross_target / max(stop_pct, 0.01)
            if not actionable and not reasons:
                reasons.append(f"준비도 {score:.1f} < {required:.1f}")
            rows.append({
                "market": market, "code": c.code, "name": c.name, "reference_price": round(c.price, 4),
                "score": round(score, 2), "required_score": round(required, 2),
                "status": "ACTIONABLE" if actionable else "WATCH", "regime": regime,
                "net_target_pct": round(net_target, 2), "gross_target_pct": round(gross_target, 2),
                "target_price": round(target_price, 4), "stop_pct": round(stop_pct, 2), "stop_price": round(stop_price, 4),
                "risk_reward": round(rr, 2), "atr_pct": round(atr_pct, 2), "change_pct": round(c.change_pct, 2),
                "reasons": reasons[:4], "strengths": _explain_components(parts, labels, 4),
                "method": "liquidity + demand + short momentum + VWAP/market + volatility reachability + 10y pattern",
                "historical_probability": round(float(hist.get("probability", 0.0)) * 100.0, 1) if hist.get("available") else None,
                "historical_expected_return_pct": round(float(hist.get("expected_return_pct", 0.0)), 2) if hist.get("available") else None,
                "historical_trained_through": hist.get("trained_through") if hist.get("available") else None,
                "warning": "+1%는 비용 차감 후 목표치이며 보장 수익률이 아닙니다.",
            })
        rows.sort(key=lambda r: (_f(r.get("score")), 1 if r.get("status") == "ACTIONABLE" else 0), reverse=True)
        # Always show the best watch candidates when data exists; this fixes the UX failure mode
        # where strict abstention produced a blank dashboard.
        return {
            "enabled": True, "title": "하루 +1% 목표", "horizon": "당일",
            "net_target_pct": net_target, "gross_target_pct": round(gross_target, 2),
            "estimated_cost_bps": cost_bps, "items": rows[:picks], "candidate_count": len(rows),
            "actionable_count": sum(1 for r in rows if r.get("status") == "ACTIONABLE"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def swing_lane(
        self,
        candidates: list[Candidate],
        daily_map: dict[str, pd.DataFrame],
        market: str,
        deep_history_fetcher: Callable[[Candidate, int], pd.DataFrame] | None = None,
        institutional_matcher: Callable[[str], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        cfg = self._swing_cfg()
        if not cfg.get("enabled", True):
            return {"enabled": False, "items": []}
        picks = int(cfg.get("picks", 3))
        deep_key = "deep_history_candidates_us" if market == "US" else "deep_history_candidates_kr"
        deep_n = int(cfg.get(deep_key, cfg.get("deep_history_candidates", 10)))
        history_days = int(cfg.get("history_days", 260))

        # Pre-rank with existing clean real-time model so we spend expensive 200+ bar calls
        # only on credible leaders, not the entire market.
        shortlist = sorted(candidates, key=lambda c: c.final_score, reverse=True)[:deep_n]
        rows: list[dict[str, Any]] = []
        labels = {
            "stage2": "Stage-2 추세", "momentum": "중기 모멘텀", "relative": "상대강도",
            "high52": "52주 고가", "canslim": "CAN-SLIM proxy", "demand": "수급/거래량",
            "quality": "품질/리스크", "market": "시장방향", "institutional": "유명기관 13F",
            "contraction": "변동성 수축",
        }
        for c in shortlist:
            d = daily_map.get(c.code, pd.DataFrame())
            if deep_history_fetcher is not None and (d is None or len(d) < min(220, history_days - 20)):
                try:
                    deep = deep_history_fetcher(c, history_days)
                    if deep is not None and not deep.empty:
                        d = deep
                except Exception:
                    pass
            d = _series_clean(d)
            if d.empty:
                continue
            m = _stage2_metrics(d, c)
            f = c.features or {}
            # Medium-horizon momentum is bounded to avoid automatically preferring parabolic moves.
            mom_raw = 0.45 * m["ret20"] / 12.0 + 0.35 * m["ret60"] / 25.0 + 0.20 * m["ret120"] / 45.0
            momentum = clamp01(0.5 + 0.5 * math.tanh(mom_raw))
            relative = clamp01(m["rs_proxy"])
            high52 = clamp01(1.0 - max(0.0, (m["high252"] / max(m["last"], 1e-9) - 1.0) * 100.0) / 25.0) if m["high252"] else 0.5
            catalyst = 0.50 * _f(f.get("broker_report_signal"), 0.5) + 0.25 * _f(f.get("event_positive_strength"), 0.5) + 0.25 * _f(f.get("news_catalyst"), 0.5)
            demand = 0.35 * _f(f.get("volume_pressure"), 0.5) + 0.25 * _f(f.get("turnover_acceleration"), 0.5) + 0.20 * _f(f.get("foreign_flow"), 0.5) + 0.20 * _f(f.get("institution_flow"), 0.5)
            if market == "US":
                demand = 0.55 * _f(f.get("volume_pressure"), 0.5) + 0.45 * _f(f.get("turnover_acceleration"), 0.5)
            quality = 0.45 * _f(f.get("event_risk_inverse"), 0.5) + 0.35 * _f(f.get("volatility_quality"), 0.5) + 0.20 * _f(f.get("overheating_inverse"), 0.5)
            market_support = 0.60 * _f(f.get("macro_global_risk_on"), 0.5) + 0.40 * _f(f.get("market_live_risk_on"), 0.5)
            inst = institutional_matcher(c.name) if institutional_matcher else {}
            inst_score = clamp01(_f(inst.get("score"), 0.5 if market == "US" else 0.5))
            # CAN-SLIM-inspired proxy: C/A are represented by analyst revision/catalyst because
            # full audited financial histories are not uniformly available in this free pipeline;
            # N=new catalyst, S=demand, L=relative strength, I=institutional, M=market direction.
            can_slim_proxy = 0.30 * catalyst + 0.20 * demand + 0.20 * relative + 0.10 * inst_score + 0.20 * market_support
            parts = {
                "stage2": m["score"], "momentum": momentum, "relative": relative, "high52": high52,
                "canslim": can_slim_proxy, "demand": demand, "quality": quality, "market": market_support,
                "institutional": inst_score, "contraction": m["contraction"],
            }
            weights = {
                "stage2": .24, "momentum": .15, "relative": .11, "high52": .08, "canslim": .12,
                "demand": .08, "quality": .06, "market": .06, "institutional": .04, "contraction": .06,
            }
            score = 100.0 * sum(parts[k] * weights[k] for k in weights)
            hist = self.historical.predict(market, "swing", f) if self.historical is not None else {"available": False}
            if hist.get("available"):
                hw = self.historical.blend_weight
                score = (1.0 - hw) * score + hw * (100.0 * float(hist.get("probability", 0.5)))
            reasons: list[str] = []
            if m["bars"] < 200:
                score -= 7.0; reasons.append(f"장기 일봉 {m['bars']}개뿐이라 Stage-2 신뢰도 제한")
            if m["passed"] < int(cfg.get("minimum_stage2_checks", 5)):
                score -= 6.0; reasons.append(f"Trend Template {m['passed']}/{m['total']}")
            if c.change_pct > _f(cfg.get("maximum_current_gain_pct"), 12.0):
                score -= 6.0; reasons.append("당일 과열로 스윙 진입 불리")
            score = max(0.0, min(100.0, score))
            regime = _regime_from_features(c)
            required = _threshold(cfg, regime, 68.0)
            actionable = score >= required and m["passed"] >= int(cfg.get("minimum_stage2_checks", 5))
            a = atr(d, 14); atr_pct = 100.0 * a / max(c.price, 1e-9)
            stop_pct = min(_f(cfg.get("max_stop_pct"), 7.0), max(_f(cfg.get("min_stop_pct"), 3.0), 1.55 * atr_pct))
            if not actionable and not reasons:
                reasons.append(f"스윙점수 {score:.1f} < {required:.1f}")
            rows.append({
                "market": market, "code": c.code, "name": c.name, "reference_price": round(c.price, 4),
                "score": round(score, 2), "required_score": round(required, 2),
                "status": "ACTIONABLE" if actionable else "WATCH", "regime": regime,
                "horizon": "5–10 거래일", "trend_template": f"{m['passed']}/{m['total']}",
                "stage2_checks": m["checks"], "rs_proxy_pct": round(100 * m["rs_proxy"], 1),
                "return_20d_pct": round(m["ret20"], 2), "return_60d_pct": round(m["ret60"], 2),
                "distance_52w_high_pct": round((m["last"] / max(m["high252"], 1e-9) - 1.0) * 100.0, 2) if m["high252"] else None,
                "atr_pct": round(atr_pct, 2), "planning_stop_pct": round(stop_pct, 2),
                "reasons": reasons[:4], "strengths": _explain_components(parts, labels, 5),
                "institutional": inst,
                "historical_probability": round(float(hist.get("probability", 0.0)) * 100.0, 1) if hist.get("available") else None,
                "historical_expected_return_pct": round(float(hist.get("expected_return_pct", 0.0)), 2) if hist.get("available") else None,
                "historical_trained_through": hist.get("trained_through") if hist.get("available") else None,
                "method": "Stage-2 + momentum/RS + 52-week high + demand + CAN-SLIM-inspired + regime + 13F context + 10y pattern",
                "warning": "1–2주 상승 가능성을 선별하는 셋업 점수이며 수익률 예측/보장이 아닙니다.",
            })
        rows.sort(key=lambda r: (_f(r.get("score")), 1 if r.get("status") == "ACTIONABLE" else 0), reverse=True)
        return {
            "enabled": True, "title": "1–2주 Swing", "horizon": "5–10 거래일",
            "items": rows[:picks], "candidate_count": len(rows),
            "actionable_count": sum(1 for r in rows if r.get("status") == "ACTIONABLE"),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }

    def build(
        self,
        candidates: list[Candidate],
        daily_map: dict[str, pd.DataFrame],
        market: str,
        deep_history_fetcher: Callable[[Candidate, int], pd.DataFrame] | None = None,
        institutional_matcher: Callable[[str], dict[str, Any]] | None = None,
        include_swing: bool = True,
        previous_swing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        day = self.day_lane(candidates, daily_map, market)
        swing = self.swing_lane(candidates, daily_map, market, deep_history_fetcher, institutional_matcher) if include_swing else (previous_swing or {"enabled": True, "items": [], "stale": True})
        return {
            "market": market,
            "day_1pct": day,
            "swing": swing,
            "method_version": "v8.0-dual-strategy",
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "disclaimer": "목표/점수는 확률적 의사결정 보조이며 1% 수익을 보장하지 않습니다.",
        }
