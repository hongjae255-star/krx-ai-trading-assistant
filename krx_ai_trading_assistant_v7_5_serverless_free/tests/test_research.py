from stockbot.research import ResearchManager


def test_rule_based_report_analysis_positive_revision():
    r = {
        "report_id": "x",
        "title": "3Q 실적 상향, 수주 호조",
        "summary": "영업이익 추정치 상향과 성장 기대",
        "opinion": "Buy",
    }
    out = ResearchManager.rule_based_analysis(r)
    assert out["sentiment"] > 0
    assert out["estimate_revision"] > 0


def test_stock_signal_rewards_multi_broker_positive_consensus(tmp_path):
    from stockbot.config import load_settings
    from stockbot.db import Database

    settings = load_settings()
    settings.config["storage"]["sqlite_path"] = str(tmp_path / "test.sqlite3")
    db = Database(tmp_path / "test.sqlite3")

    class DummyAI:
        pass

    mgr = ResearchManager(settings, db, DummyAI())
    reports = [
        {"code": "005930", "broker": "A증권", "sentiment": 0.8, "conviction": 0.8, "estimate_revision": 0.6, "target_price": 120000},
        {"code": "005930", "broker": "B증권", "sentiment": 0.7, "conviction": 0.9, "estimate_revision": 0.4, "target_price": 115000},
    ]
    sig = mgr.stock_signals(reports, {"005930": 100000})["005930"]
    assert sig["signal"] > 0.5
    assert sig["report_count"] == 2
    assert sig["broker_count"] == 2


def test_naver_research_list_parser_with_sample_html(tmp_path, monkeypatch):
    from bs4 import BeautifulSoup
    from stockbot.config import load_settings
    from stockbot.research import NaverResearchClient

    settings = load_settings()
    settings.config["research"]["fetch_detail_pages"] = False
    settings.config["research"]["max_pages"] = 1
    client = NaverResearchClient(settings)
    html = """
    <html><body><table>
      <tr>
        <td><a href='/item/main.naver?code=005930'>삼성전자</a></td>
        <td><a href='/research/company_read.naver?nid=12345&page=1'>HBM 실적 상향</a></td>
        <td>테스트증권</td><td></td><td>26.09.09</td><td>1234</td>
      </tr>
    </table></body></html>
    """
    monkeypatch.setattr(client, "_get", lambda *args, **kwargs: BeautifulSoup(html, "html.parser"))
    rows = client.fetch_recent("2026-09-09")
    assert len(rows) == 1
    assert rows[0].code == "005930"
    assert rows[0].broker == "테스트증권"
    assert rows[0].report_id == "naver:12345"
