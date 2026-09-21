from types import SimpleNamespace
from pathlib import Path

import pandas as pd

from stockbot.config import Settings
from stockbot.db import Database
from stockbot.kis import KISClient
from stockbot.market_pulse import MarketPulseService
from stockbot.webapp import DashboardStore


class DummySettings:
    config = {}
    def path(self, key):
        return Path('/tmp/nonexistent-kis-token-v77.json')


def test_kis_domestic_index_daily_parses_output(monkeypatch):
    c = KISClient(DummySettings())
    monkeypatch.setattr(c, '_get', lambda *a, **k: {'output2': [
        {'stck_bsop_date':'20260911','bstp_nmix_prpr':'3300.1','bstp_nmix_oprc':'3290','bstp_nmix_hgpr':'3310','bstp_nmix_lwpr':'3280','acml_vol':'10'},
        {'stck_bsop_date':'20260912','bstp_nmix_prpr':'3310.2','bstp_nmix_oprc':'3300','bstp_nmix_hgpr':'3320','bstp_nmix_lwpr':'3290','acml_vol':'11'},
    ]})
    df = c.domestic_index_daily('0001', 30)
    assert list(df['date']) == ['20260911','20260912']
    assert float(df.iloc[-1]['close']) == 3310.2


def test_kis_overseas_index_daily_parses_output(monkeypatch):
    c = KISClient(DummySettings())
    monkeypatch.setattr(c, '_get', lambda *a, **k: {'output2': [
        {'stck_bsop_date':'20260911','ovrs_nmix_prpr':'22000','ovrs_nmix_oprc':'21900','ovrs_nmix_hgpr':'22100','ovrs_nmix_lwpr':'21800'},
        {'stck_bsop_date':'20260912','ovrs_nmix_prpr':'22100','ovrs_nmix_oprc':'22000','ovrs_nmix_hgpr':'22200','ovrs_nmix_lwpr':'21900'},
    ]})
    df = c.overseas_index_daily('.IXIC', 30)
    assert float(df.iloc[-1]['close']) == 22100


class FakeKIS:
    def _df(self, base):
        rows=[]
        for i in range(30):
            rows.append({'date':f'202608{i+1:02d}' if i < 31 else '', 'close':base+i, 'open':base+i-1, 'high':base+i+1, 'low':base+i-2})
        return pd.DataFrame(rows)
    def domestic_index_daily(self, code, days=30):
        return self._df(3000 if code == '0001' else 800)
    def overseas_index_daily(self, symbol, days=30):
        bases={'.IXIC':22000,'.SPX':6500,'.DJI':47000,'.RUT':2500}
        return self._df(bases[symbol])
    def overseas_daily_chart(self, symbol, exchange='NAS', days=30):
        return self._df(500)


def test_market_pulse_builds_index_metrics(tmp_path):
    settings=Settings(tmp_path, {'market_dashboard':{'index_refresh_minutes':30,'index_chart_days':30}})
    db=Database(tmp_path/'db.sqlite3')
    p=MarketPulseService(settings, FakeKIS(), db).snapshot(force=True)
    assert 'kospi' in p['indexes'] and 'nasdaq' in p['indexes']
    assert len(p['indexes']['kospi']['points']) == 30
    assert p['kr']['regime']['total'] == 2
    assert p['us']['regime']['total'] == 4


def test_dashboard_explains_rejected_candidates(tmp_path):
    cfg={
        'timezone':'Asia/Seoul',
        'storage':{'sqlite_path':'data/kr.sqlite3','us_sqlite_path':'data/us.sqlite3'},
        'prediction':{'minimum_final_score':60.0,'allow_abstain':True,'min_up_probability':0.56,'risk_off_score_add':4.0,'high_vol_score_add':2.0,'risk_off_probability_add':0.04,'high_event_risk_score_add':2.0},
        'market':{'final_pick_count':3},
        'us_market':{'minimum_final_score':58.0,'market':{'final_pick_count':3},'timezone':'America/New_York'},
        'monitoring':{},
    }
    settings=Settings(tmp_path,cfg)
    db=Database(settings.path('storage.sqlite_path'))
    td=DashboardStore(settings).today()
    c=SimpleNamespace(code='005930',name='테스트',price=70000,heuristic_score=59,raw_score=59,model_score=60,final_score=59.5,
                      features={'macro_event_safety':0.8},prediction={'active':True,'up_probability':0.52,'expected_return_pct':0.1,'regime':'neutral'},risk_flags=['model_low_edge'])
    db.save_candidate_snapshots(td,[c])
    store=DashboardStore(settings)
    diag=store.candidate_diagnostics('KR',td,[])
    assert diag['evaluated_count'] == 1
    assert diag['top'][0]['score'] == 59.5
    assert any('최종점수' in x for x in diag['top'][0]['reasons'])
    assert any('상승확률' in x for x in diag['top'][0]['reasons'])
