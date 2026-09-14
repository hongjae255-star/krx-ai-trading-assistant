from types import SimpleNamespace

from stockbot.db import Database
from stockbot.learner import AdaptiveWeightLearner


def test_learning_updates_from_all_recommended_picks(tmp_path):
    db = Database(tmp_path / "test.sqlite3")
    cfg = {
        "base_weights": {"liquidity": 0.5, "trend_quality": 0.5},
        "learning": {
            "enabled": True,
            "learning_rate": 0.2,
            "baseline_decay": 0.0,
            "min_samples_before_learning": 2,
            "max_weight_change_per_day": 0.2,
            "min_weight": 0.01,
            "max_weight": 0.95,
        },
    }
    for day, code, reward, features in [
        ("2026-09-08", "000001", 0.5, {"liquidity": 0.9, "trend_quality": 0.4}),
        ("2026-09-09", "000002", 0.8, {"liquidity": 0.9, "trend_quality": 0.2}),
    ]:
        plan = SimpleNamespace(
            code=code, name="T", score=70, reference_price=100,
            entry_low_1=98, entry_high_1=99, entry_low_2=96, entry_high_2=97,
            chase_limit=103, stop_price=94, target1=105, target2=108,
            weight_pct=20, confidence=70, rationale="", features=features, risk_flags=[]
        )
        db.save_recommendations(day, [plan])
        # strategy_entered=False proves learning is independent of user's/plan's trade.
        db.save_pick_evaluation(day, code, {
            "open_price": 100, "close_price": 104, "day_high": 105, "day_low": 99,
            "open_to_close_pct": 4.0, "mfe_pct": 5.0, "mae_pct": -1.0,
            "selection_reward": reward, "strategy_entered": False,
            "strategy_return_pct": 0.0, "strategy_reward": 0.0,
        })
    learner = AdaptiveWeightLearner(db, cfg)
    before = learner.current_weights()
    out = learner.update("2026-09-09")
    after = out["after"]
    assert out["updated"] is True
    assert after["liquidity"] > before["liquidity"]
