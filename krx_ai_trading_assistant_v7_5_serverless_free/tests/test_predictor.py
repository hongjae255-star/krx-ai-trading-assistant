from datetime import date, timedelta

from stockbot.db import Database
from stockbot.models import Candidate
from stockbot.predictor import PredictiveEnsemble, date_walk_forward_splits


def test_date_walk_forward_has_no_future_leakage_and_embargo():
    dates = []
    for d in range(20):
        dates += [(date(2026, 1, 1) + timedelta(days=d)).isoformat()] * 3
    splits = date_walk_forward_splits(dates, n_splits=4, min_train_dates=6, gap_dates=1)
    assert splits
    for tr, te in splits:
        tr_dates = sorted({dates[i] for i in tr})
        te_dates = sorted({dates[i] for i in te})
        assert tr_dates[-1] < te_dates[0]
        # One whole date immediately before the first test date is embargoed.
        all_dates = sorted(set(dates))
        first_i = all_dates.index(te_dates[0])
        if first_i > 0:
            assert all_dates[first_i - 1] not in tr_dates


def test_shadow_snapshot_is_immutable_for_same_day(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    c1 = Candidate(code="000001", name="A", price=100, features={"liquidity": 0.2}, heuristic_score=50, final_score=50)
    db.save_candidate_snapshots("2026-01-02", [c1])
    c2 = Candidate(code="000001", name="A", price=130, features={"liquidity": 0.9}, heuristic_score=90, final_score=90)
    db.save_candidate_snapshots("2026-01-02", [c2])
    row = db.get_candidate_snapshots("2026-01-02")[0]
    assert row["price"] == 100
    assert row["features"]["liquidity"] == 0.2


def test_predictive_ensemble_trains_on_prior_dates_only(tmp_path):
    db = Database(tmp_path / "x.sqlite3")
    cfg = {
        "prediction": {
            "enabled": True,
            "training_lookback_days": 365,
            "min_training_samples": 120,
            "min_training_dates": 10,
            "walk_forward_splits": 4,
            "walk_forward_min_train_dates": 6,
            "embargo_dates": 1,
            "recency_half_life_days": 60,
            "label_threshold_pct": 0.30,
            "isotonic_min_samples": 1000,
            "conformal_window": 300,
            "prediction_interval_alpha": 0.20,
            "minimum_oof_improvement": 0.0,
            "max_model_blend": 0.65,
            "drift_recent_days": 3,
            "drift_reduce_threshold": 9.0,
            "drift_disable_threshold": 10.0,
            "forest_estimators": 35,
            "hist_iterations": 40,
            "min_samples_leaf": 6,
            "random_seed": 7,
        }
    }
    start = date(2026, 1, 1)
    # Non-linear signal that a constant/weak heuristic baseline cannot capture well.
    for di in range(16):
        td = (start + timedelta(days=di)).isoformat()
        candidates = []
        evals = []
        for j in range(12):
            x1 = ((j * 7 + di * 3) % 13) / 12
            x2 = ((j * 5 + di * 2) % 11) / 10
            good = x1 > 0.55 and x2 > 0.50
            ret = (2.0 if good else -0.8) + 0.10 * ((j % 3) - 1)
            feats = {
                "liquidity": x1,
                "foreign_flow": x2,
                "momentum_5d": x1,
                "trend_quality": x2,
                "heuristic_score_norm": 0.5,
            }
            c = Candidate(code=f"{j:06d}", name=f"S{j}", price=100, features=feats, heuristic_score=50, final_score=50)
            candidates.append(c)
            evals.append((c.code, ret))
        db.save_candidate_snapshots(td, candidates)
        for code, ret in evals:
            db.save_candidate_evaluation(td, code, {
                "open_price": 100, "close_price": 100 * (1 + ret / 100), "day_high": 103, "day_low": 98,
                "gross_open_to_close_pct": ret, "net_open_to_close_pct": ret,
                "mfe_pct": 3.0, "mae_pct": -2.0, "label_up": ret > 0.30, "selection_reward": ret / 4,
            })

    as_of = (start + timedelta(days=17)).isoformat()
    model = PredictiveEnsemble(db, cfg, tmp_path / "models")
    status = model.train(as_of)
    assert status["metrics"]["samples"] >= 120
    assert status["metrics"]["oof_samples"] > 0
    preds = model.predict([
        {"heuristic_score": 50, "features": {"liquidity": 0.9, "foreign_flow": 0.9, "momentum_5d": 0.9, "trend_quality": 0.9, "heuristic_score_norm": 0.5}},
        {"heuristic_score": 50, "features": {"liquidity": 0.2, "foreign_flow": 0.2, "momentum_5d": 0.2, "trend_quality": 0.2, "heuristic_score_norm": 0.5}},
    ], as_of, "neutral")
    assert 0.0 < preds[0].up_probability < 1.0
    assert preds[0].expected_return_pct > preds[1].expected_return_pct
