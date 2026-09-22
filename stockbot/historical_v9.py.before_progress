from __future__ import annotations

"""V9 long-history training pipeline.

This module is intentionally independent from the live KIS candidate database.
It builds leakage-safe daily-bar features from free historical OHLCV, creates two
labels (next-session +1% setup and 1~2 week swing), trains separate Korea/US
models and saves small model bundles that can be blended into the live engine.
"""

import argparse
import json
import math
import pickle
import re
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

try:
    from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
    from sklearn.metrics import accuracy_score, brier_score_loss, mean_absolute_error, roc_auc_score
except ImportError:  # pragma: no cover
    HistGradientBoostingClassifier = HistGradientBoostingRegressor = None  # type: ignore


FEATURES = [
    "ret_1d", "ret_5d", "ret_10d", "ret_20d",
    "volatility_10d", "volatility_20d", "atr_14_pct",
    "volume_ratio_5_20", "turnover_proxy_z20", "close_location",
    "range_pos_20", "dist_ma5", "dist_ma20", "dist_ma60",
    "ma5_over_20", "ma20_over_60", "drawdown_20", "breakout_20",
    "gap_1d", "up_days_10",
]


@dataclass
class TrainResult:
    market: str
    lane: str
    samples: int
    train_end: str
    test_start: str
    test_end: str
    auc: float | None
    brier: float | None
    accuracy: float | None
    mae_return: float | None
    positive_rate: float
    model_path: str


def _safe_symbol(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(s).strip())


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    x = df.copy()
    if isinstance(x.columns, pd.MultiIndex):
        # yfinance batch result can be either field/symbol or symbol/field.
        if "Close" in x.columns.get_level_values(0):
            raise ValueError("batch frame must be split per symbol before normalization")
    x.columns = [str(c).strip().lower().replace(" ", "_") for c in x.columns]
    rename = {"adj_close": "adj_close", "adjclose": "adj_close", "date": "date"}
    x = x.rename(columns=rename)
    if "date" not in x.columns:
        x = x.reset_index()
        x.columns = [str(c).strip().lower().replace(" ", "_") for c in x.columns]
        if "datetime" in x.columns and "date" not in x.columns:
            x = x.rename(columns={"datetime": "date"})
    needed = {"date", "open", "high", "low", "close", "volume"}
    if not needed.issubset(x.columns):
        return pd.DataFrame()
    x = x[[c for c in ["date", "open", "high", "low", "close", "adj_close", "volume"] if c in x.columns]].copy()
    x["date"] = pd.to_datetime(x["date"], errors="coerce").dt.tz_localize(None)
    for c in ["open", "high", "low", "close", "adj_close", "volume"]:
        if c in x:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    x = x.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date")
    x = x[(x["open"] > 0) & (x["high"] > 0) & (x["low"] > 0) & (x["close"] > 0)]
    return x.drop_duplicates("date", keep="last")


def build_features_and_labels(raw: pd.DataFrame, symbol: str, market: str) -> pd.DataFrame:
    x = _normalize_ohlcv(raw)
    if len(x) < 100:
        return pd.DataFrame()
    x = x.copy()
    c = x["close"]
    prev = c.shift(1)
    ret1 = c.pct_change()
    x["ret_1d"] = ret1
    for n in (5, 10, 20):
        x[f"ret_{n}d"] = c.pct_change(n)
    x["volatility_10d"] = ret1.rolling(10).std()
    x["volatility_20d"] = ret1.rolling(20).std()
    tr = pd.concat([(x["high"] - x["low"]), (x["high"] - prev).abs(), (x["low"] - prev).abs()], axis=1).max(axis=1)
    x["atr_14_pct"] = tr.rolling(14).mean() / c.replace(0, np.nan)
    vol5 = x["volume"].rolling(5).mean()
    vol20 = x["volume"].rolling(20).mean()
    x["volume_ratio_5_20"] = vol5 / vol20.replace(0, np.nan)
    turnover = c * x["volume"]
    tmean = turnover.rolling(20).mean()
    tstd = turnover.rolling(20).std().replace(0, np.nan)
    x["turnover_proxy_z20"] = (turnover - tmean) / tstd
    day_range = (x["high"] - x["low"]).replace(0, np.nan)
    x["close_location"] = (c - x["low"]) / day_range
    h20 = x["high"].rolling(20).max()
    l20 = x["low"].rolling(20).min()
    x["range_pos_20"] = (c - l20) / (h20 - l20).replace(0, np.nan)
    ma5, ma20, ma60 = c.rolling(5).mean(), c.rolling(20).mean(), c.rolling(60).mean()
    x["dist_ma5"] = c / ma5 - 1
    x["dist_ma20"] = c / ma20 - 1
    x["dist_ma60"] = c / ma60 - 1
    x["ma5_over_20"] = ma5 / ma20 - 1
    x["ma20_over_60"] = ma20 / ma60 - 1
    x["drawdown_20"] = c / h20 - 1
    x["breakout_20"] = c / h20.shift(1) - 1
    x["gap_1d"] = x["open"] / prev - 1
    x["up_days_10"] = (ret1 > 0).astype(float).rolling(10).mean()

    # Decision is made after today's close; entry reference is next session open.
    next_open = x["open"].shift(-1)
    next_high = x["high"].shift(-1)
    next_low = x["low"].shift(-1)
    next_close = x["close"].shift(-1)
    x["day_mfe_pct"] = (next_high / next_open - 1.0) * 100.0
    x["day_mae_pct"] = (next_low / next_open - 1.0) * 100.0
    x["day_close_pct"] = (next_close / next_open - 1.0) * 100.0
    # Daily OHLC cannot determine ordering when both +1% and -1% are touched.
    # Use conservative success: +1% reached without also touching -1%.
    x["day_success"] = ((x["day_mfe_pct"] >= 1.0) & (x["day_mae_pct"] > -1.0)).astype(float)

    # 10-trading-day forward swing labels, always starting at next open.
    f_high = pd.concat([x["high"].shift(-i) for i in range(1, 11)], axis=1).max(axis=1)
    f_low = pd.concat([x["low"].shift(-i) for i in range(1, 11)], axis=1).min(axis=1)
    close10 = x["close"].shift(-10)
    x["swing_mfe_pct"] = (f_high / next_open - 1.0) * 100.0
    x["swing_mae_pct"] = (f_low / next_open - 1.0) * 100.0
    x["swing_return_pct"] = (close10 / next_open - 1.0) * 100.0
    # Swing success balances meaningful upside and risk. Kept simple/interpretable.
    x["swing_success"] = ((x["swing_mfe_pct"] >= 4.0) & (x["swing_mae_pct"] > -6.0)).astype(float)

    x["symbol"] = symbol
    x["market"] = market.upper()
    cols = ["date", "symbol", "market"] + FEATURES + [
        "day_success", "day_mfe_pct", "day_mae_pct", "day_close_pct",
        "swing_success", "swing_mfe_pct", "swing_mae_pct", "swing_return_pct",
    ]
    out = x[cols].replace([np.inf, -np.inf], np.nan).dropna(subset=FEATURES).copy()
    return out


def recency_weights(dates: pd.Series, half_life_days: float = 365.0) -> np.ndarray:
    d = pd.to_datetime(dates)
    age = (d.max() - d).dt.days.astype(float).to_numpy()
    return np.exp(-math.log(2.0) * age / max(1.0, half_life_days))


def _safe_auc(y: np.ndarray, p: np.ndarray) -> float | None:
    try:
        if len(np.unique(y)) < 2:
            return None
        return float(roc_auc_score(y, p))
    except Exception:
        return None


def _bounded_sample(df: pd.DataFrame, max_rows: int, seed: int) -> pd.DataFrame:
    if len(df) <= max_rows:
        return df
    rng = np.random.default_rng(seed)
    idx = np.sort(rng.choice(len(df), size=max_rows, replace=False))
    return df.iloc[idx].copy()


def train_one(dataset: pd.DataFrame, market: str, lane: str, model_dir: Path,
              test_days: int = 252, half_life_days: int = 730, random_seed: int = 42,
              walk_forward_folds: int = 3, max_fit_rows: int = 750_000,
              max_eval_rows: int = 300_000) -> TrainResult:
    """Train one market/horizon bundle with expanding walk-forward evaluation.

    Evaluation always trains on dates strictly before the test block. After the
    metrics are produced, a deployment model is refit on every labeled row so
    the live system benefits from the newest available history.
    """
    if HistGradientBoostingClassifier is None:
        raise RuntimeError("scikit-learn is required")
    df = dataset[dataset["market"].str.upper() == market.upper()].copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "symbol"])
    if lane == "day":
        label, target = "day_success", "day_close_pct"
    elif lane == "swing":
        label, target = "swing_success", "swing_return_pct"
    else:
        raise ValueError("lane must be day or swing")
    df = df.dropna(subset=FEATURES + [label, target]).copy()
    unique_dates = np.asarray(sorted(df["date"].dt.normalize().unique()))
    if len(unique_dates) < 400:
        raise ValueError(f"not enough trading dates: {len(unique_dates)}")

    # Expanding walk-forward blocks over roughly the final 40% of history.
    first_test_i = max(252, int(len(unique_dates) * 0.60))
    remaining = unique_dates[first_test_i:]
    blocks = [b for b in np.array_split(remaining, min(max(1, walk_forward_folds), len(remaining))) if len(b)]
    all_y: list[np.ndarray] = []
    all_p: list[np.ndarray] = []
    all_r: list[np.ndarray] = []
    all_rp: list[np.ndarray] = []
    fold_rows: list[dict[str, Any]] = []

    for fold_i, block in enumerate(blocks):
        start_date = pd.Timestamp(block[0])
        end_date = pd.Timestamp(block[-1])
        train = df[df["date"] < start_date].copy()
        test = df[(df["date"] >= start_date) & (df["date"] <= end_date)].copy()
        if len(train) < 1000 or len(test) < 100:
            continue
        train_fit = _bounded_sample(train, max_fit_rows, random_seed + fold_i)
        test_eval = _bounded_sample(test, max_eval_rows, random_seed + 100 + fold_i)
        Xtr = train_fit[FEATURES].astype(float).fillna(0.0).clip(-20, 20).to_numpy()
        Xte = test_eval[FEATURES].astype(float).fillna(0.0).clip(-20, 20).to_numpy()
        ytr = train_fit[label].astype(int).to_numpy()
        yte = test_eval[label].astype(int).to_numpy()
        rtr = train_fit[target].astype(float).clip(-30, 30).to_numpy()
        rte = test_eval[target].astype(float).clip(-30, 30).to_numpy()
        sw = recency_weights(train_fit["date"], half_life_days)
        clf = HistGradientBoostingClassifier(max_iter=150, learning_rate=0.06, max_leaf_nodes=31,
                                             l2_regularization=1.0, random_state=random_seed + fold_i)
        reg = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.06, max_leaf_nodes=31,
                                            l2_regularization=1.0, random_state=random_seed + fold_i)
        clf.fit(Xtr, ytr, sample_weight=sw)
        reg.fit(Xtr, rtr, sample_weight=sw)
        prob = clf.predict_proba(Xte)[:, 1]
        pred = reg.predict(Xte)
        all_y.append(yte); all_p.append(prob); all_r.append(rte); all_rp.append(pred)
        fold_rows.append({
            "fold": fold_i + 1, "train_end": str(train["date"].max().date()),
            "test_start": str(start_date.date()), "test_end": str(end_date.date()),
            "train_rows": len(train_fit), "test_rows": len(test_eval),
            "auc": _safe_auc(yte, prob), "brier": float(brier_score_loss(yte, prob)),
            "accuracy": float(accuracy_score(yte, prob >= 0.5)),
            "mae_return": float(mean_absolute_error(rte, pred)),
        })
    if not all_y:
        raise ValueError("walk-forward produced no valid folds")
    y_eval = np.concatenate(all_y); p_eval = np.concatenate(all_p)
    r_eval = np.concatenate(all_r); rp_eval = np.concatenate(all_rp)
    auc = _safe_auc(y_eval, p_eval)
    brier = float(brier_score_loss(y_eval, p_eval))
    acc = float(accuracy_score(y_eval, p_eval >= 0.5))
    mae = float(mean_absolute_error(r_eval, rp_eval))

    # Deployment refit: use all labeled history, still with recency weighting.
    fit = _bounded_sample(df, max_fit_rows, random_seed + 999)
    X = fit[FEATURES].astype(float).fillna(0.0).clip(-20, 20).to_numpy()
    y = fit[label].astype(int).to_numpy()
    rr = fit[target].astype(float).clip(-30, 30).to_numpy()
    sw = recency_weights(fit["date"], half_life_days)
    clf = HistGradientBoostingClassifier(max_iter=180, learning_rate=0.055, max_leaf_nodes=31,
                                         l2_regularization=1.0, random_state=random_seed)
    reg = HistGradientBoostingRegressor(max_iter=180, learning_rate=0.055, max_leaf_nodes=31,
                                        l2_regularization=1.0, random_state=random_seed)
    clf.fit(X, y, sample_weight=sw)
    reg.fit(X, rr, sample_weight=sw)

    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = model_dir / f"historical_{market.lower()}_{lane}.pkl"
    bundle = {
        "version": 9, "market": market.upper(), "lane": lane, "features": FEATURES,
        "classifier": clf, "return_regressor": reg,
        "trained_through": str(df["date"].max().date()),
        "walk_forward": fold_rows,
        "metrics": {"auc": auc, "brier": brier, "accuracy": acc, "mae_return": mae,
                    "positive_rate": float(y_eval.mean()), "samples_total": len(df),
                    "samples_fit": len(fit), "samples_eval": len(y_eval), "folds": len(fold_rows)},
        "label_definition": "+1% without -1% touch" if lane == "day" else "10d MFE>=4% and MAE>-6%",
    }
    with model_path.open("wb") as f:
        pickle.dump(bundle, f)
    return TrainResult(
        market=market.upper(), lane=lane, samples=len(df),
        train_end=bundle["trained_through"], test_start=fold_rows[0]["test_start"], test_end=fold_rows[-1]["test_end"],
        auc=auc, brier=brier, accuracy=acc, mae_return=mae, positive_rate=float(y_eval.mean()),
        model_path=str(model_path),
    )


def predict_bundle(model_path: str | Path, feature_rows: pd.DataFrame) -> pd.DataFrame:
    with Path(model_path).open("rb") as f:
        b = pickle.load(f)
    feats = list(b["features"])
    X = feature_rows[feats].astype(float).fillna(0.0).clip(-20, 20).to_numpy()
    out = feature_rows.copy()
    out["historical_probability"] = b["classifier"].predict_proba(X)[:, 1]
    out["historical_expected_return_pct"] = b["return_regressor"].predict(X)
    return out


def _download_yfinance_symbol(symbol: str, start: str, end: str) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("Install yfinance: pip install yfinance") from exc
    df = yf.download(symbol, start=start, end=end, auto_adjust=True, actions=False,
                     progress=False, threads=False, timeout=20)
    if isinstance(df.columns, pd.MultiIndex):
        # single ticker with new yfinance versions returns (field,ticker)
        df.columns = [c[0] for c in df.columns]
    return _normalize_ohlcv(df)


def korea_symbols(as_of: str | None = None) -> list[tuple[str, str]]:
    """
    Return current KOSPI/KOSDAQ universe.

    FinanceDataReader is used first because pykrx universe discovery can
    intermittently fail when the KRX website changes or returns non-JSON data.
    pykrx is retained only as a fallback.
    """
    rows: list[tuple[str, str]] = []

    # Primary source: FinanceDataReader
    try:
        import FinanceDataReader as fdr

        for market, suffix in (("KOSPI", ".KS"), ("KOSDAQ", ".KQ")):
            df = fdr.StockListing(market)

            if df is None or df.empty:
                continue

            if "Code" in df.columns:
                code_col = "Code"
            elif "Symbol" in df.columns:
                code_col = "Symbol"
            else:
                continue

            for code in df[code_col].dropna().astype(str):
                code = code.strip().zfill(6)
                if code.isdigit() and len(code) == 6:
                    rows.append((code + suffix, market))

        if rows:
            return list(dict.fromkeys(rows))

    except Exception as exc:
        print(f"[KR universe] FinanceDataReader failed: {exc}")

    # Fallback: pykrx
    try:
        from pykrx import stock

        d = (as_of or date.today().strftime("%Y%m%d")).replace("-", "")
        cur = pd.Timestamp(d)

        for _ in range(14):
            ds = cur.strftime("%Y%m%d")
            rows = []

            try:
                for market in ("KOSPI", "KOSDAQ"):
                    suffix = ".KS" if market == "KOSPI" else ".KQ"
                    codes = stock.get_market_ticker_list(ds, market=market)

                    for code in codes:
                        rows.append((f"{code}{suffix}", market))
            except Exception as exc:
                print(f"[KR universe] pykrx failed for {ds}: {exc}")
                rows = []

            if rows:
                return list(dict.fromkeys(rows))

            cur -= pd.Timedelta(days=1)

    except Exception as exc:
        print(f"[KR universe] pykrx fallback failed: {exc}")

    raise RuntimeError(
        "Could not obtain Korean stock universe from FinanceDataReader or pykrx."
    )


def usa_symbols() -> list[tuple[str, str]]:
    import requests
    urls = [
        ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", "NASDAQ"),
        ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", "NYSE/AMEX"),
    ]
    out: list[tuple[str, str]] = []
    for url, exch in urls:
        r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        lines = r.text.splitlines()
        if not lines:
            continue
        header = lines[0].split("|")
        sym_col = "Symbol" if "Symbol" in header else "ACT Symbol"
        si = header.index(sym_col)
        etf_i = header.index("ETF") if "ETF" in header else -1
        test_i = header.index("Test Issue") if "Test Issue" in header else -1
        for line in lines[1:]:
            parts = line.split("|")
            if len(parts) <= si or "File Creation Time" in line:
                continue
            if test_i >= 0 and len(parts) > test_i and parts[test_i] == "Y":
                continue
            if etf_i >= 0 and len(parts) > etf_i and parts[etf_i] == "Y":
                continue
            s = parts[si].strip().replace(".", "-")
            if s and "$" not in s and "^" not in s:
                out.append((s, exch))
    return sorted(set(out))


def download_market(market: str, raw_dir: Path, years: int = 10, max_symbols: int | None = None,
                    sleep_seconds: float = 0.15, resume: bool = True) -> dict[str, Any]:
    market = market.upper()
    end_d = date.today() + timedelta(days=1)
    start_d = end_d - timedelta(days=int(years * 365.25) + 90)
    symbols = korea_symbols() if market == "KR" else usa_symbols()
    if max_symbols:
        symbols = symbols[: int(max_symbols)]
    out_dir = raw_dir / market.lower()
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = failed = skipped = 0
    failures: list[dict[str, str]] = []
    for i, (symbol, exchange) in enumerate(symbols, 1):
        path = out_dir / f"{_safe_symbol(symbol)}.csv.gz"
        if resume and path.exists():
            try:
                if len(pd.read_csv(path, nrows=5)) > 0:
                    skipped += 1
                    continue
            except Exception:
                pass
        try:
            df = _download_yfinance_symbol(symbol, str(start_d), str(end_d))
            if len(df) < 80:
                raise ValueError(f"only {len(df)} rows")
            df["symbol"] = symbol
            df["exchange"] = exchange
            df.to_csv(path, index=False, compression="gzip")
            ok += 1
        except Exception as exc:
            failed += 1
            if len(failures) < 100:
                failures.append({"symbol": symbol, "error": str(exc)[:180]})
        if sleep_seconds:
            time.sleep(float(sleep_seconds))
    summary = {"market": market, "symbols": len(symbols), "downloaded": ok, "skipped": skipped,
               "failed": failed, "start": str(start_d), "end": str(end_d), "failures": failures}
    (out_dir / "_download_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def build_dataset(raw_dir: Path, dataset_dir: Path, market: str) -> dict[str, Any]:
    market = market.upper()
    frames: list[pd.DataFrame] = []
    source = raw_dir / market.lower()
    files = sorted(source.glob("*.csv.gz"))
    for p in files:
        try:
            raw = pd.read_csv(p)
            symbol = str(raw["symbol"].iloc[0]) if "symbol" in raw and len(raw) else p.stem
            f = build_features_and_labels(raw, symbol, market)
            if not f.empty:
                frames.append(f)
        except Exception:
            continue
    if not frames:
        raise RuntimeError(f"no usable {market} raw files in {source}")
    ds = pd.concat(frames, ignore_index=True)
    ds["date"] = pd.to_datetime(ds["date"]).dt.strftime("%Y-%m-%d")
    dataset_dir.mkdir(parents=True, exist_ok=True)
    path = dataset_dir / f"historical_{market.lower()}_features.csv.gz"
    ds.to_csv(path, index=False, compression="gzip")
    meta = {"market": market, "symbols": int(ds["symbol"].nunique()), "rows": len(ds),
            "first_date": str(ds["date"].min()), "last_date": str(ds["date"].max()), "path": str(path)}
    (dataset_dir / f"historical_{market.lower()}_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def train_all(dataset_dir: Path, model_dir: Path) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for market in ("KR", "US"):
        p = dataset_dir / f"historical_{market.lower()}_features.csv.gz"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        for lane in ("day", "swing"):
            results.append(train_one(df, market, lane, model_dir).__dict__)
    (model_dir / "historical_v9_training_report.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def cli(root: Path, argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="historical-v9")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download")
    d.add_argument("market", choices=["KR", "US", "ALL"])
    d.add_argument("--years", type=int, default=10)
    d.add_argument("--max-symbols", type=int, default=None)
    b = sub.add_parser("build")
    b.add_argument("market", choices=["KR", "US", "ALL"])
    sub.add_parser("train")
    sub.add_parser("all")
    args = p.parse_args(argv)
    raw = root / "data" / "historical" / "raw"
    ds = root / "data" / "historical" / "datasets"
    models = root / "data" / "models"
    if args.cmd in {"download", "all"}:
        markets = ["KR", "US"] if getattr(args, "market", "ALL") == "ALL" or args.cmd == "all" else [args.market]
        for m in markets:
            print(json.dumps(download_market(m, raw, years=getattr(args, "years", 10), max_symbols=getattr(args, "max_symbols", None)), ensure_ascii=False, indent=2))
    if args.cmd in {"build", "all"}:
        markets = ["KR", "US"] if getattr(args, "market", "ALL") == "ALL" or args.cmd == "all" else [args.market]
        for m in markets:
            print(json.dumps(build_dataset(raw, ds, m), ensure_ascii=False, indent=2))
    if args.cmd in {"train", "all"}:
        print(json.dumps(train_all(ds, models), ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(cli(Path(__file__).resolve().parents[1]))
