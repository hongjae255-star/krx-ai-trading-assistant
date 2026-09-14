from pathlib import Path

from stockbot.config import load_settings
from stockbot.local_analysis import LocalAnalyst


def test_local_engine_requires_no_openai_and_pings():
    settings = load_settings()
    analyst = LocalAnalyst(settings)
    assert analyst.uses_openai is False
    assert analyst.configured is True
    assert "LOCAL" in analyst.ping()


def test_local_broker_analysis_detects_positive_revision():
    settings = load_settings()
    analyst = LocalAnalyst(settings)
    rows = analyst.analyze_broker_reports([{
        "report_id": "r1",
        "title": "영업이익 추정치 상향, 신규 수주 호조",
        "summary": "목표가 상향. 영업이익 추정치를 18% 높임",
        "opinion": "Buy",
    }])
    assert len(rows) == 1
    assert rows[0]["sentiment"] > 0
    assert rows[0]["estimate_revision"] > 0
    assert rows[0]["conviction"] >= 0.4


def test_local_engine_flags_cb_risk(monkeypatch):
    settings = load_settings()
    analyst = LocalAnalyst(settings)
    monkeypatch.setattr(analyst.news, "fetch", lambda code: [{"title": "전환사채 발행 결정", "date": "", "source": "", "url": ""}])
    out = analyst.enrich(
        [{"code": "000001", "name": "테스트", "change_pct": 1.0}],
        {"000001": [{"report_nm": "전환사채권발행결정", "rcept_dt": "20260910"}]},
        {"000001": []},
    )
    ev = out["evaluations"][0]
    assert ev["news_catalyst"] < 0.5
    assert ev["event_risk_inverse"] < 0.5
    assert any("전환사채" in x for x in ev["risk_flags"])


def test_requirements_contains_no_openai():
    root = Path(__file__).resolve().parents[1]
    text = (root / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "openai" not in text
