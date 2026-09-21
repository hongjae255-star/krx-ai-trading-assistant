from __future__ import annotations

"""Public SEC Form 13F tracker for well-known U.S. investment managers.

13F is deliberately treated as *context*, not a trading trigger:
- filings are quarterly and may be filed up to 45 days after quarter-end;
- they cover only reportable 13(f) securities, not a manager's full portfolio;
- a filing does not reveal the exact trade date within the quarter.

The tracker therefore caps institutional influence inside the swing score and
surfaces filing/report dates in the dashboard.
"""

from datetime import datetime, timezone
import logging
import os
import re
import time
from typing import Any
from xml.etree import ElementTree as ET

import requests

from .db import Database

log = logging.getLogger(__name__)


def _norm_name(value: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]+", " ", str(value or "").upper())
    suffixes = {
        "INC", "INCORPORATED", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED",
        "PLC", "HOLDINGS", "HOLDING", "GROUP", "NV", "SA", "AG", "LP", "LLC", "CLASS", "CL",
    }
    parts = [p for p in s.split() if p not in suffixes and len(p) > 1]
    return " ".join(parts[:6])


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(node: ET.Element, name: str) -> str:
    for el in node.iter():
        if _local(el.tag) == name:
            return (el.text or "").strip()
    return ""


class InstitutionalTracker:
    def __init__(self, settings, db: Database):
        self.settings = settings
        self.db = db
        self.cfg = dict(settings.get("institutional_tracking", {}) or {})
        self.user_agent = os.getenv("SEC_USER_AGENT", "").strip()
        self.timeout = float(self.cfg.get("timeout_seconds", 12))
        self._session = requests.Session()
        if self.user_agent:
            self._session.headers.update({
                "User-Agent": self.user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json,text/xml,application/xml,text/html,*/*",
            })

    @property
    def configured(self) -> bool:
        return bool(self.cfg.get("enabled", True) and self.user_agent and "@" in self.user_agent)

    def _get_json(self, url: str) -> dict[str, Any]:
        r = self._session.get(url, timeout=self.timeout)
        r.raise_for_status()
        out = r.json()
        time.sleep(float(self.cfg.get("request_delay_seconds", 0.12)))
        return out if isinstance(out, dict) else {}

    def _get_text(self, url: str) -> str:
        r = self._session.get(url, timeout=self.timeout)
        r.raise_for_status()
        time.sleep(float(self.cfg.get("request_delay_seconds", 0.12)))
        return r.text

    @staticmethod
    def _recent_13f(sub: dict[str, Any]) -> list[dict[str, str]]:
        recent = ((sub.get("filings") or {}).get("recent") or {})
        forms = recent.get("form") or []
        accs = recent.get("accessionNumber") or []
        dates = recent.get("filingDate") or []
        reports = recent.get("reportDate") or []
        docs = recent.get("primaryDocument") or []
        out: list[dict[str, str]] = []
        for i, form in enumerate(forms):
            if str(form) != "13F-HR":
                continue
            out.append({
                "form": str(form), "accession": str(accs[i]) if i < len(accs) else "",
                "filing_date": str(dates[i]) if i < len(dates) else "",
                "report_date": str(reports[i]) if i < len(reports) else "",
                "primary_document": str(docs[i]) if i < len(docs) else "",
            })
            if len(out) >= 2:
                break
        return out

    def _information_table_url(self, cik: str, accession: str) -> str | None:
        cik_num = str(int(cik))
        acc = accession.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc}"
        try:
            idx = self._get_json(f"{base}/index.json")
            items = ((idx.get("directory") or {}).get("item") or [])
            xmls = []
            for it in items:
                name = str((it or {}).get("name", ""))
                low = name.lower()
                if low.endswith(".xml") and "primary" not in low and "xsl" not in low:
                    xmls.append(name)
            if xmls:
                # Most issuers call it infotable.xml; otherwise choose the largest-looking non-primary XML.
                xmls.sort(key=lambda n: ("info" not in n.lower(), n))
                return f"{base}/{xmls[0]}"
        except Exception as exc:
            log.warning("SEC filing index failed %s/%s: %s", cik, accession, exc)
        return None

    def _parse_table(self, xml_text: str) -> list[dict[str, Any]]:
        root = ET.fromstring(xml_text)
        rows: list[dict[str, Any]] = []
        # SEC XML commonly uses <infoTable>; namespace names differ across versions.
        nodes = [el for el in root.iter() if _local(el.tag).lower() == "infotable"]
        for node in nodes:
            issuer = _child_text(node, "nameOfIssuer")
            cusip = _child_text(node, "cusip")
            if not issuer or not cusip:
                continue
            def num(tag: str) -> float:
                try:
                    return float(_child_text(node, tag).replace(",", "") or 0)
                except Exception:
                    return 0.0
            rows.append({
                "issuer": issuer, "issuer_key": _norm_name(issuer), "title": _child_text(node, "titleOfClass"),
                "cusip": cusip, "reported_value": num("value"), "shares": num("sshPrnamt"),
                "share_type": _child_text(node, "sshPrnamtType"), "put_call": _child_text(node, "putCall"),
            })
        return rows

    def _filing_holdings(self, cik: str, filing: dict[str, str]) -> list[dict[str, Any]]:
        url = self._information_table_url(cik, filing.get("accession", ""))
        if not url:
            return []
        try:
            return self._parse_table(self._get_text(url))
        except Exception as exc:
            log.warning("SEC information table failed %s: %s", url, exc)
            return []

    @staticmethod
    def _changes(latest: list[dict[str, Any]], previous: list[dict[str, Any]]) -> list[dict[str, Any]]:
        def key(x: dict[str, Any]) -> tuple[str, str, str]:
            return (str(x.get("cusip", "")), str(x.get("title", "")), str(x.get("put_call", "")))
        a, b = {key(x): x for x in latest}, {key(x): x for x in previous}
        changes: list[dict[str, Any]] = []
        for k in set(a) | set(b):
            new, old = a.get(k), b.get(k)
            if new and not old:
                kind = "NEW"; delta = 1.0
            elif old and not new:
                kind = "EXIT"; delta = -1.0
            else:
                ns, os_ = float((new or {}).get("shares", 0) or 0), float((old or {}).get("shares", 0) or 0)
                ratio = (ns / os_ - 1.0) if os_ > 0 else 0.0
                if ratio > 0.02: kind = "ADD"
                elif ratio < -0.02: kind = "REDUCE"
                else: kind = "UNCHANGED"
                delta = ratio
            base = dict(new or old or {})
            base.update({"change": kind, "share_change_pct": round(100.0 * delta, 2)})
            changes.append(base)
        priority = {"NEW": 5, "ADD": 4, "EXIT": 3, "REDUCE": 2, "UNCHANGED": 1}
        changes.sort(key=lambda x: (priority.get(str(x.get("change")), 0), abs(float(x.get("share_change_pct", 0) or 0)), float(x.get("reported_value", 0) or 0)), reverse=True)
        return changes

    def _manager(self, manager: dict[str, Any]) -> dict[str, Any]:
        cik = str(manager.get("cik", "")).zfill(10)
        sub = self._get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
        filings = self._recent_13f(sub)
        if not filings:
            return {"name": manager.get("name"), "cik": cik, "status": "no_13f_hr", "holdings": [], "changes": []}
        latest = self._filing_holdings(cik, filings[0])
        previous = self._filing_holdings(cik, filings[1]) if len(filings) > 1 else []
        changes = self._changes(latest, previous)
        return {
            "name": manager.get("name"), "cik": cik, "status": "ok" if latest else "table_unavailable",
            "filing_date": filings[0].get("filing_date"), "report_date": filings[0].get("report_date"),
            "previous_report_date": filings[1].get("report_date") if len(filings) > 1 else None,
            "holdings_count": len(latest),
            "top_holdings": sorted(latest, key=lambda x: float(x.get("reported_value", 0) or 0), reverse=True)[:15],
            "changes": [x for x in changes if x.get("change") != "UNCHANGED"][:20],
        }

    def refresh(self, force: bool = False) -> dict[str, Any]:
        key = "institutional_13f_latest"
        prev = self.db.get_state(key, {}) or {}
        hours = float(self.cfg.get("cache_hours", 12))
        if not force and prev.get("generated_at"):
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(prev["generated_at"]).replace("Z", "+00:00"))).total_seconds() / 3600.0
                if age < hours:
                    return prev
            except Exception:
                pass
        if not self.configured:
            out = {
                "status": "setup_needed", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "message": "SEC_USER_AGENT secret을 '이름/앱명 contact@email' 형식으로 설정하세요.",
                "managers": prev.get("managers", []) if prev else [], "stale": bool(prev),
                "limitations": "13F는 분기말 후 최대 45일 지연될 수 있고 전체 포트폴리오를 뜻하지 않습니다.",
            }
            self.db.set_state(key, out); return out
        managers = []
        failures = []
        for m in self.cfg.get("managers", []) or []:
            try:
                managers.append(self._manager(dict(m)))
            except Exception as exc:
                log.warning("13F manager refresh failed %s: %s", m.get("name"), exc)
                failures.append({"name": m.get("name"), "error": str(exc)[:180]})
        if not managers and prev.get("managers"):
            out = dict(prev); out.update({"status": "stale", "stale": True, "failures": failures, "last_attempt_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            self.db.set_state(key, out); return out
        out = {
            "status": "ok" if not failures else "partial", "stale": False,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "managers": managers, "failures": failures,
            "limitations": "SEC 13F는 분기 보고 자료이며 최대 45일 지연되고 13(f) 대상 증권만 포함합니다. 실시간 매매내역이 아닙니다.",
        }
        self.db.set_state(key, out)
        return out

    def latest(self) -> dict[str, Any]:
        return self.db.get_state("institutional_13f_latest", {}) or {}

    def match(self, company_name: str) -> dict[str, Any]:
        state = self.latest()
        target = _norm_name(company_name)
        if len(target) < 3:
            return {"score": 0.5, "matches": []}
        matches = []
        for m in state.get("managers", []) or []:
            changes = list(m.get("changes", []) or [])
            holdings = list(m.get("top_holdings", []) or [])
            candidates = changes + holdings
            best = None
            for h in candidates:
                key = str(h.get("issuer_key") or _norm_name(h.get("issuer", "")))
                if not key:
                    continue
                # Conservative fuzzy-ish matching without external dependencies.
                common = set(target.split()) & set(key.split())
                if key in target or target in key or (len(common) >= 2):
                    best = h; break
            if best:
                kind = str(best.get("change", "HOLD"))
                local_score = {"NEW": 1.0, "ADD": 0.9, "HOLD": 0.68, "UNCHANGED": 0.68, "REDUCE": 0.35, "EXIT": 0.10}.get(kind, 0.65)
                matches.append({
                    "manager": m.get("name"), "issuer": best.get("issuer"), "change": kind,
                    "share_change_pct": best.get("share_change_pct"), "report_date": m.get("report_date"),
                    "filing_date": m.get("filing_date"), "score": local_score,
                })
        if not matches:
            return {"score": 0.5, "matches": []}
        # Multiple independent managers supporting the same company is mildly stronger,
        # but 13F remains a deliberately small contextual factor.
        avg = sum(float(x["score"]) for x in matches) / len(matches)
        consensus = min(0.08, 0.025 * max(0, len(matches) - 1))
        return {"score": min(1.0, avg + consensus), "matches": matches[:5]}


def _safe_ratio(value: Any, base: float) -> float:
    try:
        v = float(value or 0)
    except Exception:
        v = 0.0
    return v if base == 0 else v / base
