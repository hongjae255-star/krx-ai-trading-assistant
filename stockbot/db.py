from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS recommendations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  created_at TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  score REAL NOT NULL,
  reference_price REAL NOT NULL,
  entry_low_1 REAL NOT NULL,
  entry_high_1 REAL NOT NULL,
  entry_low_2 REAL NOT NULL,
  entry_high_2 REAL NOT NULL,
  chase_limit REAL NOT NULL,
  stop_price REAL NOT NULL,
  target1 REAL NOT NULL,
  target2 REAL NOT NULL,
  weight_pct REAL NOT NULL,
  confidence REAL NOT NULL,
  rationale TEXT,
  features_json TEXT NOT NULL,
  risk_flags_json TEXT NOT NULL,
  UNIQUE(trade_date, code)
);

CREATE TABLE IF NOT EXISTS intraday_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  ts TEXT NOT NULL,
  code TEXT NOT NULL,
  price REAL,
  vwap REAL,
  day_high REAL,
  day_low REAL,
  change_pct REAL,
  turnover_krw REAL,
  volume REAL,
  status TEXT,
  payload_json TEXT
);

CREATE TABLE IF NOT EXISTS outcomes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  code TEXT NOT NULL,
  entered INTEGER NOT NULL,
  entry_time TEXT,
  entry_price REAL,
  exit_time TEXT,
  exit_price REAL,
  exit_reason TEXT,
  return_pct REAL,
  mfe_pct REAL,
  mae_pct REAL,
  reward REAL,
  UNIQUE(trade_date, code)
);

CREATE TABLE IF NOT EXISTS learning_state (
  key TEXT PRIMARY KEY,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  samples INTEGER NOT NULL,
  before_json TEXT NOT NULL,
  after_json TEXT NOT NULL,
  metrics_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS broker_reports (
  report_id TEXT PRIMARY KEY,
  report_date TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  title TEXT NOT NULL,
  broker TEXT,
  source TEXT,
  source_url TEXT,
  target_price REAL,
  opinion TEXT,
  summary TEXT,
  sentiment REAL DEFAULT 0,
  conviction REAL DEFAULT 0.5,
  estimate_revision REAL DEFAULT 0,
  analyzed INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  analyzed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_broker_reports_date ON broker_reports(report_date);
CREATE INDEX IF NOT EXISTS idx_broker_reports_code ON broker_reports(code);

CREATE TABLE IF NOT EXISTS pick_evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  code TEXT NOT NULL,
  open_price REAL,
  close_price REAL,
  day_high REAL,
  day_low REAL,
  open_to_close_pct REAL,
  mfe_pct REAL,
  mae_pct REAL,
  selection_reward REAL,
  strategy_entered INTEGER DEFAULT 0,
  strategy_return_pct REAL,
  strategy_reward REAL,
  UNIQUE(trade_date, code)
);


CREATE TABLE IF NOT EXISTS candidate_snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  created_at TEXT NOT NULL,
  code TEXT NOT NULL,
  name TEXT NOT NULL,
  price REAL NOT NULL,
  heuristic_score REAL NOT NULL,
  model_score REAL DEFAULT 0,
  final_score REAL NOT NULL,
  features_json TEXT NOT NULL,
  prediction_json TEXT NOT NULL,
  risk_flags_json TEXT NOT NULL,
  UNIQUE(trade_date, code)
);

CREATE TABLE IF NOT EXISTS candidate_evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  code TEXT NOT NULL,
  open_price REAL,
  close_price REAL,
  day_high REAL,
  day_low REAL,
  gross_open_to_close_pct REAL,
  net_open_to_close_pct REAL,
  mfe_pct REAL,
  mae_pct REAL,
  label_up INTEGER,
  selection_reward REAL,
  UNIQUE(trade_date, code)
);

CREATE TABLE IF NOT EXISTS model_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  trade_date TEXT NOT NULL,
  model_name TEXT NOT NULL,
  samples INTEGER NOT NULL,
  distinct_dates INTEGER NOT NULL,
  active INTEGER NOT NULL,
  metrics_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_candidate_snapshots_date ON candidate_snapshots(trade_date);
CREATE INDEX IF NOT EXISTS idx_candidate_evaluations_date ON candidate_evaluations(trade_date);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def save_recommendations(self, trade_date: str, plans: Iterable[Any]) -> None:
        with self.connect() as con:
            for p in plans:
                con.execute(
                    """INSERT OR IGNORE INTO recommendations
                    (trade_date, created_at, code, name, score, reference_price,
                     entry_low_1, entry_high_1, entry_low_2, entry_high_2, chase_limit,
                     stop_price, target1, target2, weight_pct, confidence, rationale,
                     features_json, risk_flags_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        trade_date, datetime.now().isoformat(timespec="seconds"), p.code, p.name,
                        p.score, p.reference_price, p.entry_low_1, p.entry_high_1,
                        p.entry_low_2, p.entry_high_2, p.chase_limit, p.stop_price,
                        p.target1, p.target2, p.weight_pct, p.confidence, p.rationale,
                        json.dumps(p.features, ensure_ascii=False),
                        json.dumps(p.risk_flags, ensure_ascii=False),
                    ),
                )

    def get_recommendations(self, trade_date: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM recommendations WHERE trade_date=? ORDER BY score DESC", (trade_date,)).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["features"] = json.loads(d.pop("features_json"))
            d["risk_flags"] = json.loads(d.pop("risk_flags_json"))
            result.append(d)
        return result

    def save_snapshot(self, trade_date: str, code: str, payload: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO intraday_snapshots
                (trade_date, ts, code, price, vwap, day_high, day_low, change_pct, turnover_krw, volume, status, payload_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade_date, datetime.now().isoformat(timespec="seconds"), code,
                    payload.get("price"), payload.get("vwap"), payload.get("day_high"), payload.get("day_low"),
                    payload.get("change_pct"), payload.get("turnover_krw"), payload.get("volume"), payload.get("status"),
                    json.dumps(payload, ensure_ascii=False),
                ),
            )

    def save_outcome(self, trade_date: str, code: str, outcome: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO outcomes
                (trade_date, code, entered, entry_time, entry_price, exit_time, exit_price, exit_reason,
                 return_pct, mfe_pct, mae_pct, reward)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade_date, code, int(bool(outcome.get("entered"))), outcome.get("entry_time"),
                    outcome.get("entry_price"), outcome.get("exit_time"), outcome.get("exit_price"),
                    outcome.get("exit_reason"), outcome.get("return_pct"), outcome.get("mfe_pct"),
                    outcome.get("mae_pct"), outcome.get("reward"),
                ),
            )

    def get_outcomes(self, since_days: int = 60) -> list[dict[str, Any]]:
        since = (datetime.now() - timedelta(days=since_days)).date().isoformat()
        with self.connect() as con:
            rows = con.execute(
                """SELECT o.*, r.features_json, r.score, r.name
                   FROM outcomes o JOIN recommendations r
                   ON o.trade_date=r.trade_date AND o.code=r.code
                   WHERE o.trade_date>=? ORDER BY o.trade_date, o.code""",
                (since,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["features"] = json.loads(d.pop("features_json"))
            out.append(d)
        return out

    def get_state(self, key: str, default: Any = None) -> Any:
        with self.connect() as con:
            row = con.execute("SELECT value_json FROM learning_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_state(self, key: str, value: Any) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO learning_state(key,value_json,updated_at) VALUES (?,?,?)
                   ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json, updated_at=excluded.updated_at""",
                (key, json.dumps(value, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
            )

    def save_learning_history(self, trade_date: str, samples: int, before: dict, after: dict, metrics: dict) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO learning_history(trade_date,samples,before_json,after_json,metrics_json,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (trade_date, samples, json.dumps(before), json.dumps(after), json.dumps(metrics), datetime.now().isoformat(timespec="seconds")),
            )

    def save_broker_report(self, report: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO broker_reports
                (report_id, report_date, code, name, title, broker, source, source_url, target_price,
                 opinion, summary, sentiment, conviction, estimate_revision, analyzed, created_at, analyzed_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(report_id) DO UPDATE SET
                  report_date=excluded.report_date, code=excluded.code, name=excluded.name,
                  title=excluded.title, broker=excluded.broker, source=excluded.source,
                  source_url=excluded.source_url, target_price=COALESCE(excluded.target_price, broker_reports.target_price),
                  opinion=CASE WHEN excluded.opinion<>'' THEN excluded.opinion ELSE broker_reports.opinion END,
                  summary=CASE WHEN excluded.summary<>'' THEN excluded.summary ELSE broker_reports.summary END""",
                (
                    report.get("report_id"), report.get("report_date"), report.get("code", ""),
                    report.get("name", ""), report.get("title", ""), report.get("broker", ""),
                    report.get("source", ""), report.get("source_url", ""), report.get("target_price"),
                    report.get("opinion", ""), report.get("summary", ""), float(report.get("sentiment", 0.0)),
                    float(report.get("conviction", 0.5)), float(report.get("estimate_revision", 0.0)),
                    int(bool(report.get("analyzed", False))), datetime.now().isoformat(timespec="seconds"),
                    datetime.now().isoformat(timespec="seconds") if report.get("analyzed") else None,
                ),
            )

    def update_broker_report_analysis(self, report_id: str, sentiment: float, conviction: float, estimate_revision: float) -> None:
        with self.connect() as con:
            con.execute(
                """UPDATE broker_reports SET sentiment=?, conviction=?, estimate_revision=?, analyzed=1, analyzed_at=?
                   WHERE report_id=?""",
                (sentiment, conviction, estimate_revision, datetime.now().isoformat(timespec="seconds"), report_id),
            )

    def get_broker_reports(self, report_date: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                """SELECT * FROM broker_reports WHERE report_date=? ORDER BY code, broker, report_id""",
                (report_date,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_broker_reports_between(self, start_date: str, end_date: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                """SELECT * FROM broker_reports WHERE report_date>=? AND report_date<=?
                   ORDER BY report_date DESC, code, broker, report_id""",
                (start_date, end_date),
            ).fetchall()
        return [dict(r) for r in rows]

    def save_pick_evaluation(self, trade_date: str, code: str, row: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO pick_evaluations
                (trade_date, code, open_price, close_price, day_high, day_low, open_to_close_pct, mfe_pct, mae_pct,
                 selection_reward, strategy_entered, strategy_return_pct, strategy_reward)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade_date, code, row.get("open_price"), row.get("close_price"), row.get("day_high"), row.get("day_low"),
                    row.get("open_to_close_pct"), row.get("mfe_pct"), row.get("mae_pct"), row.get("selection_reward"),
                    int(bool(row.get("strategy_entered"))), row.get("strategy_return_pct"), row.get("strategy_reward"),
                ),
            )

    def get_pick_evaluations(self, since_days: int = 60) -> list[dict[str, Any]]:
        since = (datetime.now() - timedelta(days=since_days)).date().isoformat()
        with self.connect() as con:
            rows = con.execute(
                """SELECT p.*, r.features_json, r.score, r.name
                   FROM pick_evaluations p JOIN recommendations r
                   ON p.trade_date=r.trade_date AND p.code=r.code
                   WHERE p.trade_date>=? ORDER BY p.trade_date, p.code""",
                (since,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["features"] = json.loads(d.pop("features_json"))
            out.append(d)
        return out



    def save_candidate_snapshots(self, trade_date: str, candidates: Iterable[Any]) -> None:
        with self.connect() as con:
            for c in candidates:
                pred = dict(getattr(c, "prediction", None) or {})
                feats = dict(getattr(c, "features", None) or {})
                flags = list(getattr(c, "risk_flags", None) or [])
                con.execute(
                    """INSERT OR IGNORE INTO candidate_snapshots
                    (trade_date, created_at, code, name, price, heuristic_score, model_score, final_score,
                     features_json, prediction_json, risk_flags_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        trade_date, datetime.now().isoformat(timespec="seconds"), getattr(c, "code"), getattr(c, "name"),
                        float(getattr(c, "price", 0.0)), float(getattr(c, "heuristic_score", getattr(c, "raw_score", 0.0))),
                        float(getattr(c, "model_score", 0.0)), float(getattr(c, "final_score", 0.0)),
                        json.dumps(feats, ensure_ascii=False), json.dumps(pred, ensure_ascii=False),
                        json.dumps(flags, ensure_ascii=False),
                    ),
                )

    def get_candidate_snapshots(self, trade_date: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM candidate_snapshots WHERE trade_date=? ORDER BY final_score DESC", (trade_date,)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["features"] = json.loads(d.pop("features_json"))
            d["prediction"] = json.loads(d.pop("prediction_json"))
            d["risk_flags"] = json.loads(d.pop("risk_flags_json"))
            out.append(d)
        return out

    def save_candidate_evaluation(self, trade_date: str, code: str, row: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT OR REPLACE INTO candidate_evaluations
                (trade_date, code, open_price, close_price, day_high, day_low, gross_open_to_close_pct,
                 net_open_to_close_pct, mfe_pct, mae_pct, label_up, selection_reward)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    trade_date, code, row.get("open_price"), row.get("close_price"), row.get("day_high"), row.get("day_low"),
                    row.get("gross_open_to_close_pct"), row.get("net_open_to_close_pct"), row.get("mfe_pct"), row.get("mae_pct"),
                    int(bool(row.get("label_up"))), row.get("selection_reward"),
                ),
            )

    def get_candidate_training_rows(self, as_of_date: str, since_days: int = 365) -> list[dict[str, Any]]:
        try:
            since = (datetime.fromisoformat(as_of_date) - timedelta(days=since_days)).date().isoformat()
        except Exception:
            since = (datetime.now() - timedelta(days=since_days)).date().isoformat()
        with self.connect() as con:
            rows = con.execute(
                """SELECT e.*, s.name, s.heuristic_score, s.model_score, s.final_score,
                          s.features_json, s.prediction_json
                   FROM candidate_evaluations e JOIN candidate_snapshots s
                   ON e.trade_date=s.trade_date AND e.code=s.code
                   WHERE e.trade_date>=? AND e.trade_date<?
                   ORDER BY e.trade_date, e.code""",
                (since, as_of_date),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["features"] = json.loads(d.pop("features_json"))
            d["prediction"] = json.loads(d.pop("prediction_json"))
            out.append(d)
        return out

    def save_model_history(self, trade_date: str, model_name: str, samples: int, distinct_dates: int, active: bool, metrics: dict[str, Any]) -> None:
        with self.connect() as con:
            con.execute(
                """INSERT INTO model_history
                (trade_date, model_name, samples, distinct_dates, active, metrics_json, created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (trade_date, model_name, samples, distinct_dates, int(bool(active)),
                 json.dumps(metrics, ensure_ascii=False), datetime.now().isoformat(timespec="seconds")),
            )

    def get_model_history(self, limit: int = 30) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM model_history ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(d.pop("metrics_json"))
            out.append(d)
        return out


    def get_candidate_evaluations(self, trade_date: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                """SELECT e.*, s.name, s.heuristic_score, s.model_score, s.final_score,
                          s.features_json, s.prediction_json
                   FROM candidate_evaluations e JOIN candidate_snapshots s
                   ON e.trade_date=s.trade_date AND e.code=s.code
                   WHERE e.trade_date=? ORDER BY s.final_score DESC""",
                (trade_date,),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["features"] = json.loads(d.pop("features_json"))
            d["prediction"] = json.loads(d.pop("prediction_json"))
            out.append(d)
        return out
