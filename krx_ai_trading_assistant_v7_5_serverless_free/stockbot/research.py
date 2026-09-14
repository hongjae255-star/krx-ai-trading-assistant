from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import Settings
from .db import Database

log = logging.getLogger(__name__)


@dataclass
class BrokerReport:
    report_id: str
    report_date: str
    code: str
    name: str
    title: str
    broker: str
    source: str = "naver_finance"
    source_url: str = ""
    target_price: float | None = None
    opinion: str = ""
    summary: str = ""
    sentiment: float = 0.0          # -1 ~ +1
    conviction: float = 0.5         # 0 ~ 1
    estimate_revision: float = 0.0  # -1 ~ +1
    analyzed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _float_price(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"([0-9][0-9,]*)", text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except Exception:
        return None


def _report_date(text: str) -> str | None:
    text = text.strip()
    for fmt in ["%y.%m.%d", "%Y.%m.%d", "%Y-%m-%d"]:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except Exception:
            pass
    return None


def _code_from_href(href: str) -> str:
    if not href:
        return ""
    qs = parse_qs(urlparse(href).query)
    for key in ["code", "itemcode"]:
        v = qs.get(key, [""])[0]
        if re.fullmatch(r"\d{6}", v):
            return v
    m = re.search(r"(?:code|itemcode)=(\d{6})", href)
    return m.group(1) if m else ""


class NaverResearchClient:
    """Fetches Npay Finance *company report index/detail pages* for personal analysis.

    It intentionally does not download or redistribute attached PDFs. The adapter stores
    only index metadata and the short text displayed on the report detail page.
    """

    BASE = "https://finance.naver.com"
    LIST_URL = BASE + "/research/company_list.naver"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.timeout = int(settings.get("research.http_timeout_seconds", 12))
        self.delay = float(settings.get("research.request_delay_seconds", 0.15))
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KRX-AI-Trading-Assistant/2.0",
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.6",
        })

    def _get(self, url: str, params: dict[str, Any] | None = None) -> BeautifulSoup:
        r = self.session.get(url, params=params, timeout=self.timeout)
        r.raise_for_status()
        # Legacy Naver Finance pages can be EUC-KR even when requests guesses ISO-8859-1.
        if not r.encoding or r.encoding.lower() in {"iso-8859-1", "ascii"}:
            r.encoding = r.apparent_encoding or "euc-kr"
        time.sleep(max(0.0, self.delay))
        return BeautifulSoup(r.text, "html.parser")

    def fetch_recent(self, target_date: str | None = None, fetch_details: bool | None = None) -> list[BrokerReport]:
        target = date.fromisoformat(target_date) if target_date else date.today()
        lookback = int(self.settings.get("research.calendar_lookback_days", 1))
        oldest = target - timedelta(days=max(0, lookback - 1))
        max_pages = int(self.settings.get("research.max_pages", 30))
        if fetch_details is None:
            fetch_details = bool(self.settings.get("research.fetch_detail_pages", True))
        reports: list[BrokerReport] = []
        seen: set[str] = set()
        reached_old = False

        for page in range(1, max_pages + 1):
            try:
                soup = self._get(self.LIST_URL, {"page": page})
            except Exception as exc:
                log.warning("Naver research list failed page=%s: %s", page, exc)
                break

            page_dates: list[date] = []
            found_any = False
            for tr in soup.find_all("tr"):
                anchors = tr.find_all("a", href=True)
                if not anchors:
                    continue
                title_a = next((a for a in anchors if "company_read.naver" in a.get("href", "")), None)
                code_a = next((a for a in anchors if _code_from_href(a.get("href", ""))), None)
                if not title_a or not code_a:
                    continue

                cells = [x.get_text(" ", strip=True) for x in tr.find_all("td")]
                rdate = next((_report_date(x) for x in reversed(cells) if _report_date(x)), None)
                if not rdate:
                    continue
                d = date.fromisoformat(rdate)
                page_dates.append(d)
                if d > target or d < oldest:
                    continue

                href = urljoin(self.BASE, title_a.get("href", ""))
                nid = parse_qs(urlparse(href).query).get("nid", [""])[0]
                report_id = f"naver:{nid or href}"
                if report_id in seen:
                    continue
                seen.add(report_id)
                found_any = True
                code = _code_from_href(code_a.get("href", ""))
                name = code_a.get_text(" ", strip=True)
                title = title_a.get_text(" ", strip=True)

                broker = ""
                # Broker is usually the cell between title and attachment/date columns.
                for c in cells:
                    if c in {name, title} or _report_date(c) or re.fullmatch(r"[0-9,]+", c or ""):
                        continue
                    if c and len(c) <= 30 and ("증권" in c or "IR협의회" in c or "리서치" in c):
                        broker = c
                        break
                if not broker and len(cells) >= 3:
                    broker = cells[2]

                report = BrokerReport(
                    report_id=report_id, report_date=rdate, code=code, name=name,
                    title=title, broker=broker, source_url=href,
                )
                if fetch_details:
                    try:
                        self.fill_detail(report)
                    except Exception as exc:
                        log.debug("Naver report detail failed %s: %s", report_id, exc)
                reports.append(report)

            if page_dates and min(page_dates) < oldest:
                reached_old = True
            if reached_old:
                break
            if not found_any and page > 3:
                # Do not crawl dozens of empty/irrelevant pages when site markup changes.
                break

        return reports

    def fill_detail(self, report: BrokerReport) -> None:
        soup = self._get(report.source_url)
        text = soup.get_text(" ", strip=True)
        m = re.search(r"목표가\s*([0-9][0-9,]*)", text)
        if m:
            report.target_price = _float_price(m.group(1))
        m = re.search(r"투자의견\s*([^\s|]+)", text)
        if m:
            report.opinion = m.group(1).strip()

        # The short publisher-provided abstract generally sits in the same container as PDF link.
        pdf = soup.find("a", href=re.compile(r"\.pdf(?:$|\?)", re.I))
        summary = ""
        if pdf is not None:
            container = pdf.parent
            if container is not None:
                summary = container.get_text(" ", strip=True)
        if not summary:
            # Fallback: take text between report title and standard disclaimer.
            start = text.find(report.title)
            end = text.find("보고서의 내용은 투자판단", start + 1 if start >= 0 else 0)
            if start >= 0:
                summary = text[start:end if end > start else start + 1800]
        summary = re.sub(r"\s+", " ", summary).strip()
        if report.title and summary.startswith(report.title):
            summary = summary[len(report.title):].strip()
        report.summary = summary[:1800]


class ResearchManager:
    def __init__(self, settings: Settings, db: Database, analyst: Any):
        self.settings = settings
        self.db = db
        self.analyst = analyst
        self.client = NaverResearchClient(settings)

    def collect_and_analyze(self, target_date: str) -> list[dict[str, Any]]:
        if not bool(self.settings.get("research.enabled", True)):
            return []
        lookback = int(self.settings.get("research.calendar_lookback_days", 4))
        start_date = (date.fromisoformat(target_date) - timedelta(days=max(0, lookback - 1))).isoformat()
        existing = {r["report_id"]: r for r in self.db.get_broker_reports_between(start_date, target_date)}
        fetched: list[BrokerReport] = []
        try:
            # First crawl only the index. Fetch detail pages only for genuinely new/missing rows.
            fetched = self.client.fetch_recent(target_date, fetch_details=False)
        except Exception as exc:
            log.warning("broker research collection failed: %s", exc)

        want_details = bool(self.settings.get("research.fetch_detail_pages", True))
        for r in fetched:
            old = existing.get(r.report_id)
            if want_details and (old is None or not str(old.get("summary") or "")):
                try:
                    self.client.fill_detail(r)
                except Exception as exc:
                    log.debug("broker report detail fill failed %s: %s", r.report_id, exc)
            self.db.save_broker_report(r.to_dict())

        rows = self.db.get_broker_reports_between(start_date, target_date)
        pending = [r for r in rows if not int(r.get("analyzed") or 0)]
        if pending:
            batch_size = int(self.settings.get("research.analysis_batch_size", 35))
            for i in range(0, len(pending), max(1, batch_size)):
                batch = pending[i:i + batch_size]
                results = self.analyst.analyze_broker_reports(batch)
                by_id = {str(x.get("report_id")): x for x in results}
                for r in batch:
                    ev = by_id.get(str(r["report_id"]), self.rule_based_analysis(r))
                    self.db.update_broker_report_analysis(
                        str(r["report_id"]),
                        float(ev.get("sentiment", 0.0)),
                        float(ev.get("conviction", 0.5)),
                        float(ev.get("estimate_revision", 0.0)),
                    )
        return self.db.get_broker_reports_between(start_date, target_date)

    @staticmethod
    def rule_based_analysis(r: dict[str, Any]) -> dict[str, Any]:
        text = f"{r.get('title','')} {r.get('summary','')} {r.get('opinion','')}".lower()
        pos = ["상향", "호조", "성장", "개선", "수혜", "buy", "매수", "긍정", "최대", "회복", "기대"]
        neg = ["하향", "부진", "감소", "우려", "sell", "매도", "중립", "둔화", "적자", "부담"]
        s = 0.0
        for k in pos:
            if k in text:
                s += 0.16
        for k in neg:
            if k in text:
                s -= 0.18
        rev = 0.0
        if any(k in text for k in ["추정치 상향", "실적 상향", "목표가 상향"]):
            rev = 0.7
        elif any(k in text for k in ["추정치 하향", "실적 하향", "목표가 하향"]):
            rev = -0.7
        return {
            "report_id": str(r.get("report_id", "")),
            "sentiment": max(-1.0, min(1.0, s)),
            "conviction": 0.55 if abs(s) > 0.1 else 0.4,
            "estimate_revision": rev,
        }

    def stock_signals(self, reports: list[dict[str, Any]], prices: dict[str, float] | None = None) -> dict[str, dict[str, Any]]:
        prices = prices or {}
        broker_rel = self.db.get_state("broker_reliability", {}) or {}
        grouped: dict[str, list[dict[str, Any]]] = {}
        for r in reports:
            code = str(r.get("code", ""))
            if code:
                grouped.setdefault(code, []).append(r)

        result: dict[str, dict[str, Any]] = {}
        for code, rs in grouped.items():
            num = 0.0
            den = 0.0
            revision_num = 0.0
            target_num = 0.0
            target_den = 0.0
            brokers = set()
            for r in rs:
                broker = str(r.get("broker") or "unknown")
                brokers.add(broker)
                reliability = float(broker_rel.get(broker, 0.5))
                # reliability 0.5 is neutral; keep even unproven brokers meaningful.
                rw = 0.65 + 0.70 * reliability
                conviction = max(0.1, min(1.0, float(r.get("conviction") or 0.5)))
                rdate = str(r.get("report_date") or "")
                try:
                    age_days = max(0, (datetime.now(ZoneInfo(self.settings.timezone)).date() - date.fromisoformat(rdate)).days)
                except Exception:
                    age_days = 0
                recency = math.exp(-0.35 * age_days)
                w = rw * conviction * recency
                s = max(-1.0, min(1.0, float(r.get("sentiment") or 0.0)))
                rev = max(-1.0, min(1.0, float(r.get("estimate_revision") or 0.0)))
                num += w * s
                revision_num += w * rev
                den += w
                tp = float(r.get("target_price") or 0.0)
                price = float(prices.get(code) or 0.0)
                if tp > 0 and price > 0:
                    upside = max(-0.5, min(0.8, tp / price - 1.0))
                    target_num += w * upside
                    target_den += w
            sentiment = num / den if den else 0.0
            revision = revision_num / den if den else 0.0
            target_upside = target_num / target_den if target_den else 0.0
            coverage_bonus = min(0.10, math.log1p(len(rs)) * 0.035)
            signal = 0.5 + 0.27 * sentiment + 0.10 * revision + 0.13 * max(-0.5, min(0.5, target_upside)) + coverage_bonus
            signal = max(0.0, min(1.0, signal))
            result[code] = {
                "signal": signal,
                "report_count": len(rs),
                "broker_count": len(brokers),
                "sentiment": sentiment,
                "revision": revision,
                "target_upside": target_upside,
                "reports": rs,
            }
        return result

    def update_broker_reliability(self, trade_date: str, code_returns: dict[str, float]) -> dict[str, float]:
        """Update broker usefulness slowly using same-day open->close reaction.

        This is deliberately a weak adaptive signal, not a claim that a single day's move
        validates a fundamental report. It only tunes the *short-term trading* usefulness.
        """
        reports = self.db.get_broker_reports(trade_date)
        if not reports:
            return self.db.get_state("broker_reliability", {}) or {}
        rel = self.db.get_state("broker_reliability", {}) or {}
        counts = self.db.get_state("broker_reliability_counts", {}) or {}
        lr = float(self.settings.get("research.broker_learning_rate", 0.03))
        target = float(self.settings.get("learning.reward_target_return_pct", 4.0))
        for r in reports:
            code = str(r.get("code", ""))
            if code not in code_returns:
                continue
            broker = str(r.get("broker") or "unknown")
            sentiment = float(r.get("sentiment") or 0.0)
            conviction = float(r.get("conviction") or 0.5)
            if abs(sentiment) < 0.05:
                continue
            signed_result = sentiment * float(code_returns[code]) / max(0.5, target)
            signed_result = max(-1.0, min(1.0, signed_result)) * max(0.2, conviction)
            old = float(rel.get(broker, 0.5))
            n = int(counts.get(broker, 0))
            # Smaller adaptation for low sample counts and strict bounds.
            step = lr * min(1.0, (n + 1) / 10.0) * signed_result
            rel[broker] = max(0.30, min(0.70, old + step))
            counts[broker] = n + 1
        self.db.set_state("broker_reliability", rel)
        self.db.set_state("broker_reliability_counts", counts)
        return rel
