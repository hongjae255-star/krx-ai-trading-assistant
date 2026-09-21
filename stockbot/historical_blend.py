from __future__ import annotations

import pickle
from pathlib import Path
from typing import Any

import numpy as np


class HistoricalModelRegistry:
    """Loads V9 pre-trained long-history models without breaking live operation.

    The current live engine has richer intraday/KIS features than the historical
    OHLCV model. Blending is therefore opt-in and only applied when a caller can
    provide all historical feature names. Missing/unavailable bundles return a
    neutral result rather than blocking recommendations.
    """

    def __init__(self, model_dir: Path, blend_weight: float = 0.30):
        self.model_dir = Path(model_dir)
        self.blend_weight = max(0.0, min(0.70, float(blend_weight)))
        self._cache: dict[tuple[str, str], dict[str, Any] | None] = {}

    def load(self, market: str, lane: str) -> dict[str, Any] | None:
        key = (market.upper(), lane)
        if key not in self._cache:
            p = self.model_dir / f"historical_{market.lower()}_{lane}.pkl"
            if not p.exists():
                self._cache[key] = None
            else:
                try:
                    with p.open("rb") as f:
                        self._cache[key] = pickle.load(f)
                except Exception:
                    self._cache[key] = None
        return self._cache[key]

    def predict(self, market: str, lane: str, features: dict[str, float]) -> dict[str, Any]:
        b = self.load(market, lane)
        if not b:
            return {"available": False}
        names = list(b.get("features", []))
        if not names or any(n not in features for n in names):
            return {"available": False, "reason": "historical features unavailable"}
        X = np.asarray([[float(features[n]) for n in names]], dtype=float)
        X = np.nan_to_num(X, nan=0.0, posinf=20.0, neginf=-20.0)
        X = np.clip(X, -20.0, 20.0)
        p = float(b["classifier"].predict_proba(X)[0, 1])
        r = float(b["return_regressor"].predict(X)[0])
        return {"available": True, "probability": p, "expected_return_pct": r,
                "trained_through": b.get("trained_through"), "metrics": b.get("metrics", {})}

    def blend_probability(self, live_probability: float, historical_probability: float) -> float:
        w = self.blend_weight
        return (1.0 - w) * float(live_probability) + w * float(historical_probability)
