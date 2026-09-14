from stockbot.features import add_cross_sectional_features
from stockbot.models import Candidate


def test_cross_sectional_ranks_order_candidates():
    a = Candidate(code="1", name="A", price=1, features={"liquidity": 0.1, "turnover_acceleration": 0.1, "volume_pressure": 0.1, "momentum_1d": 0.1, "momentum_5d": 0.1, "momentum_10d": 0.1, "foreign_flow": 0.1, "institution_flow": 0.1, "trend_quality": 0.1, "near_20d_high": 0.1, "range_position_20d": 0.1, "broker_report_signal": 0.1, "news_catalyst": 0.1})
    b = Candidate(code="2", name="B", price=1, features={"liquidity": 0.9, "turnover_acceleration": 0.9, "volume_pressure": 0.9, "momentum_1d": 0.9, "momentum_5d": 0.9, "momentum_10d": 0.9, "foreign_flow": 0.9, "institution_flow": 0.9, "trend_quality": 0.9, "near_20d_high": 0.9, "range_position_20d": 0.9, "broker_report_signal": 0.9, "news_catalyst": 0.9})
    add_cross_sectional_features([a, b])
    assert b.features["xs_liquidity_rank"] > a.features["xs_liquidity_rank"]
    assert b.features["xs_flow_rank"] > a.features["xs_flow_rank"]
