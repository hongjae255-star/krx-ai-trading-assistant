import numpy as np
import pandas as pd

from stockbot.historical_v9 import FEATURES, build_features_and_labels, recency_weights


def synthetic(n=180):
    rng = np.random.default_rng(3)
    dates = pd.date_range("2020-01-01", periods=n, freq="B")
    close = 100 * np.cumprod(1 + rng.normal(0.0005, 0.012, n))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.02, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.02, n))
    vol = rng.integers(100_000, 3_000_000, n)
    return pd.DataFrame({"Date": dates, "Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol})


def test_feature_builder_is_backward_looking_at_decision_date():
    raw = synthetic()
    a = build_features_and_labels(raw, "TEST", "US")
    assert not a.empty
    assert set(FEATURES).issubset(a.columns)
    # Alter only far-future prices. An earlier decision row's features must not change.
    cutoff = raw.index[-30]
    raw2 = raw.copy()
    raw2.loc[raw2.index > cutoff, ["Open", "High", "Low", "Close"]] *= 3
    b = build_features_and_labels(raw2, "TEST", "US")
    d = a.iloc[-50]["date"]
    ar = a[a["date"] == d].iloc[0]
    br = b[b["date"] == d].iloc[0]
    for f in FEATURES:
        assert np.isclose(float(ar[f]), float(br[f]), equal_nan=True)


def test_labels_use_next_session_open():
    raw = synthetic()
    out = build_features_and_labels(raw, "TEST", "US")
    row = out.iloc[10]
    idx = raw.index[pd.to_datetime(raw["Date"]) == pd.to_datetime(row["date"])][0]
    nxt = raw.loc[idx + 1]
    expected = (nxt["High"] / nxt["Open"] - 1) * 100
    assert abs(float(row["day_mfe_pct"]) - float(expected)) < 1e-9


def test_recency_weights_are_larger_for_recent_rows():
    dates = pd.Series(pd.to_datetime(["2020-01-01", "2021-01-01", "2022-01-01"]))
    w = recency_weights(dates, 365)
    assert w[0] < w[1] < w[2]
    assert abs(w[-1] - 1.0) < 1e-12
