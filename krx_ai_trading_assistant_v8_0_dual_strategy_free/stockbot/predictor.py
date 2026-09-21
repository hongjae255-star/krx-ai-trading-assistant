from __future__ import annotations

import math
import pickle
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

try:
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import LogisticRegression, Ridge
    from sklearn.metrics import brier_score_loss, log_loss, mean_absolute_error, mean_squared_error, roc_auc_score
    from sklearn.isotonic import IsotonicRegression
except ImportError:  # pragma: no cover - graceful fallback on minimal installs
    HistGradientBoostingClassifier = HistGradientBoostingRegressor = None  # type: ignore
    RandomForestClassifier = RandomForestRegressor = None  # type: ignore
    LogisticRegression = Ridge = None  # type: ignore
    IsotonicRegression = None  # type: ignore

from .db import Database


DEFAULT_FEATURES = [
    "liquidity", "turnover_acceleration", "momentum_1d", "momentum_5d", "momentum_10d",
    "trend_quality", "foreign_flow", "institution_flow", "news_catalyst", "broker_report_signal",
    "overheating_inverse", "volatility_quality", "atr_pct_quality", "near_20d_high", "range_position_20d",
    "close_location_value", "volume_pressure", "xs_liquidity_rank", "xs_momentum_rank", "xs_flow_rank",
    "xs_trend_rank", "xs_broker_rank", "xs_news_rank", "regime_risk_on", "regime_risk_off",
    "regime_high_vol", "market_breadth", "market_dispersion", "global_risk_on", "macro_event_safety",
    "macro_global_risk_on", "macro_rate_easing", "macro_curve_health", "macro_real_rate_safety",
    "macro_inflation_stability", "macro_volatility_safety", "macro_credit_safety",
    "macro_financial_conditions_safety", "macro_usd_safety", "macro_krw_safety",
    "macro_liquidity_support", "macro_oil_stability", "macro_gold_risk_signal", "macro_domestic_fx_support",
    "market_live_risk_on", "market_spy_strength", "market_nasdaq_strength", "market_semis_strength",
    "market_smallcap_strength", "market_bond_bid", "market_credit_bid", "market_dollar_weakness",
    "market_gold_bid", "market_oil_impulse",
    "heuristic_score_norm",
]


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, float(x)))))


def _rank_corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3 or np.nanstd(a) < 1e-12 or np.nanstd(b) < 1e-12:
        return 0.0
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    val = np.corrcoef(ra, rb)[0, 1]
    return float(val) if np.isfinite(val) else 0.0




def _calibration_stats(y: np.ndarray, p: np.ndarray, bins: int = 5) -> tuple[float, list[dict[str, float]]]:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    if len(y) == 0:
        return 0.0, []
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    rows: list[dict[str, float]] = []
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p < hi if i < bins - 1 else p <= hi)
        if not mask.any():
            continue
        pred = float(np.mean(p[mask]))
        actual = float(np.mean(y[mask]))
        weight = float(np.mean(mask))
        ece += weight * abs(pred - actual)
        rows.append({"predicted": round(pred, 4), "actual": round(actual, 4), "weight": round(weight, 4)})
    return float(ece), rows

def date_walk_forward_splits(
    dates: list[str],
    n_splits: int = 5,
    min_train_dates: int = 8,
    gap_dates: int = 1,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding walk-forward splits grouped by whole trading dates.

    Every test date is strictly later than every train date, with `gap_dates`
    whole dates embargoed between them. Stocks from the same date never leak
    across train and test.
    """
    arr = np.asarray(dates, dtype=object)
    uniq = sorted(set(str(x) for x in arr))
    if len(uniq) < min_train_dates + gap_dates + 2:
        return []
    first_test = max(min_train_dates + gap_dates, int(len(uniq) * 0.45))
    test_dates = uniq[first_test:]
    blocks = [list(x) for x in np.array_split(np.asarray(test_dates, dtype=object), min(n_splits, len(test_dates))) if len(x)]
    out: list[tuple[np.ndarray, np.ndarray]] = []
    for block in blocks:
        first = uniq.index(str(block[0]))
        train_end = max(0, first - gap_dates)
        allowed_train = set(uniq[:train_end])
        allowed_test = set(str(x) for x in block)
        tr = np.where(np.asarray([str(x) in allowed_train for x in arr]))[0]
        te = np.where(np.asarray([str(x) in allowed_test for x in arr]))[0]
        if len(tr) and len(te):
            out.append((tr, te))
    return out


@dataclass
class Prediction:
    up_probability: float
    expected_return_pct: float
    expected_mfe_pct: float
    expected_mae_pct: float
    lower_return_pct: float
    upper_return_pct: float
    model_score: float
    blend_weight: float
    active: bool
    quality: float
    regime: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PredictiveEnsemble:
    """Leakage-resistant probabilistic return forecaster.

    - trains only on dates strictly before `as_of_date`
    - whole-date expanding walk-forward validation with an embargo gap
    - logistic + tree ensemble for P(net return > threshold)
    - ridge + tree ensemble for expected net open->close return
    - OOF probability calibration (sigmoid for small samples, isotonic for large samples)
    - recent OOF residual quantiles for uncertainty intervals
    - champion/challenger gate: ML gets meaningful weight only when OOF metrics beat
      simple one-score baselines
    - recency weighting and drift reduction for non-stationary markets
    """

    STATE_KEY = "predictive_model_status"

    def __init__(self, db: Database, config: dict[str, Any], model_dir: str | Path):
        self.db = db
        self.config = config
        self.pcfg = config.get("prediction", {})
        self.model_dir = Path(model_dir)
        self.model_dir.mkdir(parents=True, exist_ok=True)
        self.artifact_path = self.model_dir / "ensemble.pkl"
        self._artifact: dict[str, Any] | None = None

    @property
    def available(self) -> bool:
        return LogisticRegression is not None

    def _features(self) -> list[str]:
        configured = self.pcfg.get("features")
        out = [str(x) for x in configured] if configured else list(DEFAULT_FEATURES)
        if "heuristic_score_norm" not in out:
            out.append("heuristic_score_norm")
        return out

    def _matrix(self, rows: list[dict[str, Any]], feature_names: list[str]) -> np.ndarray:
        X = np.full((len(rows), len(feature_names)), 0.5, dtype=float)
        for i, r in enumerate(rows):
            feats = dict(r.get("features") or {})
            feats["heuristic_score_norm"] = _clip01(float(r.get("heuristic_score", r.get("score", 50.0))) / 100.0)
            for j, k in enumerate(feature_names):
                try:
                    v = float(feats.get(k, 0.5))
                    if np.isfinite(v):
                        X[i, j] = max(-2.0, min(2.0, v))
                except Exception:
                    pass
        return X

    def _sample_weights(self, dates: list[str], as_of_date: str) -> np.ndarray:
        half_life = max(5.0, float(self.pcfg.get("recency_half_life_days", 45.0)))
        anchor = date.fromisoformat(as_of_date)
        vals = []
        for d in dates:
            try:
                age = max(0, (anchor - date.fromisoformat(str(d))).days)
            except Exception:
                age = 0
            vals.append(math.exp(-math.log(2.0) * age / half_life))
        return np.asarray(vals, dtype=float)

    def _new_models(self) -> tuple[dict[str, Any], dict[str, Any]]:
        seed = int(self.pcfg.get("random_seed", 42))
        ntrees = int(self.pcfg.get("forest_estimators", 180))
        hiter = int(self.pcfg.get("hist_iterations", 100))
        min_leaf = int(self.pcfg.get("min_samples_leaf", 12))
        cls = {
            "logit": LogisticRegression(C=0.7, max_iter=1000, class_weight="balanced", random_state=seed),
            "hist": HistGradientBoostingClassifier(max_iter=hiter, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=min_leaf, l2_regularization=1.5, random_state=seed),
            "rf": RandomForestClassifier(n_estimators=ntrees, max_depth=6, min_samples_leaf=max(4, min_leaf // 2), max_features="sqrt", class_weight="balanced_subsample", n_jobs=-1, random_state=seed),
        }
        reg = {
            "ridge": Ridge(alpha=3.0),
            "hist": HistGradientBoostingRegressor(loss="absolute_error", max_iter=hiter, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=min_leaf, l2_regularization=1.5, random_state=seed),
            "rf": RandomForestRegressor(n_estimators=ntrees, max_depth=6, min_samples_leaf=max(4, min_leaf // 2), max_features="sqrt", n_jobs=-1, random_state=seed),
        }
        return cls, reg

    @staticmethod
    def _fit_model(model: Any, X: np.ndarray, y: np.ndarray, w: np.ndarray) -> Any:
        try:
            model.fit(X, y, sample_weight=w)
        except TypeError:
            model.fit(X, y)
        return model

    @staticmethod
    def _classification_weights(preds: dict[str, np.ndarray], y: np.ndarray) -> dict[str, float]:
        losses: dict[str, float] = {}
        for name, p in preds.items():
            p = np.clip(p, 1e-4, 1 - 1e-4)
            losses[name] = float(brier_score_loss(y, p))
        inv = {k: 1.0 / max(1e-5, v) for k, v in losses.items()}
        s = sum(inv.values()) or 1.0
        return {k: v / s for k, v in inv.items()}

    @staticmethod
    def _regression_weights(preds: dict[str, np.ndarray], y: np.ndarray) -> dict[str, float]:
        losses = {name: float(mean_absolute_error(y, p)) for name, p in preds.items()}
        inv = {k: 1.0 / max(1e-5, v) for k, v in losses.items()}
        s = sum(inv.values()) or 1.0
        return {k: v / s for k, v in inv.items()}

    def _drift_score(self, X: np.ndarray, dates: list[str]) -> float:
        uniq = sorted(set(dates))
        recent_days = int(self.pcfg.get("drift_recent_days", 4))
        if len(uniq) <= recent_days + 3:
            return 0.0
        recent_set = set(uniq[-recent_days:])
        rmask = np.asarray([d in recent_set for d in dates])
        hmask = ~rmask
        if rmask.sum() < 5 or hmask.sum() < 20:
            return 0.0
        hist_mean = np.nanmean(X[hmask], axis=0)
        hist_std = np.nanstd(X[hmask], axis=0) + 0.05
        recent_mean = np.nanmean(X[rmask], axis=0)
        shift = np.abs(recent_mean - hist_mean) / hist_std
        return float(np.nanmedian(np.clip(shift, 0, 6)))

    def train(self, as_of_date: str) -> dict[str, Any]:
        if not self.available or not bool(self.pcfg.get("enabled", True)):
            status = {"active": False, "reason": "prediction disabled or scikit-learn unavailable", "trained_as_of": as_of_date}
            self.db.set_state(self.STATE_KEY, status)
            return status

        lookback = int(self.pcfg.get("training_lookback_days", 365))
        rows = self.db.get_candidate_training_rows(as_of_date=as_of_date, since_days=lookback)
        min_samples = int(self.pcfg.get("min_training_samples", 180))
        min_dates = int(self.pcfg.get("min_training_dates", 12))
        distinct_dates = sorted(set(str(r["trade_date"]) for r in rows))
        if len(rows) < min_samples or len(distinct_dates) < min_dates:
            status = {
                "active": False, "reason": "minimum training gate", "samples": len(rows),
                "distinct_dates": len(distinct_dates), "required_samples": min_samples, "required_dates": min_dates,
                "trained_as_of": as_of_date,
            }
            self.db.set_state(self.STATE_KEY, status)
            return status

        feature_names = self._features()
        X = self._matrix(rows, feature_names)
        returns = np.asarray([float(r.get("net_open_to_close_pct") or 0.0) for r in rows], dtype=float)
        returns = np.clip(returns, -15.0, 15.0)
        # Day trading is path-dependent: a stock can offer a strong intraday opportunity
        # even when it closes near the open.  Learn upside/downside excursion separately.
        mfe = np.asarray([float(r.get("mfe_pct") or 0.0) for r in rows], dtype=float)
        mae_target = np.asarray([float(r.get("mae_pct") or 0.0) for r in rows], dtype=float)
        mfe = np.clip(mfe, 0.0, 20.0)
        mae_target = np.clip(mae_target, -20.0, 0.0)
        threshold = float(self.pcfg.get("label_threshold_pct", 0.30))
        y = (returns > threshold).astype(int)
        dates = [str(r["trade_date"]) for r in rows]
        if len(np.unique(y)) < 2:
            status = {"active": False, "reason": "single class training data", "samples": len(rows), "trained_as_of": as_of_date}
            self.db.set_state(self.STATE_KEY, status)
            return status

        splits = date_walk_forward_splits(
            dates,
            n_splits=int(self.pcfg.get("walk_forward_splits", 5)),
            min_train_dates=int(self.pcfg.get("walk_forward_min_train_dates", 8)),
            gap_dates=int(self.pcfg.get("embargo_dates", 1)),
        )
        if len(splits) < 2:
            status = {"active": False, "reason": "insufficient walk-forward folds", "samples": len(rows), "trained_as_of": as_of_date}
            self.db.set_state(self.STATE_KEY, status)
            return status

        cls_oof: dict[str, np.ndarray] = {k: np.full(len(rows), np.nan) for k in ["logit", "hist", "rf"]}
        reg_oof: dict[str, np.ndarray] = {k: np.full(len(rows), np.nan) for k in ["ridge", "hist", "rf"]}
        mfe_oof: dict[str, np.ndarray] = {k: np.full(len(rows), np.nan) for k in ["ridge", "hist", "rf"]}
        mae_oof: dict[str, np.ndarray] = {k: np.full(len(rows), np.nan) for k in ["ridge", "hist", "rf"]}
        baseline_p = np.full(len(rows), np.nan)
        baseline_r = np.full(len(rows), np.nan)

        for tr, te in splits:
            if len(np.unique(y[tr])) < 2:
                continue
            cls_models, reg_models = self._new_models()
            _, mfe_models = self._new_models()
            _, mae_models = self._new_models()
            w = self._sample_weights([dates[i] for i in tr], dates[te[0]])
            for name, model in cls_models.items():
                self._fit_model(model, X[tr], y[tr], w)
                cls_oof[name][te] = model.predict_proba(X[te])[:, 1]
            for name, model in reg_models.items():
                self._fit_model(model, X[tr], returns[tr], w)
                reg_oof[name][te] = model.predict(X[te])
            for name, model in mfe_models.items():
                self._fit_model(model, X[tr], mfe[tr], w)
                mfe_oof[name][te] = model.predict(X[te])
            for name, model in mae_models.items():
                self._fit_model(model, X[tr], mae_target[tr], w)
                mae_oof[name][te] = model.predict(X[te])

            # Leakage-free baselines using only the pre-existing heuristic score.
            htr = X[tr, feature_names.index("heuristic_score_norm")].reshape(-1, 1)
            hte = X[te, feature_names.index("heuristic_score_norm")].reshape(-1, 1)
            bcls = LogisticRegression(C=0.5, max_iter=500, class_weight="balanced", random_state=17)
            self._fit_model(bcls, htr, y[tr], w)
            baseline_p[te] = bcls.predict_proba(hte)[:, 1]
            breg = Ridge(alpha=3.0)
            self._fit_model(breg, htr, returns[tr], w)
            baseline_r[te] = breg.predict(hte)

        valid = np.ones(len(rows), dtype=bool)
        for p in cls_oof.values():
            valid &= np.isfinite(p)
        for p in reg_oof.values():
            valid &= np.isfinite(p)
        for p in mfe_oof.values():
            valid &= np.isfinite(p)
        for p in mae_oof.values():
            valid &= np.isfinite(p)
        valid &= np.isfinite(baseline_p) & np.isfinite(baseline_r)
        if valid.sum() < max(60, min_samples // 3):
            status = {"active": False, "reason": "too few OOF predictions", "samples": len(rows), "oof_samples": int(valid.sum()), "trained_as_of": as_of_date}
            self.db.set_state(self.STATE_KEY, status)
            return status

        yv_all, rv_all = y[valid], returns[valid]
        mfe_all, mae_all = mfe[valid], mae_target[valid]
        cls_preds_all = {k: v[valid] for k, v in cls_oof.items()}
        reg_preds_all = {k: v[valid] for k, v in reg_oof.items()}
        mfe_preds_all = {k: v[valid] for k, v in mfe_oof.items()}
        mae_preds_all = {k: v[valid] for k, v in mae_oof.items()}
        valid_dates = np.asarray(dates, dtype=object)[valid]

        # Nested chronological calibration/evaluation split inside the OOF predictions.
        # The probability calibrator and ensemble weights are fitted on earlier OOF rows;
        # champion/challenger metrics are measured only on later OOF dates. This avoids
        # grading the calibrator on the same labels used to fit it.
        uniq_oof_dates = sorted(set(str(x) for x in valid_dates))
        eval_frac = max(0.20, min(0.45, float(self.pcfg.get("oof_evaluation_fraction", 0.35))))
        n_eval_dates = max(2, int(math.ceil(len(uniq_oof_dates) * eval_frac)))
        n_eval_dates = min(n_eval_dates, max(2, len(uniq_oof_dates) - 2))
        eval_date_set = set(uniq_oof_dates[-n_eval_dates:])
        eval_mask = np.asarray([str(d) in eval_date_set for d in valid_dates])
        cal_mask = ~eval_mask
        if cal_mask.sum() < 30 or eval_mask.sum() < 20 or len(np.unique(yv_all[cal_mask])) < 2:
            # Conservative fallback if the history is barely above the minimum gate.
            split_at = max(20, int(len(yv_all) * 0.65))
            split_at = min(split_at, len(yv_all) - 15)
            cal_mask = np.arange(len(yv_all)) < split_at
            eval_mask = ~cal_mask

        y_cal, r_cal = yv_all[cal_mask], rv_all[cal_mask]
        y_eval, r_eval = yv_all[eval_mask], rv_all[eval_mask]
        mfe_cal, mfe_eval = mfe_all[cal_mask], mfe_all[eval_mask]
        mae_cal, mae_eval = mae_all[cal_mask], mae_all[eval_mask]
        cls_cal = {k: v[cal_mask] for k, v in cls_preds_all.items()}
        cls_eval = {k: v[eval_mask] for k, v in cls_preds_all.items()}
        reg_cal = {k: v[cal_mask] for k, v in reg_preds_all.items()}
        reg_eval = {k: v[eval_mask] for k, v in reg_preds_all.items()}
        mfe_cal_preds = {k: v[cal_mask] for k, v in mfe_preds_all.items()}
        mfe_eval_preds = {k: v[eval_mask] for k, v in mfe_preds_all.items()}
        mae_cal_preds = {k: v[cal_mask] for k, v in mae_preds_all.items()}
        mae_eval_preds = {k: v[eval_mask] for k, v in mae_preds_all.items()}

        cw_eval = self._classification_weights(cls_cal, y_cal)
        rw_eval = self._regression_weights(reg_cal, r_cal)
        mfe_w_eval = self._regression_weights(mfe_cal_preds, mfe_cal)
        mae_w_eval = self._regression_weights(mae_cal_preds, mae_cal)
        p_raw_cal = sum(cw_eval[k] * cls_cal[k] for k in cw_eval)
        p_raw_eval = sum(cw_eval[k] * cls_eval[k] for k in cw_eval)
        r_raw_eval = sum(rw_eval[k] * reg_eval[k] for k in rw_eval)
        mfe_raw_eval = sum(mfe_w_eval[k] * mfe_eval_preds[k] for k in mfe_w_eval)
        mae_raw_eval = sum(mae_w_eval[k] * mae_eval_preds[k] for k in mae_w_eval)

        eval_cal_method = "sigmoid"
        if len(y_cal) >= int(self.pcfg.get("isotonic_min_samples", 1000)) and IsotonicRegression is not None:
            eval_calibrator: Any = IsotonicRegression(out_of_bounds="clip")
            eval_calibrator.fit(p_raw_cal, y_cal)
            p_eval = eval_calibrator.predict(p_raw_eval)
            eval_cal_method = "isotonic"
        else:
            eval_calibrator = LogisticRegression(C=1.0, max_iter=500, random_state=23)
            eval_calibrator.fit(np.asarray(p_raw_cal).reshape(-1, 1), y_cal)
            p_eval = eval_calibrator.predict_proba(np.asarray(p_raw_eval).reshape(-1, 1))[:, 1]
        p_eval = np.clip(p_eval, 1e-4, 1 - 1e-4)

        base_p_eval = np.clip(baseline_p[valid][eval_mask], 1e-4, 1 - 1e-4)
        base_r_eval = baseline_r[valid][eval_mask]
        brier = float(brier_score_loss(y_eval, p_eval))
        base_brier = float(brier_score_loss(y_eval, base_p_eval))
        try:
            auc = float(roc_auc_score(y_eval, p_eval))
            base_auc = float(roc_auc_score(y_eval, base_p_eval))
        except Exception:
            auc = base_auc = 0.5
        ll = float(log_loss(y_eval, p_eval, labels=[0, 1]))
        ece, calibration_bins = _calibration_stats(y_eval, p_eval, int(self.pcfg.get("calibration_bins", 5)))
        return_mae = float(mean_absolute_error(r_eval, r_raw_eval))
        base_mae = float(mean_absolute_error(r_eval, base_r_eval))
        rmse = float(math.sqrt(mean_squared_error(r_eval, r_raw_eval)))
        mfe_mae = float(mean_absolute_error(mfe_eval, mfe_raw_eval))
        mae_mae = float(mean_absolute_error(mae_eval, mae_raw_eval))

        eval_dates = valid_dates[eval_mask]
        ics, base_ics = [], []
        for d in sorted(set(eval_dates)):
            m = eval_dates == d
            if m.sum() >= 3:
                ics.append(_rank_corr(r_raw_eval[m], r_eval[m]))
                base_ics.append(_rank_corr(base_r_eval[m], r_eval[m]))
        rank_ic = float(np.mean(ics)) if ics else 0.0
        base_rank_ic = float(np.mean(base_ics)) if base_ics else 0.0

        # Prediction interval is based on genuinely later OOF evaluation residuals.
        residuals = r_eval - r_raw_eval
        conformal_window = int(self.pcfg.get("conformal_window", 500))
        residuals = residuals[-conformal_window:]
        alpha = max(0.05, min(0.40, float(self.pcfg.get("prediction_interval_alpha", 0.20))))
        lower_resid = float(np.quantile(residuals, alpha / 2))
        upper_resid = float(np.quantile(residuals, 1 - alpha / 2))

        brier_impr = (base_brier - brier) / max(base_brier, 1e-6)
        mae_impr = (base_mae - return_mae) / max(base_mae, 1e-6)
        ic_impr = rank_ic - base_rank_ic
        min_impr = float(self.pcfg.get("minimum_oof_improvement", 0.01))
        statistical_edge = brier_impr >= min_impr or mae_impr >= min_impr or ic_impr >= 0.02
        quality = _clip01(0.40 + 2.0 * max(0.0, brier_impr) + 1.1 * max(0.0, mae_impr) + 0.8 * max(0.0, rank_ic))

        # Deployment weights/calibrator may use all OOF predictions *after* the unbiased
        # challenger decision has already been made on the later evaluation block.
        cw = self._classification_weights(cls_preds_all, yv_all)
        rw = self._regression_weights(reg_preds_all, rv_all)
        mfe_w = self._regression_weights(mfe_preds_all, mfe_all)
        mae_w = self._regression_weights(mae_preds_all, mae_all)
        p_raw_all = sum(cw[k] * cls_preds_all[k] for k in cw)
        cal_method = "sigmoid"
        if len(yv_all) >= int(self.pcfg.get("isotonic_min_samples", 1000)) and IsotonicRegression is not None:
            calibrator: Any = IsotonicRegression(out_of_bounds="clip")
            calibrator.fit(p_raw_all, yv_all)
            cal_method = "isotonic"
        else:
            calibrator = LogisticRegression(C=1.0, max_iter=500, random_state=23)
            calibrator.fit(np.asarray(p_raw_all).reshape(-1, 1), yv_all)

        drift = self._drift_score(X, dates)
        drift_reduce = float(self.pcfg.get("drift_reduce_threshold", 1.35))
        drift_disable = float(self.pcfg.get("drift_disable_threshold", 2.50))
        max_blend = float(self.pcfg.get("max_model_blend", 0.65))
        blend = max_blend * quality if statistical_edge else 0.0
        if drift >= drift_disable:
            blend = 0.0
            statistical_edge = False
        elif drift >= drift_reduce:
            blend *= 0.5

        # Train final models strictly on all historical rows prior to as_of_date.
        cls_models, reg_models = self._new_models()
        _, mfe_models = self._new_models()
        _, mae_models = self._new_models()
        w_all = self._sample_weights(dates, as_of_date)
        for model in cls_models.values():
            self._fit_model(model, X, y, w_all)
        for model in reg_models.values():
            self._fit_model(model, X, returns, w_all)
        for model in mfe_models.values():
            self._fit_model(model, X, mfe, w_all)
        for model in mae_models.values():
            self._fit_model(model, X, mae_target, w_all)

        metrics = {
            "samples": len(rows), "distinct_dates": len(distinct_dates), "oof_samples": int(valid.sum()),
            "calibration_samples": int(cal_mask.sum()), "validation_samples": int(eval_mask.sum()),
            "validation_dates": len(set(str(x) for x in eval_dates)),
            "brier": round(brier, 6), "baseline_brier": round(base_brier, 6), "brier_improvement": round(brier_impr, 6),
            "auc": round(auc, 6), "baseline_auc": round(base_auc, 6), "log_loss": round(ll, 6),
            "expected_calibration_error": round(ece, 6), "calibration_bins": calibration_bins,
            "mae_return_pct": round(return_mae, 6), "baseline_mae_return_pct": round(base_mae, 6), "mae_improvement": round(mae_impr, 6),
            "rmse_return_pct": round(rmse, 6), "mfe_mae_pct": round(mfe_mae, 6), "mae_mae_pct": round(mae_mae, 6),
            "rank_ic": round(rank_ic, 6), "baseline_rank_ic": round(base_rank_ic, 6),
            "drift_score": round(drift, 6), "calibration": cal_method, "validation_calibration": eval_cal_method,
            "classification_weights": cw, "regression_weights": rw,
            "mfe_regression_weights": mfe_w, "mae_regression_weights": mae_w,
            "conformal_residual_low": round(lower_resid, 6), "conformal_residual_high": round(upper_resid, 6),
        }
        artifact = {
            "trained_as_of": as_of_date, "feature_names": feature_names, "classifiers": cls_models, "regressors": reg_models,
            "mfe_regressors": mfe_models, "mae_regressors": mae_models,
            "class_weights": cw, "reg_weights": rw, "mfe_reg_weights": mfe_w, "mae_reg_weights": mae_w,
            "calibrator": calibrator, "calibration_method": cal_method,
            "lower_resid": lower_resid, "upper_resid": upper_resid, "metrics": metrics,
            "active": bool(statistical_edge and blend > 0), "blend_weight": float(blend), "quality": float(quality),
        }
        with open(self.artifact_path, "wb") as f:
            pickle.dump(artifact, f, protocol=pickle.HIGHEST_PROTOCOL)
        self._artifact = artifact
        status = {
            "active": artifact["active"], "blend_weight": round(blend, 4), "quality": round(quality, 4),
            "reason": "OOF edge accepted" if artifact["active"] else "challenger did not beat baseline / drift gate",
            "metrics": metrics, "trained_as_of": as_of_date,
        }
        self.db.set_state(self.STATE_KEY, status)
        self.db.save_model_history(as_of_date, "ensemble_v3", len(rows), len(distinct_dates), artifact["active"], status)
        return status

    def _load(self) -> dict[str, Any] | None:
        if self._artifact is not None:
            return self._artifact
        try:
            with open(self.artifact_path, "rb") as f:
                self._artifact = pickle.load(f)
        except Exception:
            self._artifact = None
        return self._artifact

    def predict(self, rows: list[dict[str, Any]], as_of_date: str, regime: str = "unknown") -> list[Prediction]:
        if not rows:
            return []
        art = self._load()
        if not art or str(art.get("trained_as_of", "")) != as_of_date:
            prior_status = self.status()
            if str(prior_status.get("trained_as_of", "")) != as_of_date:
                self.train(as_of_date)
                art = self._load()
            else:
                # A current-day warmup/validation gate must not silently reuse yesterday's model.
                art = None

        # Deterministic fallback is intentionally conservative until enough clean history exists.
        if not art:
            out = []
            for r in rows:
                h = float(r.get("heuristic_score", r.get("score", 50.0)))
                p = _clip01(_sigmoid((h - 55.0) / 12.0))
                er = max(-2.5, min(2.5, (h - 55.0) * 0.035))
                out.append(Prediction(
                    up_probability=p, expected_return_pct=er, expected_mfe_pct=max(0.0, er + 1.5),
                    expected_mae_pct=min(0.0, er - 1.5), lower_return_pct=er - 3.0, upper_return_pct=er + 3.0,
                    model_score=h, blend_weight=0.0, active=False, quality=0.0, regime=regime,
                ))
            return out

        feature_names = list(art["feature_names"])
        X = self._matrix(rows, feature_names)
        cls_pred: dict[str, np.ndarray] = {}
        for name, model in art["classifiers"].items():
            cls_pred[name] = model.predict_proba(X)[:, 1]
        p_raw = sum(float(art["class_weights"].get(k, 0)) * cls_pred[k] for k in cls_pred)
        cal = art["calibrator"]
        if art.get("calibration_method") == "isotonic":
            p = cal.predict(p_raw)
        else:
            p = cal.predict_proba(np.asarray(p_raw).reshape(-1, 1))[:, 1]
        p = np.clip(p, 0.02, 0.98)

        reg_pred: dict[str, np.ndarray] = {}
        for name, model in art["regressors"].items():
            reg_pred[name] = model.predict(X)
        er = sum(float(art["reg_weights"].get(k, 0)) * reg_pred[k] for k in reg_pred)
        mfe_pred = {name: model.predict(X) for name, model in art.get("mfe_regressors", {}).items()}
        mae_pred = {name: model.predict(X) for name, model in art.get("mae_regressors", {}).items()}
        emfe = sum(float(art.get("mfe_reg_weights", {}).get(k, 0)) * mfe_pred[k] for k in mfe_pred) if mfe_pred else np.maximum(er, 0)
        emae = sum(float(art.get("mae_reg_weights", {}).get(k, 0)) * mae_pred[k] for k in mae_pred) if mae_pred else np.minimum(er, 0)
        emfe = np.clip(emfe, 0.0, 20.0)
        emae = np.clip(emae, -20.0, 0.0)
        lower = er + float(art["lower_resid"])
        upper = er + float(art["upper_resid"])

        blend = float(art.get("blend_weight", 0.0)) if bool(art.get("active")) else 0.0
        quality = float(art.get("quality", 0.0))
        out: list[Prediction] = []
        for i in range(len(rows)):
            # Probability and expected-return views are complementary. A wide/negative
            # lower interval penalizes fragile predictions.
            ret_component = _sigmoid(float(er[i]) / 1.25)
            # Prefer candidates whose expected intraday upside dominates expected adverse excursion.
            excursion_edge = float(emfe[i]) - abs(float(emae[i]))
            excursion_component = _sigmoid(excursion_edge / 1.5)
            uncertainty_penalty = max(0.0, -float(lower[i])) * float(self.pcfg.get("lower_bound_penalty_per_pct", 4.0))
            model_score = 100.0 * (0.48 * float(p[i]) + 0.32 * ret_component + 0.20 * excursion_component) - uncertainty_penalty
            model_score = max(0.0, min(100.0, model_score))
            out.append(Prediction(
                up_probability=round(float(p[i]), 5),
                expected_return_pct=round(float(er[i]), 5),
                expected_mfe_pct=round(float(emfe[i]), 5),
                expected_mae_pct=round(float(emae[i]), 5),
                lower_return_pct=round(float(lower[i]), 5),
                upper_return_pct=round(float(upper[i]), 5),
                model_score=round(model_score, 4),
                blend_weight=round(blend, 4),
                active=bool(art.get("active", False)),
                quality=round(quality, 4),
                regime=regime,
            ))
        return out


    def evaluate_day(self, trade_date: str) -> dict[str, Any]:
        """Evaluate frozen morning predictions against realized same-day outcomes."""
        rows = self.db.get_candidate_evaluations(trade_date)
        if not rows:
            return {"trade_date": trade_date, "samples": 0}
        probs, labels, exp_ret, actual, exp_mfe, act_mfe, exp_mae, act_mae, covered = [], [], [], [], [], [], [], [], []
        active_count = 0
        for r in rows:
            pred = dict(r.get("prediction") or {})
            if not pred:
                continue
            p = float(pred.get("up_probability", 0.0) or 0.0)
            er = float(pred.get("expected_return_pct", 0.0) or 0.0)
            lo = float(pred.get("lower_return_pct", -999.0) or -999.0)
            hi = float(pred.get("upper_return_pct", 999.0) or 999.0)
            act = float(r.get("net_open_to_close_pct") or 0.0)
            emfe = float(pred.get("expected_mfe_pct", 0.0) or 0.0)
            emae = float(pred.get("expected_mae_pct", 0.0) or 0.0)
            probs.append(max(1e-4, min(1 - 1e-4, p)))
            labels.append(int(bool(r.get("label_up"))))
            exp_ret.append(er)
            actual.append(act)
            exp_mfe.append(emfe)
            act_mfe.append(float(r.get("mfe_pct") or 0.0))
            exp_mae.append(emae)
            act_mae.append(float(r.get("mae_pct") or 0.0))
            covered.append(1 if lo <= act <= hi else 0)
            active_count += int(bool(pred.get("active")))
        if not probs:
            return {"trade_date": trade_date, "samples": len(rows), "predictions": 0}
        pp = np.asarray(probs, dtype=float)
        yy = np.asarray(labels, dtype=int)
        ee = np.asarray(exp_ret, dtype=float)
        aa = np.asarray(actual, dtype=float)
        emfea = np.asarray(exp_mfe, dtype=float)
        amfea = np.asarray(act_mfe, dtype=float)
        emaea = np.asarray(exp_mae, dtype=float)
        amaea = np.asarray(act_mae, dtype=float)
        result = {
            "trade_date": trade_date,
            "samples": len(rows),
            "predictions": len(probs),
            "model_active_predictions": active_count,
            "directional_accuracy": round(float(np.mean((pp >= 0.5).astype(int) == yy)), 4),
            "brier": round(float(np.mean((pp - yy) ** 2)), 6),
            "return_mae_pct": round(float(np.mean(np.abs(ee - aa))), 6),
            "mfe_mae_pct": round(float(np.mean(np.abs(emfea - amfea))), 6),
            "mae_mae_pct": round(float(np.mean(np.abs(emaea - amaea))), 6),
            "cross_sectional_rank_ic": round(_rank_corr(ee, aa), 6),
            "interval_coverage": round(float(np.mean(covered)), 4),
            "avg_actual_net_return_pct": round(float(np.mean(aa)), 5),
        }
        self.db.set_state("last_prediction_feedback", result)
        return result

    def status(self) -> dict[str, Any]:
        return self.db.get_state(self.STATE_KEY, {}) or {}
