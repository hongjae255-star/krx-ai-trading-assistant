from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class Candidate:
    code: str
    name: str
    price: float
    change_pct: float = 0.0
    turnover_krw: float = 0.0
    volume: float = 0.0
    foreign_net_krw: float = 0.0
    institution_net_krw: float = 0.0
    features: dict[str, float] | None = None
    raw_score: float = 0.0
    heuristic_score: float = 0.0
    model_score: float = 0.0
    final_score: float = 0.0
    news_catalyst: float = 0.5
    ai_summary: str = ""
    risk_flags: list[str] | None = None
    prediction: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TradePlan:
    code: str
    name: str
    reference_price: float
    entry_low_1: float
    entry_high_1: float
    entry_low_2: float
    entry_high_2: float
    chase_limit: float
    stop_price: float
    target1: float
    target2: float
    weight_pct: float
    confidence: float
    score: float
    rationale: str
    features: dict[str, float]
    risk_flags: list[str]
    up_probability: float = 0.0
    expected_return_pct: float = 0.0
    expected_mfe_pct: float = 0.0
    expected_mae_pct: float = 0.0
    lower_return_pct: float = 0.0
    upper_return_pct: float = 0.0
    model_active: bool = False
    model_quality: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
