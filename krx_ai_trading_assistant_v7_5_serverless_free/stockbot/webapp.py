from __future__ import annotations

import json
import logging
import mimetypes
import os
from datetime import datetime, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from .config import Settings
from .db import Database

log = logging.getLogger(__name__)


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


class DashboardStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = Database(settings.path("storage.sqlite_path"))
        self.us_db = Database(settings.path("storage.us_sqlite_path"))
        self.tz = ZoneInfo(settings.timezone)
        self.us_tz = ZoneInfo(str(settings.get("us_market.timezone", "America/New_York")))

    def today(self) -> str:
        return datetime.now(self.tz).date().isoformat()


    def us_today(self) -> str:
        return datetime.now(self.us_tz).date().isoformat()

    def _latest_snapshot_map_db(self, db: Database, trade_date: str) -> dict[str, dict]:
        with db.connect() as con:
            rows = con.execute(
                """
                SELECT s.* FROM intraday_snapshots s
                JOIN (
                  SELECT code, MAX(id) AS max_id
                  FROM intraday_snapshots
                  WHERE trade_date=?
                  GROUP BY code
                ) x ON x.max_id=s.id
                ORDER BY s.id DESC
                """,
                (trade_date,),
            ).fetchall()
        out = {}
        for r in rows:
            d = dict(r)
            try: payload = json.loads(d.get("payload_json") or "{}")
            except Exception: payload = {}
            d.update(payload); out[str(d.get("code", ""))] = d
        return out

    def _latest_snapshot_map(self, trade_date: str) -> dict[str, dict]:
        return self._latest_snapshot_map_db(self.db, trade_date)

    def us_recommendations(self, trade_date: str | None = None) -> list[dict]:
        trade_date = trade_date or self.us_today()
        recs = self.us_db.get_recommendations(trade_date)
        snaps = self._latest_snapshot_map_db(self.us_db, trade_date)
        for r in recs:
            snap = snaps.get(str(r["code"]), {})
            r["market"] = "US"
            r["currency"] = "USD"
            r["live"] = {
                "price": _safe_float(snap.get("price"), r.get("reference_price", 0)),
                "vwap": _safe_float(snap.get("vwap")),
                "day_high": _safe_float(snap.get("day_high")),
                "day_low": _safe_float(snap.get("day_low")),
                "change_pct": _safe_float(snap.get("change_pct")),
                "status": snap.get("status") or "US 장전 전략",
                "ts": snap.get("ts") or r.get("created_at"),
            }
        return recs

    def global_macro(self) -> dict:
        base = self.db.get_state("global_macro_latest", {}) or {}
        live = self.db.get_state("global_macro_live_latest", {}) or {}
        out = dict(base)
        out["live"] = live
        return out

    def us_learning_summary(self) -> dict:
        state = self.us_db.get_state("last_prediction_feedback", {}) or {}
        with self.us_db.connect() as con:
            recent = con.execute("SELECT * FROM pick_evaluations ORDER BY trade_date DESC, id DESC LIMIT 50").fetchall()
        rows = [dict(x) for x in recent]
        wins = sum(1 for x in rows if _safe_float(x.get("open_to_close_pct")) > 0)
        avg = sum(_safe_float(x.get("open_to_close_pct")) for x in rows) / len(rows) if rows else 0.0
        return {"samples": len(rows), "win_rate": 100*wins/len(rows) if rows else 0.0, "avg_return_pct": avg, "prediction_feedback": state}

    def recommendations(self, trade_date: str | None = None) -> list[dict]:
        trade_date = trade_date or self.today()
        recs = self.db.get_recommendations(trade_date)
        snaps = self._latest_snapshot_map(trade_date)
        for r in recs:
            snap = snaps.get(str(r["code"]), {})
            r["live"] = {
                "price": _safe_float(snap.get("price"), r.get("reference_price", 0)),
                "vwap": _safe_float(snap.get("vwap")),
                "day_high": _safe_float(snap.get("day_high")),
                "day_low": _safe_float(snap.get("day_low")),
                "change_pct": _safe_float(snap.get("change_pct")),
                "status": snap.get("status") or "장전 전략",
                "ts": snap.get("ts") or r.get("created_at"),
            }
        return recs

    def reports_summary(self, trade_date: str | None = None) -> dict:
        trade_date = trade_date or self.today()
        rows = self.db.get_broker_reports(trade_date)
        by_code = {}
        brokers = set()
        for r in rows:
            code = str(r.get("code", ""))
            if not code:
                continue
            brokers.add(str(r.get("broker", "")))
            item = by_code.setdefault(code, {
                "code": code, "name": r.get("name") or code,
                "count": 0, "sentiment_sum": 0.0, "revision_sum": 0.0,
                "target_prices": [], "brokers": set(),
            })
            item["count"] += 1
            item["sentiment_sum"] += _safe_float(r.get("sentiment"))
            item["revision_sum"] += _safe_float(r.get("estimate_revision"))
            if _safe_float(r.get("target_price")) > 0:
                item["target_prices"].append(_safe_float(r.get("target_price")))
            if r.get("broker"):
                item["brokers"].add(str(r.get("broker")))
        ranked = []
        for x in by_code.values():
            n = max(1, x["count"])
            x["sentiment"] = x["sentiment_sum"] / n
            x["revision"] = x["revision_sum"] / n
            x["broker_count"] = len(x["brokers"])
            x["avg_target_price"] = sum(x["target_prices"]) / len(x["target_prices"]) if x["target_prices"] else 0
            x.pop("brokers", None); x.pop("sentiment_sum", None); x.pop("revision_sum", None); x.pop("target_prices", None)
            x["signal"] = max(0.0, min(1.0, 0.5 + 0.28*x["sentiment"] + 0.22*x["revision"]))
            ranked.append(x)
        ranked.sort(key=lambda z: (z["signal"], z["count"]), reverse=True)
        return {"report_count": len(rows), "broker_count": len([x for x in brokers if x]), "top": ranked[:10]}

    def learning_summary(self) -> dict:
        state = self.db.get_state("last_prediction_feedback", {}) or {}
        with self.db.connect() as con:
            latest = con.execute("SELECT * FROM learning_history ORDER BY id DESC LIMIT 1").fetchone()
            recent = con.execute(
                "SELECT * FROM pick_evaluations ORDER BY trade_date DESC, id DESC LIMIT 50"
            ).fetchall()
        recent = [dict(x) for x in recent]
        wins = sum(1 for x in recent if _safe_float(x.get("open_to_close_pct")) > 0)
        avg = sum(_safe_float(x.get("open_to_close_pct")) for x in recent) / len(recent) if recent else 0.0
        return {
            "samples": len(recent),
            "win_rate": 100.0*wins/len(recent) if recent else 0.0,
            "avg_return_pct": avg,
            "prediction_feedback": state,
            "last_learning_date": dict(latest).get("trade_date") if latest else None,
        }

    def history(self, days: int = 10) -> list[dict]:
        since = (datetime.now(self.tz).date() - timedelta(days=days)).isoformat()
        with self.db.connect() as con:
            rows = con.execute(
                """SELECT p.*, r.name, r.score FROM pick_evaluations p
                   JOIN recommendations r ON p.trade_date=r.trade_date AND p.code=r.code
                   WHERE p.trade_date>=? ORDER BY p.trade_date DESC, r.score DESC""",
                (since,),
            ).fetchall()
        return [dict(x) for x in rows]

    def dashboard(self) -> dict:
        trade_date = self.today()
        recs = self.recommendations(trade_date)
        reports = self.reports_summary(trade_date)
        learning = self.learning_summary()
        last_update = None
        for r in recs:
            ts = (r.get("live") or {}).get("ts")
            if ts and (last_update is None or str(ts) > str(last_update)):
                last_update = ts
        replacement = self.db.get_state("last_replacement_candidate", None)
        return {
            "trade_date": trade_date,
            "server_time": datetime.now(self.tz).isoformat(timespec="seconds"),
            "last_update": last_update,
            "mode": "FREE_LOCAL_RULE_ML",
            "monitor_interval_minutes": int(self.settings.get("monitoring.active_interval_minutes", 10)),
            "replacement_interval_minutes": int(self.settings.get("monitoring.replacement_interval_minutes", 30)),
            "app_refresh_seconds": int(self.settings.get("monitoring.app_refresh_seconds", 20)),
            "replacement_candidate": replacement,
            "recommendations": recs,
            "reports": reports,
            "learning": learning,
            "positions": self.settings.get("existing_positions", []),
            "risk": self.settings.get("risk", {}),
            "global_macro": self.global_macro(),
            "us": {
                "trade_date": self.us_today(),
                "recommendations": self.us_recommendations(),
                "learning": self.us_learning_summary(),
                "monitor_interval_minutes": int(self.settings.get("us_market.scheduler.active_interval_minutes", 10)),
                "replacement_interval_minutes": int(self.settings.get("us_market.scheduler.replacement_interval_minutes", 30)),
                "replacement_candidate": self.us_db.get_state("us_last_replacement_candidate", None),
            },
        }


class AppHandler(BaseHTTPRequestHandler):
    server_version = "KRXAI/6.0"

    @property
    def app(self):
        return self.server.app_context  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args):
        log.info("web %s - %s", self.address_string(), fmt % args)

    def _authorized(self) -> bool:
        token = self.app["token"]
        if not token:
            return True
        got = self.headers.get("X-API-Token", "")
        return got == token

    def _send_json(self, obj, status=200):
        raw = _json_bytes(obj)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _serve_static(self, rel: str):
        root: Path = self.app["web_root"]
        target = (root / rel.lstrip("/")).resolve()
        if root.resolve() not in target.parents and target != root.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            target = root / "index.html"
        data = target.read_bytes()
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if target.suffix == ".webmanifest":
            ctype = "application/manifest+json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache" if target.name in {"index.html", "app.js"} else "public, max-age=3600")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path.startswith("/api/"):
            if path != "/api/health" and not self._authorized():
                self._send_json({"error": "unauthorized"}, 401)
                return
            try:
                store: DashboardStore = self.app["store"]
                if path == "/api/health":
                    self._send_json({"ok": True, "time": datetime.now(store.tz).isoformat(timespec="seconds"), "mode": "FREE"})
                elif path == "/api/dashboard":
                    self._send_json(store.dashboard())
                elif path == "/api/recommendations":
                    self._send_json({"items": store.recommendations()})
                elif path == "/api/us/recommendations":
                    self._send_json({"trade_date": store.us_today(), "items": store.us_recommendations(), "learning": store.us_learning_summary()})
                elif path == "/api/global":
                    self._send_json(store.global_macro())
                elif path == "/api/reports":
                    self._send_json(store.reports_summary())
                elif path == "/api/learning":
                    self._send_json(store.learning_summary())
                elif path == "/api/history":
                    q = parse_qs(parsed.query)
                    days = max(1, min(120, int((q.get("days") or ["10"])[0])))
                    self._send_json({"items": store.history(days)})
                elif path == "/api/settings":
                    self._send_json({
                        "monitoring": store.settings.get("monitoring", {}),
                        "risk": store.settings.get("risk", {}),
                        "positions": store.settings.get("existing_positions", []),
                    })
                else:
                    self._send_json({"error": "not_found"}, 404)
            except Exception as exc:
                log.exception("web api failed %s", path)
                self._send_json({"error": str(exc)}, 500)
            return
        rel = path.lstrip("/") or "index.html"
        self._serve_static(rel)


def run_web_server(settings: Settings) -> None:
    host = os.getenv("APP_HOST", str(settings.get("monitoring.web_host", "0.0.0.0")))
    port = int(os.getenv("APP_PORT", str(settings.get("monitoring.web_port", 8080))))
    token = os.getenv("APP_API_TOKEN", "").strip()
    web_root = settings.root / "web"
    store = DashboardStore(settings)
    httpd = ThreadingHTTPServer((host, port), AppHandler)
    httpd.app_context = {"store": store, "token": token, "web_root": web_root}  # type: ignore[attr-defined]
    log.info("Mobile web app: http://%s:%s  token=%s", host, port, "enabled" if token else "disabled")
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
