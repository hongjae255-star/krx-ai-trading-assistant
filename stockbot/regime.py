from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, Any
import numpy as np


@dataclass
class MarketRegime:
    name: str
    median_change_pct: float
    dispersion_pct: float
    positive_breadth: float
    high_volatility: bool
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def infer_market_regime(candidates: Iterable[Any]) -> MarketRegime:
    changes: list[float] = []
    for c in candidates:
        try:
            changes.append(float(getattr(c, "change_pct", c.get("change_pct", 0.0))))
        except Exception:
            continue
    if not changes:
        return MarketRegime("unknown", 0.0, 0.0, 0.5, False, 0.0)

    arr = np.asarray(changes, dtype=float)
    med = float(np.nanmedian(arr))
    dispersion = float(np.nanstd(arr))
    breadth = float(np.nanmean(arr > 0.0))
    high_vol = dispersion >= 4.0 or float(np.nanpercentile(np.abs(arr), 75)) >= 6.0

    if high_vol and med < -0.5:
        name = "risk_off_high_vol"
    elif high_vol and med > 0.5:
        name = "risk_on_high_vol"
    elif med >= 0.6 and breadth >= 0.62:
        name = "risk_on"
    elif med <= -0.6 and breadth <= 0.38:
        name = "risk_off"
    else:
        name = "neutral"

    separation = min(1.0, abs(med) / 2.0 + abs(breadth - 0.5) * 1.2 + min(dispersion, 8.0) / 20.0)
    return MarketRegime(
        name=name,
        median_change_pct=round(med, 4),
        dispersion_pct=round(dispersion, 4),
        positive_breadth=round(breadth, 4),
        high_volatility=high_vol,
        confidence=round(separation, 4),
    )


def attach_regime_features(features: dict[str, float], regime: MarketRegime) -> dict[str, float]:
    out = dict(features)
    out["regime_risk_on"] = 1.0 if regime.name.startswith("risk_on") else 0.0
    out["regime_risk_off"] = 1.0 if regime.name.startswith("risk_off") else 0.0
    out["regime_high_vol"] = 1.0 if regime.high_volatility else 0.0
    out["market_breadth"] = max(0.0, min(1.0, regime.positive_breadth))
    out["market_dispersion"] = max(0.0, min(1.0, regime.dispersion_pct / 8.0))
    return out
