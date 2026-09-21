from __future__ import annotations

from typing import Any


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    s = sum(max(0.0, float(v)) for v in weights.values())
    if s <= 0:
        n = len(weights) or 1
        return {k: 1.0 / n for k in weights}
    return {k: max(0.0, float(v)) / s for k, v in weights.items()}


def score(features: dict[str, float], weights: dict[str, float]) -> float:
    w = normalize_weights(weights)
    return 100.0 * sum(w.get(k, 0.0) * max(0.0, min(1.0, float(v))) for k, v in features.items())


def risk_adjusted_score(raw_score: float, change_pct: float, five_day_pct: float, risk_flags: list[str] | None = None) -> float:
    penalty = 0.0
    if change_pct > 10:
        penalty += min(12.0, (change_pct - 10.0) * 1.5)
    if five_day_pct > 25:
        penalty += min(12.0, (five_day_pct - 25.0) * 0.8)
    penalty += 2.0 * len(risk_flags or [])
    return max(0.0, raw_score - penalty)


def five_day_return(daily) -> float:
    if daily is None or daily.empty or "close" not in daily or len(daily) < 6:
        return 0.0
    a = float(daily["close"].iloc[-6])
    b = float(daily["close"].iloc[-1])
    return (b / a - 1.0) * 100.0 if a else 0.0
