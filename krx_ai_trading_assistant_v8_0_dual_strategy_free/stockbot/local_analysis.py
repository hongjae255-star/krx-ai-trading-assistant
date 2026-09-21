from __future__ import annotations

import logging
import math
import re
from typing import Any

from .config import Settings
from .free_news import NaverFinanceNewsClient

log = logging.getLogger(__name__)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def _text(*parts: Any) -> str:
    return " ".join(str(x or "") for x in parts).lower()


# High-information corporate events receive more weight than generic optimistic words.
POSITIVE_EVENTS: dict[str, float] = {
    "단일판매": 0.16, "공급계약": 0.16, "수주": 0.14, "대규모 계약": 0.14,
    "자사주 취득": 0.12, "자기주식 취득": 0.12, "배당 확대": 0.08,
    "허가": 0.10, "승인": 0.10, "특허취득": 0.06, "흑자전환": 0.12,
    "사상 최대": 0.10, "최대 실적": 0.10, "어닝 서프라이즈": 0.12,
    "영업이익 증가": 0.08, "매출액 증가": 0.06, "목표가 상향": 0.08,
    "추정치 상향": 0.09, "실적 상향": 0.09, "턴어라운드": 0.08,
}

NEGATIVE_EVENTS: dict[str, float] = {
    "유상증자": 0.22, "전환사채": 0.18, "신주인수권": 0.18, "교환사채": 0.15,
    "거래정지": 0.25, "상장폐지": 0.30, "불성실공시": 0.18, "감사의견": 0.18,
    "횡령": 0.28, "배임": 0.25, "소송": 0.12, "적자전환": 0.16,
    "실적 쇼크": 0.14, "어닝 쇼크": 0.14, "목표가 하향": 0.10,
    "추정치 하향": 0.11, "실적 하향": 0.11, "수요 둔화": 0.08,
    "마진 악화": 0.08, "대규모 매도": 0.10,
}

SOFT_POSITIVE = ["호조", "개선", "성장", "회복", "수혜", "긍정", "강세", "기대", "매수", "buy", "최선호"]
SOFT_NEGATIVE = ["부진", "감소", "우려", "둔화", "부담", "약세", "매도", "sell", "중립", "하회"]
EVIDENCE_WORDS = ["매출", "영업이익", "순이익", "수주", "계약", "목표가", "추정", "컨센서스", "가동률", "점유율", "증설", "%", "억원", "조원"]
BINARY_RISK_WORDS = ["임상", "fda", "허가결정", "소송 결과", "판결", "상장적격성", "감사의견"]


class LocalAnalyst:
    """Zero-cost local event/research analyzer.

    This class deliberately contains no OpenAI client and makes no paid AI calls. It
    converts public disclosures, public news headlines and brokerage-report metadata
    into reproducible numeric features. The downstream scikit-learn ensemble learns
    whether those features have actually been useful in the user's accumulated data.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.news = NaverFinanceNewsClient(settings)
        self.mode = "FREE_LOCAL_RULE_ML"

    @property
    def configured(self) -> bool:
        return bool(self.settings.get("free_mode", True)) and bool(self.settings.get("local_engine.enabled", True))

    @property
    def uses_openai(self) -> bool:
        return False

    def ping(self) -> str:
        return "LOCAL RULE/ML ENGINE OK"

    @staticmethod
    def _event_score(text: str) -> tuple[float, float, list[str]]:
        pos = 0.0
        neg = 0.0
        tags: list[str] = []
        for key, w in POSITIVE_EVENTS.items():
            if key in text:
                pos += w
                tags.append(f"pos:{key}")
        for key, w in NEGATIVE_EVENTS.items():
            if key in text:
                neg += w
                tags.append(f"neg:{key}")
        pos += min(0.12, 0.025 * sum(1 for k in SOFT_POSITIVE if k in text))
        neg += min(0.12, 0.030 * sum(1 for k in SOFT_NEGATIVE if k in text))
        return _clamp(pos), _clamp(neg), tags

    @staticmethod
    def _conviction(text: str) -> float:
        evidence = sum(1 for k in EVIDENCE_WORDS if k in text)
        number_count = len(re.findall(r"\d+(?:\.\d+)?(?:%|억|조|원)?", text))
        v = 0.34 + min(0.36, evidence * 0.055) + min(0.22, number_count * 0.025)
        if any(k in text for k in BINARY_RISK_WORDS):
            v -= 0.08
        return _clamp(v, 0.25, 0.92)

    def analyze_broker_reports(self, reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for r in reports:
            text = _text(r.get("title"), r.get("summary"), r.get("opinion"))
            pos, neg, _ = self._event_score(text)
            soft = 0.035 * sum(1 for k in SOFT_POSITIVE if k in text) - 0.040 * sum(1 for k in SOFT_NEGATIVE if k in text)
            sentiment = max(-1.0, min(1.0, (pos - neg) * 1.65 + soft))

            revision = 0.0
            if any(k in text for k in ["추정치 상향", "실적 상향", "영업이익 상향", "eps 상향", "목표가 상향"]):
                revision += 0.72
            if any(k in text for k in ["추정치 하향", "실적 하향", "영업이익 하향", "eps 하향", "목표가 하향"]):
                revision -= 0.78
            if "상향" in text and revision == 0:
                revision = 0.40
            if "하향" in text and revision == 0:
                revision = -0.45
            revision = max(-1.0, min(1.0, revision))

            # A standing BUY is weak evidence unless accompanied by a fresh revision/event.
            if ("buy" in text or "매수" in text) and abs(revision) < 0.1 and pos < 0.08:
                sentiment = min(sentiment, 0.28)

            out.append({
                "report_id": str(r.get("report_id", "")),
                "sentiment": sentiment,
                "conviction": self._conviction(text),
                "estimate_revision": revision,
            })
        return out

    def enrich(
        self,
        candidates: list[dict[str, Any]],
        disclosures: dict[str, list[dict[str, Any]]],
        broker_reports: dict[str, list[dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        if not candidates:
            return {
                "market_regime": "local_numeric",
                "global_risk_on": 0.5,
                "macro_event_risk": 0.5,
                "market_summary": "무료 로컬 분석: 후보 없음",
                "evaluations": [],
            }
        broker_reports = broker_reports or {}
        evaluations: list[dict[str, Any]] = []
        total_news = total_disclosures = total_reports = 0

        for c in candidates:
            code = str(c.get("code", ""))
            ds = disclosures.get(code, [])[:10]
            rs = broker_reports.get(code, [])[:12]
            headlines = self.news.fetch(code)
            total_news += len(headlines)
            total_disclosures += len(ds)
            total_reports += len(rs)

            dtext = _text(*(x.get("report_nm", "") for x in ds))
            ntext = _text(*(x.get("title", "") for x in headlines))
            rtext = _text(*(f"{x.get('title','')} {x.get('summary','')} {x.get('opinion','')}" for x in rs))
            dpos, dneg, dtags = self._event_score(dtext)
            npos, nneg, ntags = self._event_score(ntext)
            rpos, rneg, rtags = self._event_score(rtext)

            # DART is primary evidence; public headlines are secondary; reports are slower-moving.
            # Individual keyword weights are deliberately modest. Rescale the evidence
            # mixture so one high-information event (e.g. CB issuance or a major supply
            # contract) becomes a meaningful model feature instead of being washed out.
            positive = _clamp((0.52 * dpos + 0.30 * npos + 0.18 * rpos) * 3.0)
            negative = _clamp((0.56 * dneg + 0.28 * nneg + 0.16 * rneg) * 3.5)

            report_sentiment = 0.0
            if rs:
                wsum = 0.0
                ssum = 0.0
                for r in rs:
                    conv = _clamp(float(r.get("conviction") or 0.5), 0.1, 1.0)
                    ssum += conv * max(-1.0, min(1.0, float(r.get("sentiment") or 0.0)))
                    wsum += conv
                report_sentiment = ssum / wsum if wsum else 0.0

            catalyst = 0.5 + 0.42 * positive - 0.52 * negative + 0.08 * report_sentiment
            catalyst = _clamp(catalyst)
            risk_flags: list[str] = []
            all_text = f"{dtext} {ntext} {rtext}"
            for key in ["유상증자", "전환사채", "신주인수권", "거래정지", "상장폐지", "횡령", "배임", "감사의견"]:
                if key in all_text:
                    risk_flags.append(f"event:{key}")
            if any(k in all_text for k in BINARY_RISK_WORDS):
                risk_flags.append("binary_event_risk")
            if float(c.get("change_pct") or 0.0) >= 10.0:
                risk_flags.append("intraday_overheat_risk")

            key_titles = [x.get("report_nm", "") for x in ds[:2]] + [x.get("title", "") for x in headlines[:2]]
            key_titles = [x for x in key_titles if x]
            summary = " | ".join(key_titles[:3]) if key_titles else "신규 공시/무료 뉴스 촉매 제한적"
            evaluations.append({
                "code": code,
                "news_catalyst": catalyst,
                "event_positive_strength": positive,
                "event_risk_inverse": 1.0 - negative,
                "summary": summary[:500],
                "risk_flags": list(dict.fromkeys(risk_flags)),
                "event_tags": list(dict.fromkeys((dtags + ntags + rtags)))[:12],
                "public_news_count": len(headlines),
            })

        macro_default = _clamp(float(self.settings.get("local_engine.macro_event_risk_default", 0.5)))
        risk_default = _clamp(float(self.settings.get("local_engine.global_risk_on_default", 0.5)))
        summary = (
            f"무료 로컬 엔진: DART {total_disclosures}건, 공개 뉴스 제목 {total_news}건, "
            f"증권사 리포트 {total_reports}건을 규칙/통계로 평가. "
            "유료 LLM 호출 없음. 글로벌 매크로는 별도 유료 피드 없이 중립 기본값을 사용하므로 "
            "국내 수급·가격·공시·리포트 신호의 비중이 더 큽니다."
        )
        return {
            "market_regime": "local_numeric",
            "global_risk_on": risk_default,
            "macro_event_risk": macro_default,
            "market_summary": summary,
            "evaluations": evaluations,
        }
