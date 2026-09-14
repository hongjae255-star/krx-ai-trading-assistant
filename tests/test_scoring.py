from stockbot.scoring import normalize_weights, score


def test_normalize_weights_sum_to_one():
    w = normalize_weights({"a": 2, "b": 1})
    assert abs(sum(w.values()) - 1.0) < 1e-9
    assert w["a"] > w["b"]


def test_score_range():
    s = score({"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5})
    assert 49.9 <= s <= 50.1
