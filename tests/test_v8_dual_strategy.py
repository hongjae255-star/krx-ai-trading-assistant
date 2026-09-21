from pathlib import Path
import os

import numpy as np
import pandas as pd

from stockbot.config import Settings
from stockbot.db import Database
from stockbot.models import Candidate
from stockbot.strategy_lanes import DualStrategyEngine
from stockbot.institutional import InstitutionalTracker


def _settings(tmp_path):
    return Settings(tmp_path, {
        'technical': {'atr_period': 14},
        'prediction': {'estimated_round_trip_cost_bps': 25},
        'strategy_lanes': {
            'day_1pct': {
                'enabled': True, 'picks': 3, 'net_target_pct': 1.0,
                'estimated_round_trip_cost_bps': 25, 'minimum_atr_pct': .95,
                'maximum_atr_pct': 6, 'maximum_current_gain_pct': 8,
                'min_stop_pct': .55, 'max_stop_pct': .9,
                'actionable_score_by_regime': {'risk_on': 58, 'mixed': 61, 'risk_off': 65},
            },
            'swing': {
                'enabled': True, 'picks': 3, 'history_days': 260,
                'deep_history_candidates': 10, 'deep_history_candidates_kr': 10,
                'deep_history_candidates_us': 6, 'minimum_stage2_checks': 5,
                'maximum_current_gain_pct': 12, 'min_stop_pct': 3, 'max_stop_pct': 7,
                'actionable_score_by_regime': {'risk_on': 64, 'mixed': 68, 'risk_off': 72},
            },
        },
        'institutional_tracking': {'enabled': True, 'managers': []},
    })


def _daily(n=260, start=100.0, step=.35):
    close = start + np.arange(n) * step
    # enough day-to-day range for ATR and a clean rising Stage-2 structure
    return pd.DataFrame({
        'date': pd.date_range('2025-01-01', periods=n, freq='B').strftime('%Y%m%d'),
        'open': close - .45, 'high': close + 1.35, 'low': close - 1.25,
        'close': close, 'volume': np.linspace(1_000_000, 1_700_000, n),
        'turnover': np.linspace(1e9, 2e9, n),
    })


def _candidate(code='AAA', name='Alpha', price=190.0, score=80.0):
    f = {
        'liquidity': .85, 'turnover_acceleration': .82, 'volume_pressure': .80,
        'momentum_1d': .75, 'momentum_5d': .78, 'intraday_strength': .73,
        'trend_quality': .85, 'foreign_flow': .72, 'institution_flow': .74,
        'near_20d_high': .90, 'range_position_20d': .86,
        'market_live_risk_on': .78, 'macro_global_risk_on': .74,
        'overheating_inverse': .82, 'event_risk_inverse': .84,
        'xs_momentum_rank': .92, 'xs_trend_rank': .91, 'xs_flow_rank': .75,
        'broker_report_signal': .72, 'event_positive_strength': .70,
        'news_catalyst': .69, 'volatility_quality': .80,
    }
    return Candidate(code=code, name=name, price=price, change_pct=1.3,
                     features=f, final_score=score, risk_flags=[])


def test_day_lane_always_shows_watch_or_actionable_and_targets_net_one_percent(tmp_path):
    engine = DualStrategyEngine(_settings(tmp_path))
    c = _candidate(price=190.65)
    out = engine.day_lane([c], {c.code: _daily()}, 'KR')
    assert len(out['items']) == 1
    row = out['items'][0]
    assert row['status'] in {'ACTIONABLE', 'WATCH'}
    assert row['net_target_pct'] == 1.0
    assert row['gross_target_pct'] == 1.25
    assert abs(row['target_price'] / row['reference_price'] - 1.0125) < 1e-4
    assert '보장' in row['warning']


def test_swing_lane_uses_stage2_and_returns_visible_candidate(tmp_path):
    engine = DualStrategyEngine(_settings(tmp_path))
    d = _daily()
    c = _candidate(price=float(d.iloc[-1].close))
    out = engine.swing_lane([c], {c.code: d}, 'US', institutional_matcher=lambda _: {'score': .9, 'matches': [{'manager': 'Test Fund', 'change': 'ADD'}]})
    assert len(out['items']) == 1
    row = out['items'][0]
    passed = int(row['trend_template'].split('/')[0])
    assert passed >= 5
    assert row['horizon'] == '5–10 거래일'
    assert row['institutional']['matches'][0]['manager'] == 'Test Fund'
    assert row['status'] in {'ACTIONABLE', 'WATCH'}


def test_institutional_xml_parser_and_missing_user_agent_is_nonfatal(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    db = Database(tmp_path / 'institution.sqlite3')
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    tracker = InstitutionalTracker(settings, db)
    xml = '''<?xml version="1.0"?><informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable"><infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>12345</value><shrsOrPrnAmt><sshPrnamt>1000</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable></informationTable>'''
    rows = tracker._parse_table(xml)
    assert rows[0]['issuer'] == 'APPLE INC'
    assert rows[0]['shares'] == 1000
    state = tracker.refresh(force=True)
    assert state['status'] == 'setup_needed'


def test_us_swing_deep_history_limit_is_six(tmp_path):
    engine = DualStrategyEngine(_settings(tmp_path))
    calls = []
    candidates = [_candidate(str(i), f'Name {i}', 100+i, 90-i) for i in range(10)]
    short = _daily(60)
    daily_map = {c.code: short for c in candidates}
    def fetch(c, days):
        calls.append(c.code)
        return _daily(260, 100 + int(c.code))
    out = engine.swing_lane(candidates, daily_map, 'US', deep_history_fetcher=fetch)
    assert len(calls) == 6
    assert len(out['items']) <= 3
