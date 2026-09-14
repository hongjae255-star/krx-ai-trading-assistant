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

    @staticmethod
    def _flag_label(flag: str) -> str:
        labels = {
            "market_risk_off": "시장 Risk-off",
            "macro_event_risk": "매크로 이벤트 위험",
            "model_low_edge": "ML 기대우위 부족",
            "wide_downside_interval": "하방 예측구간 넓음",
            "overheated": "단기 과열",
            "high_volatility": "변동성 과다",
        }
        return labels.get(str(flag), str(flag).replace("_", " "))

    def _premarket_diagnostics(self, db: Database, trade_date: str, market: str, recs: list[dict]) -> dict:
        rows = db.get_candidate_snapshots(trade_date)
        if not rows:
            return {
                "market": market, "trade_date": trade_date, "phase": "premarket",
                "data_status": "not_scanned", "evaluated_count": 0, "top": [],
                "summary": "아직 후보 스캔 기록이 없습니다.",
            }
        selected = {str(x.get("code", "")) for x in recs}
        first = rows[0]
        if market == "KR":
            threshold = float(self.settings.get("prediction.minimum_final_score", 60.0))
            regime = str((first.get("prediction") or {}).get("regime", "unknown"))
            if regime.startswith("risk_off"):
                threshold += float(self.settings.get("prediction.risk_off_score_add", 4.0))
            elif "high_vol" in regime:
                threshold += float(self.settings.get("prediction.high_vol_score_add", 2.0))
            event_safety = _safe_float((first.get("features") or {}).get("macro_event_safety"), 0.5)
            if event_safety < 0.30:
                threshold += float(self.settings.get("prediction.high_event_risk_score_add", 2.0))
            active_ml = any(bool((x.get("prediction") or {}).get("active")) for x in rows)
            min_prob = float(self.settings.get("prediction.min_up_probability", 0.56))
            if regime.startswith("risk_off"):
                min_prob += float(self.settings.get("prediction.risk_off_probability_add", 0.04))
            slots = int(self.settings.get("market.final_pick_count", 3))
        else:
            threshold = float(self.settings.get("us_market.minimum_final_score", 58.0))
            regime = str((first.get("prediction") or {}).get("regime", "unknown"))
            active_ml = False
            min_prob = 0.0
            slots = int(self.settings.get("us_market.market.final_pick_count", 3))

        top = []
        for x in rows:
            if str(x.get("code", "")) in selected:
                continue
            pred = x.get("prediction") or {}
            score = _safe_float(x.get("final_score"))
            reasons = []
            if score < threshold:
                reasons.append(f"최종점수 {score:.1f} < 기준 {threshold:.1f}")
            if market == "KR" and active_ml and bool(pred.get("active")):
                prob = _safe_float(pred.get("up_probability"))
                if prob < min_prob:
                    reasons.append(f"상승확률 {100*prob:.1f}% < 기준 {100*min_prob:.1f}%")
            if not reasons and len(selected) >= slots:
                reasons.append(f"선발 슬롯 {slots}개 밖")
            flags = list(x.get("risk_flags") or [])
            top.append({
                "code": x.get("code"), "name": x.get("name"), "price": x.get("price"),
                "score": round(score, 2), "required_score": round(threshold, 2),
                "gap": round(score-threshold, 2),
                "up_probability": (round(100*_safe_float(pred.get("up_probability")), 1) if pred.get("active") else None),
                "expected_return_pct": (round(_safe_float(pred.get("expected_return_pct")), 2) if pred.get("active") else None),
                "risk_flags": flags,
                "reasons": reasons + [self._flag_label(f) for f in flags[:2]],
            })
        top.sort(key=lambda z: _safe_float(z.get("score")), reverse=True)
        best = top[0] if top else None
        return {
            "market": market, "trade_date": trade_date, "phase": "premarket",
            "data_status": "ok", "evaluated_count": len(rows), "selected_count": len(recs),
            "required_score": round(threshold, 2), "required_probability_pct": round(100*min_prob, 1) if active_ml else None,
            "regime": regime, "top": top[:5],
            "summary": (f"최고 탈락후보 {best['score']:.1f} / 기준 {threshold:.1f}" if best else "기준 통과 후보만 선발됨"),
        }

    def candidate_diagnostics(self, market: str, trade_date: str, recs: list[dict]) -> dict:
        db = self.db if market == "KR" else self.us_db
        key = "last_replacement_scan" if market == "KR" else "us_last_replacement_scan"
        scan = db.get_state(key, {}) or {}
        if str(scan.get("trade_date", "")) == trade_date and scan.get("phase") == "intraday":
            top = list(scan.get("top") or [])
            for x in top:
                x["reasons"] = [self._flag_label(r.replace("risk: ", "")) if str(r).startswith("risk: ") else r for r in (x.get("reasons") or [])]
            return {**scan, "market": market, "top": top}
        return self._premarket_diagnostics(db, trade_date, market, recs)

    def data_health(self, market_pulse: dict, kr_diag: dict, us_diag: dict) -> dict:
        macro = self.global_macro()
        macro_failures = len(macro.get("failures") or [])
        macro_stale = int(macro.get("stale_series_count") or 0)
        macro_transport = int(macro.get("transport_failure_count") or len(macro.get("transport_failures") or []))
        macro_key_status = str(macro.get("api_key_status") or "unknown")
        live = macro.get("live") or {}
        live_failures = len(live.get("failures") or [])
        index_failures = len(market_pulse.get("failures") or [])
        index_stale = int(market_pulse.get("stale_count") or 0)
        degraded = macro_failures + macro_stale + live_failures + index_failures + index_stale
        if macro_key_status in {"missing", "invalid"}: degraded += 1
        if kr_diag.get("data_status") not in {"ok", "not_scanned"}: degraded += 1
        if us_diag.get("data_status") not in {"ok", "not_scanned"}: degraded += 1
        pulse_waiting = not bool((market_pulse.get("indexes") or {}))
        return {
            "status": "waiting" if pulse_waiting else ("good" if degraded == 0 else "degraded"),
            "macro_failures": macro_failures, "macro_stale": macro_stale,
            "macro_transport_failures": macro_transport, "macro_api_key_status": macro_key_status,
            "live_proxy_failures": live_failures, "index_failures": index_failures,
            "index_stale": index_stale, "kr_scan": kr_diag.get("data_status", "unknown"),
            "us_scan": us_diag.get("data_status", "unknown"),
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
        us_trade_date = self.us_today()
        recs = self.recommendations(trade_date)
        us_recs = self.us_recommendations(us_trade_date)
        reports = self.reports_summary(trade_date)
        learning = self.learning_summary()
        last_update = None
        for r in recs:
            ts = (r.get("live") or {}).get("ts")
            if ts and (last_update is None or str(ts) > str(last_update)):
                last_update = ts
        replacement = self.db.get_state("last_replacement_candidate", None)
        market_pulse = self.db.get_state("market_pulse_latest", {}) or {}
        kr_diag = self.candidate_diagnostics("KR", trade_date, recs)
        us_diag = self.candidate_diagnostics("US", us_trade_date, us_recs)
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
            "candidate_diagnostics": kr_diag,
            "reports": reports,
            "learning": learning,
            "positions": self.settings.get("existing_positions", []),
            "risk": self.settings.get("risk", {}),
            "global_macro": self.global_macro(),
            "market_pulse": market_pulse,
            "data_health": self.data_health(market_pulse, kr_diag, us_diag),
            "us": {
                "trade_date": us_trade_date,
                "recommendations": us_recs,
                "candidate_diagnostics": us_diag,
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
