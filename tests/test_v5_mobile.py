from pathlib import Path

from stockbot.scheduler import _is_interval_time


def test_ten_minute_monitoring_schedule():
    assert _is_interval_time("09:10", "09:10", "15:20", 10)
    assert _is_interval_time("10:00", "09:10", "15:20", 10)
    assert _is_interval_time("15:20", "09:10", "15:20", 10)
    assert not _is_interval_time("09:15", "09:10", "15:20", 10)
    assert not _is_interval_time("15:30", "09:10", "15:20", 10)


def test_pwa_files_exist():
    root = Path(__file__).resolve().parents[1]
    for rel in ["web/index.html", "web/app.js", "web/styles.css", "web/manifest.webmanifest", "web/sw.js"]:
        assert (root / rel).exists()


def test_no_order_endpoint_in_v5_code():
    root = Path(__file__).resolve().parents[1] / "stockbot"
    text = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*.py"))
    forbidden = ["order-cash", "order-rvsecncl", "주문가능조회"]
    assert all(x not in text for x in forbidden)


def test_intraday_still_scans_when_morning_abstained():
    from stockbot.jobs import TradingAssistant

    class DummySettings:
        timezone = "Asia/Seoul"
        def get(self, key, default=None):
            return default

    class DummyDB:
        def __init__(self):
            self.states = {}
        def get_recommendations(self, trade_date):
            return []
        def get_state(self, key, default=None):
            return self.states.get(key, default)
        def set_state(self, key, value):
            self.states[key] = value

    class DummyNotifier:
        def __init__(self): self.messages=[]
        def send(self, text): self.messages.append(text); return True

    bot = TradingAssistant.__new__(TradingAssistant)
    bot.settings = DummySettings()
    bot.db = DummyDB()
    bot.notifier = DummyNotifier()
    called = {"value": False}
    def fake_replacement(recs):
        called["value"] = True
        assert recs == []
        return {"code":"005930","name":"삼성전자","score":75,"price":100,"status":"신규 주도 후보","entry_low":95,"entry_high":99,"chase_limit":105}
    bot.find_replacement = fake_replacement
    bot.format_intraday = lambda updates, replacement: f"replacement={replacement['code']}"
    result = bot.intraday(scan_replacement=True, force_summary=False)
    assert result == []
    assert called["value"] is True
    assert bot.db.states["last_replacement_candidate"]["code"] == "005930"
    assert bot.notifier.messages
