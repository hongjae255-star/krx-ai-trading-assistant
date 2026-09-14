from __future__ import annotations

from typing import Any
import pandas as pd

from .features import atr
from .models import Candidate, TradePlan


def tick_size(price: float) -> float:
    # Common KRX quote-unit approximation. This is advisory-only; no orders are generated.
    if price < 2_000:
        return 1
    if price < 5_000:
        return 5
    if price < 20_000:
        return 10
    if price < 50_000:
        return 50
    if price < 200_000:
        return 100
    if price < 500_000:
        return 500
    return 1_000


def rtick(price: float) -> float:
    t = tick_size(max(1.0, price))
    return round(price / t) * t


def make_plan(candidate: Candidate, daily: pd.DataFrame, config: dict[str, Any]) -> TradePlan:
    tcfg = config.get("technical", {})
    ref = float(candidate.price)
    a = atr(daily, int(tcfg.get("atr_period", 14)))
    if a <= 0:
        a = max(ref * 0.025, tick_size(ref) * 10)

    p1 = float(tcfg.get("entry_pullback_atr", 0.30))
    p2 = float(tcfg.get("second_entry_pullback_atr", 0.70))
    stop_a = float(tcfg.get("stop_atr", 1.10))
    r1 = float(tcfg.get("target1_r", 1.20))
    r2 = float(tcfg.get("target2_r", 2.00))

    e1_hi = ref - 0.08 * a
    e1_lo = ref - p1 * a
    e2_hi = ref - max(p1 + 0.10, p2 - 0.15) * a
    e2_lo = ref - p2 * a

    recent_low = 0.0
    if daily is not None and not daily.empty and "low" in daily:
        recent_low = float(daily["low"].tail(5).min())
    model_stop = ref - stop_a * a
    if recent_low > 0 and recent_low < ref:
        # Do not let a very distant swing low create an unbounded stop.
        stop = max(ref - 1.45 * a, min(model_stop, recent_low - 0.05 * a))
    else:
        stop = model_stop

    mid_entry = (e1_lo + e1_hi) / 2.0
    risk = max(mid_entry - stop, 0.4 * a)
    target1 = mid_entry + r1 * risk
    target2 = mid_entry + r2 * risk
    chase = ref + 0.35 * a

    pred = dict(candidate.prediction or {})
    p_up = float(pred.get("up_probability", 0.0) or 0.0)
    quality = float(pred.get("quality", 0.0) or 0.0)
    if pred.get("active"):
        confidence = 100.0 * (0.55 * p_up + 0.45 * max(0.0, min(1.0, candidate.final_score / 100.0)))
        confidence *= 0.85 + 0.15 * quality
    else:
        confidence = candidate.final_score

    return TradePlan(
        code=candidate.code,
        name=candidate.name,
        reference_price=rtick(ref),
        entry_low_1=rtick(min(e1_lo, e1_hi)),
        entry_high_1=rtick(max(e1_lo, e1_hi)),
        entry_low_2=rtick(min(e2_lo, e2_hi)),
        entry_high_2=rtick(max(e2_lo, e2_hi)),
        chase_limit=rtick(chase),
        stop_price=rtick(stop),
        target1=rtick(target1),
        target2=rtick(target2),
        weight_pct=0.0,
        confidence=round(min(95.0, max(35.0, confidence)), 1),
        score=round(candidate.final_score, 2),
        rationale=candidate.ai_summary,
        features=dict(candidate.features or {}),
        risk_flags=list(candidate.risk_flags or []),
        up_probability=round(100.0 * p_up, 1) if p_up else 0.0,
        expected_return_pct=round(float(pred.get("expected_return_pct", 0.0) or 0.0), 2),
        expected_mfe_pct=round(float(pred.get("expected_mfe_pct", 0.0) or 0.0), 2),
        expected_mae_pct=round(float(pred.get("expected_mae_pct", 0.0) or 0.0), 2),
        lower_return_pct=round(float(pred.get("lower_return_pct", 0.0) or 0.0), 2),
        upper_return_pct=round(float(pred.get("upper_return_pct", 0.0) or 0.0), 2),
        model_active=bool(pred.get("active", False)),
        model_quality=round(100.0 * quality, 1),
    )
