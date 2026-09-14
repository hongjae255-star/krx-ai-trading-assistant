from __future__ import annotations

import math
from datetime import date
from typing import Any

from .db import Database
from .scoring import normalize_weights


class AdaptiveWeightLearner:
    """
    Reward-driven online multiplicative-weights learner.

    This is intentionally simpler and more stable than deep RL for a small,
    non-stationary financial dataset. It only adapts feature weights and is
    bounded by minimum/maximum weight and maximum daily change.
    """

    STATE_KEY = "feature_weights"

    def __init__(self, db: Database, config: dict[str, Any]):
        self.db = db
        self.config = config
        self.base = normalize_weights(config.get("base_weights", {}))
        self.lcfg = config.get("learning", {})

    def current_weights(self) -> dict[str, float]:
        saved = self.db.get_state(self.STATE_KEY)
        if not saved:
            return dict(self.base)
        merged = {k: float(saved.get(k, self.base.get(k, 0.0))) for k in self.base}
        return normalize_weights(merged)

    def update(self, trade_date: str) -> dict[str, Any]:
        before = self.current_weights()
        if not bool(self.lcfg.get("enabled", True)):
            return {"updated": False, "reason": "disabled", "before": before, "after": before, "metrics": {}}

        # Learn stock-selection weights from *every recommended pick*, regardless of whether
        # the user actually bought it or whether the pullback entry zone was touched.
        # Execution-plan quality is tracked separately in outcomes/strategy_return_pct.
        all_rows = self.db.get_pick_evaluations(since_days=3650)
        usable_all = [r for r in all_rows if r.get("selection_reward") is not None]
        today = [r for r in usable_all if r.get("trade_date") == trade_date]
        min_samples = int(self.lcfg.get("min_samples_before_learning", 12))

        metrics = self.performance_metrics(usable_all)
        if len(usable_all) < min_samples or not today:
            self.db.save_learning_history(trade_date, len(today), before, before, {**metrics, "note": "minimum sample gate"})
            return {"updated": False, "reason": "minimum sample gate", "before": before, "after": before, "metrics": metrics}

        lr = float(self.lcfg.get("learning_rate", 0.04))
        decay = float(self.lcfg.get("baseline_decay", 0.03))
        min_w = float(self.lcfg.get("min_weight", 0.02))
        max_w = float(self.lcfg.get("max_weight", 0.30))
        max_delta = float(self.lcfg.get("max_weight_change_per_day", 0.03))

        grads = {k: 0.0 for k in before}
        for row in today:
            reward = float(row.get("selection_reward") or 0.0)
            feats = row.get("features", {})
            for k in grads:
                x = float(feats.get(k, 0.5))
                grads[k] += reward * (x - 0.5)
        n = max(1, len(today))
        grads = {k: v / n for k, v in grads.items()}

        candidate = {k: before[k] * math.exp(lr * grads[k]) for k in before}
        candidate = normalize_weights(candidate)
        candidate = {k: (1.0 - decay) * candidate[k] + decay * self.base[k] for k in candidate}

        bounded = {}
        for k, v in candidate.items():
            v = max(min_w, min(max_w, v))
            lo = max(min_w, before[k] - max_delta)
            hi = min(max_w, before[k] + max_delta)
            bounded[k] = max(lo, min(hi, v))
        after = normalize_weights(bounded)
        self.db.set_state(self.STATE_KEY, after)
        self.db.save_learning_history(trade_date, len(today), before, after, {**metrics, "grads": grads})
        return {"updated": True, "before": before, "after": after, "metrics": metrics, "grads": grads}

    @staticmethod
    def performance_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"samples": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0, "avg_reward": 0.0, "max_drawdown_pct": 0.0}
        returns = [float(r.get("open_to_close_pct") if r.get("open_to_close_pct") is not None else r.get("return_pct") or 0.0) for r in rows]
        rewards = [float(r.get("selection_reward") if r.get("selection_reward") is not None else r.get("reward") or 0.0) for r in rows]
        wins = sum(r > 0 for r in returns)
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        for ret in returns:
            equity *= 1.0 + ret / 100.0
            peak = max(peak, equity)
            dd = (equity / peak - 1.0) * 100.0
            max_dd = min(max_dd, dd)
        return {
            "samples": len(rows),
            "win_rate_pct": round(100.0 * wins / len(rows), 1),
            "avg_return_pct": round(sum(returns) / len(returns), 3),
            "avg_reward": round(sum(rewards) / len(rewards), 3),
            "max_drawdown_pct": round(max_dd, 2),
        }
