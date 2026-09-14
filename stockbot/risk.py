from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .models import TradePlan


def allocate_weights(plans: list[TradePlan], config: dict) -> list[TradePlan]:
    if not plans:
        return []
    risk_cfg = config.get("risk", {})
    total_cap = float(risk_cfg.get("max_total_deployed_pct", 70))
    min_cash = float(risk_cfg.get("minimum_cash_pct", 30))
    total_cap = min(total_cap, 100.0 - min_cash)
    max_single = float(risk_cfg.get("max_single_stock_pct", 30))
    max_high = float(risk_cfg.get("max_high_volatility_stock_pct", 12))

    existing = config.get("existing_positions", []) or []
    if any(str(p.get("risk_class", "")).lower() == "high" for p in existing):
        total_cap = max(0.0, total_cap - float(risk_cfg.get("high_volatility_existing_position_penalty_pct", 10)))

    all_flags = {flag for plan in plans for flag in (plan.risk_flags or [])}
    if "market_risk_off" in all_flags:
        total_cap = max(0.0, total_cap - float(risk_cfg.get("market_risk_off_penalty_pct", 15)))
    if "macro_event_risk" in all_flags:
        total_cap = max(0.0, total_cap - float(risk_cfg.get("macro_event_penalty_pct", 10)))

    scores = [max(1.0, p.score) for p in plans]
    s = sum(scores)
    out: list[TradePlan] = []
    for p, sc in zip(plans, scores):
        w = total_cap * sc / s
        high = any(flag in {"high_volatility", "biotech_binary", "overheated"} for flag in p.risk_flags)
        cap = max_high if high else max_single
        out.append(replace(p, weight_pct=round(min(w, cap), 1)))
    # If caps leave some cash, we intentionally keep it as cash instead of redistributing aggressively.
    return out
