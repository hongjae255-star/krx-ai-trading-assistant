import pandas as pd

from stockbot.config import load_settings
from stockbot.jobs import TradingAssistant


def test_simulate_outcome_target2(tmp_path):
    # Reuse project config but isolate storage by monkey-patching paths in memory.
    settings = load_settings()
    settings.config["storage"]["sqlite_path"] = str(tmp_path / "test.sqlite3")
    settings.config["storage"]["token_cache_path"] = str(tmp_path / "token.json")
    settings.config["storage"]["dart_corp_cache_path"] = str(tmp_path / "dart.json")
    settings.config["storage"]["output_dir"] = str(tmp_path / "reports")
    bot = TradingAssistant(settings)
    rec = {
        "entry_low_1": 98.0, "entry_high_1": 99.0,
        "entry_low_2": 96.0, "entry_high_2": 97.0,
        "stop_price": 94.0, "target1": 104.0, "target2": 108.0,
    }
    bars = pd.DataFrame([
        {"time": "093000", "open": 101, "high": 102, "low": 100, "close": 101},
        {"time": "100000", "open": 100, "high": 101, "low": 98.5, "close": 99},
        {"time": "103000", "open": 100, "high": 105, "low": 99.5, "close": 104},
        {"time": "110000", "open": 105, "high": 109, "low": 104, "close": 108},
    ])
    out = bot.simulate_outcome(rec, bars)
    assert out["entered"] is True
    assert out["exit_reason"] == "target2"
    assert out["return_pct"] > 0
    assert out["mfe_pct"] > 0


def test_simulate_outcome_stop_wins_same_bar(tmp_path):
    settings = load_settings()
    settings.config["storage"]["sqlite_path"] = str(tmp_path / "test.sqlite3")
    settings.config["storage"]["token_cache_path"] = str(tmp_path / "token.json")
    settings.config["storage"]["dart_corp_cache_path"] = str(tmp_path / "dart.json")
    settings.config["storage"]["output_dir"] = str(tmp_path / "reports")
    bot = TradingAssistant(settings)
    rec = {
        "entry_low_1": 98.0, "entry_high_1": 99.0,
        "entry_low_2": 96.0, "entry_high_2": 97.0,
        "stop_price": 94.0, "target1": 104.0, "target2": 108.0,
    }
    # One minute touches entry, stop and target1. Conservative rule must choose stop.
    bars = pd.DataFrame([
        {"time": "093000", "open": 100, "high": 105, "low": 93, "close": 100},
    ])
    out = bot.simulate_outcome(rec, bars)
    assert out["entered"] is True
    assert out["exit_reason"] == "stop"
    assert out["return_pct"] < 0


def test_selection_evaluation_runs_even_when_entry_not_touched(tmp_path):
    settings = load_settings()
    settings.config["storage"]["sqlite_path"] = str(tmp_path / "test.sqlite3")
    settings.config["storage"]["token_cache_path"] = str(tmp_path / "token.json")
    settings.config["storage"]["dart_corp_cache_path"] = str(tmp_path / "dart.json")
    settings.config["storage"]["output_dir"] = str(tmp_path / "reports")
    bot = TradingAssistant(settings)
    rec = {
        "entry_low_1": 90.0, "entry_high_1": 91.0,
        "entry_low_2": 88.0, "entry_high_2": 89.0,
        "stop_price": 85.0, "target1": 95.0, "target2": 100.0,
    }
    bars = pd.DataFrame([
        {"time": "090000", "open": 100, "high": 101, "low": 99, "close": 100},
        {"time": "153000", "open": 104, "high": 106, "low": 103, "close": 105},
    ])
    strategy = bot.simulate_outcome(rec, bars)
    assert strategy["entered"] is False
    selection = bot.evaluate_selection(rec, bars, strategy)
    assert selection["open_to_close_pct"] == 5.0
    assert selection["strategy_entered"] is False
    assert selection["selection_reward"] > 0
