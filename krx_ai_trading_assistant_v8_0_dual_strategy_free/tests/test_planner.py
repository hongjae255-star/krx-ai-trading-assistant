import pandas as pd

from stockbot.models import Candidate
from stockbot.planner import make_plan


def test_plan_has_ordered_levels():
    daily = pd.DataFrame({
        "open": [100000, 101000, 102000, 103000, 104000, 105000] * 3,
        "high": [102000, 103000, 104000, 105000, 106000, 107000] * 3,
        "low": [99000, 100000, 101000, 102000, 103000, 104000] * 3,
        "close": [101000, 102000, 103000, 104000, 105000, 106000] * 3,
        "volume": [1000] * 18,
    })
    c = Candidate("000001", "TEST", 106000, features={}, final_score=80, risk_flags=[])
    cfg = {"technical": {"atr_period": 14, "entry_pullback_atr": 0.3, "second_entry_pullback_atr": 0.7, "stop_atr": 1.1, "target1_r": 1.2, "target2_r": 2.0}}
    p = make_plan(c, daily, cfg)
    assert p.stop_price < p.entry_low_2 <= p.entry_high_2 <= p.entry_high_1
    assert p.target1 < p.target2
    assert p.chase_limit > p.reference_price
