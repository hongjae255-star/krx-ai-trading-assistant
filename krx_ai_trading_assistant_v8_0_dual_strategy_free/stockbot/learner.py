from __future__ import annotations

import math
from typing import Any

from .db import Database
from .scoring import normalize_weights


FEATURE_LABELS = {
    "liquidity": "거래가 충분히 활발한지",
    "turnover_acceleration": "거래대금이 빠르게 늘었는지",
    "momentum_1d": "당일 가격 힘",
    "momentum_5d": "최근 5일 상승 흐름",
    "momentum_10d": "최근 10일 상승 흐름",
    "trend_quality": "추세가 안정적으로 이어지는지",
    "foreign_flow": "외국인 수급",
    "institution_flow": "기관 수급",
    "news_catalyst": "최근 뉴스 재료",
    "broker_report_signal": "증권사 리포트 분위기",
    "event_positive_strength": "긍정 공시·이벤트 힘",
    "event_risk_inverse": "악재 위험이 낮은 정도",
    "overheating_inverse": "과열되지 않은 정도",
    "volatility_quality": "변동성이 지나치지 않은지",
    "atr_pct_quality": "하루 움직임이 매매하기 적당한지",
    "near_20d_high": "최근 고점에 가까운 강한 흐름",
    "range_position_20d": "최근 가격대에서 위쪽에 있는지",
    "close_location_value": "종가가 하루 중 강하게 끝났는지",
    "volume_pressure": "매수 거래량의 힘",
    "macro_global_risk_on": "글로벌 시장 분위기",
    "macro_rate_easing": "금리 부담 완화",
    "macro_credit_safety": "신용시장 안정",
    "macro_volatility_safety": "시장 공포가 낮은 정도",
    "macro_krw_safety": "원화·환율 부담이 낮은 정도",
    "macro_liquidity_support": "시장 유동성 지원",
    "market_live_risk_on": "장중 전체 시장 분위기",
    "market_semis_strength": "반도체 시장 강도",
    "market_bond_bid": "채권시장 안정 신호",
    "market_credit_bid": "위험자산 자금환경",
}


class AdaptiveWeightLearner:
    """Reward-driven online learner for the interpretable score layer.

    It is deliberately not deep reinforcement learning. Each close it uses the
    frozen morning candidate pool and the actual same-day outcome to nudge
    feature weights. This gives far more feedback than learning only from the
    final 1-3 recommendations, while keeping daily changes bounded.
    """

    STATE_KEY = "feature_weights"
    LAST_DATE_KEY = "feature_weights_last_update_date"
    SUMMARY_KEY = "last_learning_summary"

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

    def _lessons(self, grads: dict[str, float], before: dict[str, float], after: dict[str, float]) -> list[str]:
        rows = []
        for k in grads:
            delta = float(after.get(k, 0.0)) - float(before.get(k, 0.0))
            if abs(delta) < 1e-6:
                continue
            label = FEATURE_LABELS.get(k, k)
            rows.append((abs(delta), f"{label}을(를) {'조금 더 중요하게' if delta > 0 else '조금 덜 중요하게'} 보도록 조정"))
        return [x[1] for x in sorted(rows, reverse=True)[:4]]

    def update(self, trade_date: str) -> dict[str, Any]:
        before = self.current_weights()
        if not bool(self.lcfg.get("enabled", True)):
            return {"updated": False, "reason": "disabled", "before": before, "after": before, "metrics": {}}

        # Idempotency: cloud retries or a manual rerun must not train repeatedly on the same day.
        if str(self.db.get_state(self.LAST_DATE_KEY, "")) == str(trade_date):
            prev = self.db.get_state(self.SUMMARY_KEY, {}) or {}
            return {"updated": False, "reason": "already learned today", "before": before, "after": before,
                    "metrics": prev.get("metrics", {}), "lessons": prev.get("lessons", [])}

        picks = [r for r in self.db.get_pick_evaluations(since_days=3650) if r.get("selection_reward") is not None]
        shadow = [r for r in self.db.get_candidate_evaluations_since(since_days=3650) if r.get("selection_reward") is not None]
        today_shadow = [r for r in shadow if str(r.get("trade_date")) == str(trade_date)]
        today_picks = [r for r in picks if str(r.get("trade_date")) == str(trade_date)]
        metrics = self.performance_metrics(picks)
        metrics.update({"shadow_samples": len(shadow), "today_shadow_samples": len(today_shadow), "today_pick_samples": len(today_picks)})

        min_samples = int(self.lcfg.get("min_samples_before_learning", 30))
        min_today = int(self.lcfg.get("min_candidates_per_day", 5))
        # Use the broad frozen candidate pool when available; fall back to picks for legacy DBs.
        today = today_shadow if len(today_shadow) >= min_today else today_picks
        total_n = len(shadow) if shadow else len(picks)
        if total_n < min_samples or len(today) < max(1, min_today if today_shadow else 1):
            self.db.save_learning_history(trade_date, len(today), before, before, {**metrics, "note": "minimum sample gate"})
            summary = {"trade_date": trade_date, "updated": False, "reason": "학습할 사후평가 표본이 아직 부족함", "metrics": metrics, "lessons": []}
            self.db.set_state(self.SUMMARY_KEY, summary)
            return {"updated": False, "reason": "minimum sample gate", "before": before, "after": before, "metrics": metrics, "lessons": []}

        lr = float(self.lcfg.get("learning_rate", 0.035))
        decay = float(self.lcfg.get("baseline_decay", 0.04))
        min_w = float(self.lcfg.get("min_weight", 0.01))
        max_w = float(self.lcfg.get("max_weight", 0.22))
        max_delta = float(self.lcfg.get("max_weight_change_per_day", 0.015))

        grads = {k: 0.0 for k in before}
        if today_shadow:
            # Cross-sectional centering: a feature earns weight when stocks stronger than
            # today's peer average on that feature also produced better outcomes.
            means = {k: sum(float((r.get("features") or {}).get(k, 0.5)) for r in today) / len(today) for k in before}
            avg_reward = sum(float(r.get("selection_reward") or 0.0) for r in today) / len(today)
            denom = {k: 0.0 for k in before}
            for row in today:
                reward = float(row.get("selection_reward") or 0.0) - avg_reward
                feats = row.get("features", {}) or {}
                for k in grads:
                    x = float(feats.get(k, 0.5)) - means[k]
                    grads[k] += reward * x
                    denom[k] += x * x
            grads = {k: grads[k] / max(0.05, math.sqrt(denom[k])) for k in grads}
        else:
            # Backward-compatible fallback for databases that only contain final picks.
            for row in today:
                reward = float(row.get("selection_reward") or 0.0)
                feats = row.get("features", {}) or {}
                for k in grads:
                    grads[k] += reward * (float(feats.get(k, 0.5)) - 0.5)
            grads = {k: v / max(1, len(today)) for k, v in grads.items()}

        candidate = {k: before[k] * math.exp(lr * grads[k]) for k in before}
        candidate = normalize_weights(candidate)
        candidate = {k: (1.0 - decay) * candidate[k] + decay * self.base[k] for k in candidate}
        bounded = {}
        for k, v in candidate.items():
            v = max(min_w, min(max_w, v))
            bounded[k] = max(max(min_w, before[k] - max_delta), min(min(max_w, before[k] + max_delta), v))
        after = normalize_weights(bounded)
        lessons = self._lessons(grads, before, after)
        self.db.set_state(self.STATE_KEY, after)
        self.db.set_state(self.LAST_DATE_KEY, trade_date)
        summary = {"trade_date": trade_date, "updated": True, "metrics": metrics, "lessons": lessons,
                   "source": "morning shadow candidates" if today_shadow else "final picks", "samples_today": len(today)}
        self.db.set_state(self.SUMMARY_KEY, summary)
        self.db.save_learning_history(trade_date, len(today), before, after, {**metrics, "grads": grads, "lessons": lessons})
        return {"updated": True, "before": before, "after": after, "metrics": metrics, "grads": grads,
                "lessons": lessons, "samples_today": len(today), "source": summary["source"]}

    @staticmethod
    def performance_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {"samples": 0, "win_rate_pct": 0.0, "avg_return_pct": 0.0, "avg_reward": 0.0, "max_drawdown_pct": 0.0}
        returns = [float(r.get("open_to_close_pct") if r.get("open_to_close_pct") is not None else r.get("return_pct") or 0.0) for r in rows]
        rewards = [float(r.get("selection_reward") if r.get("selection_reward") is not None else r.get("reward") or 0.0) for r in rows]
        wins = sum(r > 0 for r in returns)
        equity = peak = 1.0
        max_dd = 0.0
        for ret in returns:
            equity *= 1.0 + ret / 100.0
            peak = max(peak, equity)
            max_dd = min(max_dd, (equity / peak - 1.0) * 100.0)
        return {"samples": len(rows), "win_rate_pct": round(100.0 * wins / len(rows), 1),
                "avg_return_pct": round(sum(returns) / len(returns), 3),
                "avg_reward": round(sum(rewards) / len(rewards), 3), "max_drawdown_pct": round(max_dd, 2)}
