from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .local_analysis import LocalAnalyst
from .config import Settings
from .dart import DartClient
from .db import Database
from .features import compute_features, session_vwap, add_cross_sectional_features
from .kis import KISClient
from .global_macro import GlobalMacroEngine
from .learner import AdaptiveWeightLearner
from .models import Candidate, TradePlan
from .notifier import TelegramNotifier
from .planner import make_plan
from .predictor import PredictiveEnsemble
from .regime import infer_market_regime, attach_regime_features
from .risk import allocate_weights
from .research import ResearchManager
from .scoring import five_day_return, risk_adjusted_score, score

log = logging.getLogger(__name__)


def fnum(v: Any) -> float:
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return 0.0


def pick(d: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default


def rank_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": str(pick(row, "mksc_shrn_iscd", "stck_shrn_iscd", "stck_shrn_iscd", default="")).zfill(6),
        "name": str(pick(row, "hts_kor_isnm", default="")),
        "price": fnum(pick(row, "stck_prpr", default=0)),
        "change_pct": fnum(pick(row, "prdy_ctrt", default=0)),
        "turnover_krw": fnum(pick(row, "acml_tr_pbmn", default=0)),
        "volume": fnum(pick(row, "acml_vol", default=0)),
    }


def _dart_seed_score(disclosures: list[dict[str, Any]]) -> float:
    if not disclosures:
        return 0.5
    pos = ["공급계약", "단일판매", "수주", "취득", "승인", "허가", "영업이익", "매출액"]
    neg = ["유상증자", "전환사채", "불성실", "거래정지", "소송", "손상", "감사의견"]
    value = 0.5
    for x in disclosures[:8]:
        title = str(x.get("report_nm", ""))
        if any(k in title for k in pos):
            value += 0.06
        if any(k in title for k in neg):
            value -= 0.09
    return max(0.0, min(1.0, value))


class TradingAssistant:
    def __init__(self, settings: Settings, kis: KISClient | None = None, macro: GlobalMacroEngine | None = None):
        self.settings = settings
        self.cfg = settings.config
        self.db = Database(settings.path("storage.sqlite_path"))
        self.kis = kis or KISClient(settings)
        self.macro = macro or GlobalMacroEngine(settings, self.db, self.kis)
        self.dart = DartClient(settings)
        self.analyst = LocalAnalyst(settings)
        self.notifier = TelegramNotifier()
        self.learner = AdaptiveWeightLearner(self.db, self.cfg)
        self.research = ResearchManager(settings, self.db, self.analyst)
        self.predictor = PredictiveEnsemble(self.db, self.cfg, settings.path("storage.model_dir"))
        self._latest_reports: list[dict[str, Any]] = []

    def today(self) -> str:
        return datetime.now(ZoneInfo(self.settings.timezone)).date().isoformat()

    def _excluded(self, code: str, name: str) -> bool:
        mcfg = self.cfg.get("market", {})
        if code in set(str(x) for x in mcfg.get("exclude_codes", [])):
            return True
        up = name.upper()
        return any(str(x).upper() in up for x in mcfg.get("exclude_name_contains", []))

    def discover(self) -> tuple[list[Candidate], dict[str, pd.DataFrame], str]:
        weights = self.learner.current_weights()
        trade_date = self.today()
        try:
            reports = self.research.collect_and_analyze(trade_date)
        except Exception as exc:
            log.warning("research pipeline failed: %s", exc)
            reports = []
        self._latest_reports = reports
        report_by_code: dict[str, list[dict[str, Any]]] = {}
        for rr in reports:
            code = str(rr.get("code", ""))
            if code:
                report_by_code.setdefault(code, []).append(rr)
        mcfg = self.cfg.get("market", {})
        pool_size = int(mcfg.get("candidate_pool_size", 18))
        min_price = float(mcfg.get("min_price_krw", 5000))
        min_turnover = float(mcfg.get("min_turnover_krw", 10_000_000_000))

        source_rows: dict[str, dict[str, Any]] = {}
        ordered_codes: list[str] = []
        for fn in [self.kis.volume_rank, self.kis.fluctuation_rank]:
            try:
                rows = fn("0000")
            except Exception as exc:
                log.warning("ranking API failed: %s", exc)
                rows = []
            for raw in rows:
                r = rank_row(raw)
                code = r["code"]
                if not code.strip("0") or code in source_rows:
                    continue
                source_rows[code] = r
                ordered_codes.append(code)

        # Fresh brokerage reports can introduce a stock that is not yet in the top
        # turnover/fluctuation lists. Analyze all reports, but only inject the strongest
        # report-driven names into the trading candidate universe so market leaders are
        # not crowded out by a very large morning research batch.
        prelim_report_signals = self.research.stock_signals(reports)
        max_report_candidates = int(self.settings.get("research.max_report_candidates", 25))
        report_priority = sorted(
            report_by_code,
            key=lambda c: float(prelim_report_signals.get(c, {}).get("signal", 0.5)),
            reverse=True,
        )[:max_report_candidates]
        for code in report_priority:
            rs = report_by_code[code]
            if code not in source_rows:
                source_rows[code] = {
                    "code": code, "name": str(rs[0].get("name", code)), "price": 0.0,
                    "change_pct": 0.0, "turnover_krw": 0.0, "volume": 0.0,
                }
        report_set = set(report_priority)
        ordered_codes = report_priority + [c for c in ordered_codes if c not in report_set]

        foreign_map: dict[str, float] = {}
        inst_map: dict[str, float] = {}
        try:
            flow_rows = self.kis.foreign_institution_rank("0000", "0")
            for r in flow_rows:
                code = str(pick(r, "mksc_shrn_iscd", default="")).zfill(6)
                if code.strip("0"):
                    foreign_map[code] = fnum(r.get("frgn_ntby_tr_pbmn", 0))
                    inst_map[code] = fnum(r.get("orgn_ntby_tr_pbmn", 0))
                    if code not in source_rows:
                        source_rows[code] = rank_row(r)
                        ordered_codes.append(code)
        except Exception as exc:
            log.warning("flow rank failed: %s", exc)

        candidates: list[Candidate] = []
        daily_map: dict[str, pd.DataFrame] = {}
        for code in ordered_codes[: max(pool_size * 4, 40)]:
            seed = source_rows[code]
            try:
                q = self.kis.current_price(code)
            except Exception as exc:
                log.warning("quote failed %s: %s", code, exc)
                continue
            name = q.get("name") or seed.get("name") or code
            price = q.get("price") or seed.get("price") or 0
            turnover = q.get("turnover_krw") or seed.get("turnover_krw") or 0
            change = q.get("change_pct") if q.get("change_pct") not in (None, 0) else seed.get("change_pct", 0)
            if price < min_price or turnover < min_turnover or self._excluded(code, name):
                continue
            q["price"], q["turnover_krw"], q["change_pct"] = price, turnover, change
            try:
                daily = self.kis.daily_chart(code, 45)
            except Exception as exc:
                log.warning("daily chart failed %s: %s", code, exc)
                continue
            if daily.empty:
                continue
            report_signal = self.research.stock_signals(report_by_code.get(code, []), {code: float(price)}).get(code, {}).get("signal", 0.5)
            feats = compute_features(
                q, daily,
                foreign_net_krw=foreign_map.get(code, 0.0),
                institution_net_krw=inst_map.get(code, 0.0),
                news_catalyst=0.5,
                broker_report_signal=float(report_signal),
            )
            raw = score(feats, weights)
            five = five_day_return(daily)
            risk_flags = []
            if float(change) > float(mcfg.get("max_one_day_gain_pct", 15.0)):
                risk_flags.append("overheated")
            if five > float(mcfg.get("max_five_day_gain_pct", 30.0)):
                risk_flags.append("overheated")
            heuristic = risk_adjusted_score(raw, float(change), five, risk_flags)
            cand = Candidate(
                code=code, name=name, price=float(price), change_pct=float(change),
                turnover_krw=float(turnover), volume=float(q.get("volume") or seed.get("volume") or 0),
                foreign_net_krw=foreign_map.get(code, 0.0), institution_net_krw=inst_map.get(code, 0.0),
                features=feats, raw_score=raw, heuristic_score=heuristic,
                final_score=heuristic,
                risk_flags=risk_flags,
            )
            candidates.append(cand)
            daily_map[code] = daily
            if len(candidates) >= pool_size:
                break

        candidates.sort(key=lambda x: x.final_score, reverse=True)
        if not candidates:
            return [], daily_map, "후보 데이터 없음"

        # Only the strongest numeric shortlist incurs DART/public-news detail calls.
        shortlist_n = int(self.settings.get("local_engine.shortlist_size", 10))
        shortlist = candidates[:shortlist_n]
        disclosures: dict[str, list[dict[str, Any]]] = {}
        for c in shortlist:
            try:
                disclosures[c.code] = self.dart.recent_disclosures(c.code, int(self.settings.get("local_engine.max_event_age_days", 7)))
            except Exception as exc:
                log.warning("DART failed %s: %s", c.code, exc)
                disclosures[c.code] = []
            c.news_catalyst = _dart_seed_score(disclosures[c.code])

        ai_result = self.analyst.enrich([c.to_dict() for c in shortlist], disclosures, report_by_code)
        eval_map = {str(x.get("code")): x for x in ai_result.get("evaluations", [])}
        for c in shortlist:
            ev = eval_map.get(c.code, {})
            c.news_catalyst = float(ev.get("news_catalyst", c.news_catalyst))
            c.ai_summary = str(ev.get("summary", ""))
            event_positive = float(ev.get("event_positive_strength", 0.5))
            event_risk_inverse = float(ev.get("event_risk_inverse", 0.5))
            c.risk_flags = list(dict.fromkeys((c.risk_flags or []) + list(ev.get("risk_flags", []))))
            q = {
                "price": c.price, "change_pct": c.change_pct, "turnover_krw": c.turnover_krw,
                "volume": c.volume,
            }
            report_signal = self.research.stock_signals(report_by_code.get(c.code, []), {c.code: float(c.price)}).get(c.code, {}).get("signal", 0.5)
            c.features = compute_features(
                q, daily_map[c.code], c.foreign_net_krw, c.institution_net_krw,
                news_catalyst=c.news_catalyst, broker_report_signal=float(report_signal),
                event_positive_strength=event_positive, event_risk_inverse=event_risk_inverse,
            )
            c.raw_score = score(c.features, weights)
            c.heuristic_score = risk_adjusted_score(c.raw_score, c.change_pct, five_day_return(daily_map[c.code]), c.risk_flags)
            c.final_score = c.heuristic_score

        # Cross-sectional ranks and market regime use only information available before the open.
        add_cross_sectional_features(candidates)
        regime = infer_market_regime(candidates)
        try:
            macro_snapshot = self.macro.snapshot()
            macro_features = self.macro.equity_features("KR")
        except Exception as exc:
            log.warning("global macro snapshot failed: %s", exc)
            macro_snapshot = {"summary": "macro unavailable", "features": {}}
            macro_features = {"global_risk_on": 0.5, "macro_event_safety": 0.5}
        global_risk_on = max(0.0, min(1.0, float(macro_features.get("global_risk_on", 0.5))))
        macro_event_risk = 1.0 - max(0.0, min(1.0, float(macro_features.get("macro_event_safety", 0.5))))
        for c in candidates:
            c.features = attach_regime_features(dict(c.features or {}), regime)
            c.features.update(macro_features)
            c.features["heuristic_score_norm"] = max(0.0, min(1.0, c.heuristic_score / 100.0))
            if regime.name.startswith("risk_off"):
                c.risk_flags = list(dict.fromkeys((c.risk_flags or []) + ["market_risk_off"]))
            if macro_event_risk >= 0.70:
                c.risk_flags = list(dict.fromkeys((c.risk_flags or []) + ["macro_event_risk"]))

        # Train strictly on prior dates, then produce calibrated probability + expected return + interval.
        model_status = self.predictor.train(trade_date)
        pred_rows = [{"features": c.features, "heuristic_score": c.heuristic_score} for c in candidates]
        preds = self.predictor.predict(pred_rows, trade_date, regime.name)
        min_prob = float(self.settings.get("prediction.min_up_probability", 0.56))
        min_er = float(self.settings.get("prediction.min_expected_return_pct", 0.20))
        min_lower = float(self.settings.get("prediction.minimum_lower_bound_pct", -1.80))
        for c, pred in zip(candidates, preds):
            c.prediction = pred.to_dict()
            c.model_score = pred.model_score
            blend = pred.blend_weight if pred.active else 0.0
            blended = (1.0 - blend) * c.heuristic_score + blend * c.model_score
            if pred.active:
                if pred.up_probability < min_prob or pred.expected_return_pct < min_er:
                    c.risk_flags = list(dict.fromkeys((c.risk_flags or []) + ["model_low_edge"]))
                    blended -= 6.0
                if pred.lower_return_pct < min_lower:
                    c.risk_flags = list(dict.fromkeys((c.risk_flags or []) + ["wide_downside_interval"]))
                    blended -= 3.0
            c.final_score = risk_adjusted_score(blended, c.change_pct, five_day_return(daily_map[c.code]), c.risk_flags)

        candidates.sort(key=lambda x: x.final_score, reverse=True)
        report_stock_count = len(report_by_code)
        today_report_count = sum(1 for r in reports if str(r.get("report_date", "")) == trade_date)
        lookback = int(self.settings.get("research.calendar_lookback_days", 4))
        research_prefix = (
            f"오늘 기업리포트 {today_report_count}건, 최근 {lookback}일 유효 리포트 {len(reports)}건/{report_stock_count}종목 분석. "
            if reports else "최근 유효 증권사 기업리포트 없음. "
        )
        model_note = (
            f"수치 국면={regime.name}, 글로벌 risk-on={global_risk_on:.2f}, 매크로스트레스={macro_event_risk:.2f}, "
            f"예측모델={'활성' if model_status.get('active') else '보수적 fallback'}, ML 혼합비중={float(model_status.get('blend_weight', 0) or 0)*100:.0f}%. "
            f"[{macro_snapshot.get('summary', '')}] "
        )
        return candidates, daily_map, research_prefix + model_note + str(ai_result.get("market_summary", ""))

    def premarket(self) -> list[TradePlan]:
        trade_date = self.today()
        candidates, daily_map, market_summary = self.discover()
        if not candidates:
            self.notifier.send(f"[{trade_date} 08:30] 장전 분석 실패/후보 없음\n{market_summary}")
            return []
        # Save the full shadow candidate pool BEFORE final selection. These rows are
        # evaluated at the close even if the user never trades them, greatly increasing
        # clean supervised-learning samples. INSERT OR IGNORE prevents accidental
        # after-open reruns from contaminating the original morning snapshot.
        self.db.save_candidate_snapshots(trade_date, candidates)

        n = int(self.settings.get("market.final_pick_count", 3))
        min_final = float(self.settings.get("prediction.minimum_final_score", 60.0))
        allow_abstain = bool(self.settings.get("prediction.allow_abstain", True))
        numeric_regime = str((candidates[0].prediction or {}).get("regime", "unknown")) if candidates else "unknown"
        if numeric_regime.startswith("risk_off"):
            min_final += float(self.settings.get("prediction.risk_off_score_add", 4.0))
        elif "high_vol" in numeric_regime:
            min_final += float(self.settings.get("prediction.high_vol_score_add", 2.0))
        event_safety = float((candidates[0].features or {}).get("macro_event_safety", 0.5)) if candidates else 0.5
        if event_safety < 0.30:
            min_final += float(self.settings.get("prediction.high_event_risk_score_add", 2.0))
        eligible = [c for c in candidates if c.final_score >= min_final]
        if allow_abstain:
            active_ml = any(bool((c.prediction or {}).get("active")) for c in candidates)
            if active_ml:
                min_prob = float(self.settings.get("prediction.min_up_probability", 0.56))
                if numeric_regime.startswith("risk_off"):
                    min_prob += float(self.settings.get("prediction.risk_off_probability_add", 0.04))
                eligible = [c for c in eligible if float((c.prediction or {}).get("up_probability", 0.0)) >= min_prob]
        selected = eligible[:n] if eligible else ([] if allow_abstain else candidates[:n])
        plans = [make_plan(c, daily_map[c.code], self.cfg) for c in selected]
        plans = allocate_weights(plans, self.cfg)
        self.db.save_recommendations(trade_date, plans)
        report = {
            "trade_date": trade_date, "market_summary": market_summary,
            "broker_report_count": len(self._latest_reports),
            "broker_report_count_today": sum(1 for r in self._latest_reports if str(r.get("report_date", "")) == trade_date),
            "broker_reports": self._latest_reports,
            "model_status": self.predictor.status(),
            "shadow_candidate_count": len(candidates),
            "plans": [p.to_dict() for p in plans],
        }
        self._write_report(f"premarket_{trade_date}.json", report)
        if self._latest_reports:
            self._write_report(f"broker_research_{trade_date}.json", {"trade_date": trade_date, "reports": self._latest_reports})
        self.notifier.send(self.format_premarket(plans, market_summary))
        return plans

    def _write_report(self, name: str, obj: dict[str, Any]) -> None:
        out = self.settings.path("storage.output_dir") / name
        out.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

    def format_premarket(self, plans: list[TradePlan], market_summary: str) -> str:
        lines = [f"📌 {self.today()} 장전 단타 전략", market_summary or "시장 요약 없음", ""]
        if not plans:
            lines += [
                "🟨 오늘은 모델의 최소 확률/점수 기준을 통과한 종목이 없어 신규 단타는 현금 대기를 우선합니다.",
                "정확도를 높이기 위해 억지로 매일 3종목을 채우지 않는 선택적 예측(abstention)을 사용합니다.",
                "",
            ]
        for i, p in enumerate(plans, 1):
            lines += [
                f"{i}. {p.name} ({p.code}) | 종합 score {p.score:.1f}",
                f"기준가 {p.reference_price:,.0f}원",
                f"1차 관심 {p.entry_low_1:,.0f}~{p.entry_high_1:,.0f}원",
                f"2차 관심 {p.entry_low_2:,.0f}~{p.entry_high_2:,.0f}원",
                f"추격 금지 {p.chase_limit:,.0f}원 이상",
                f"무효화/손절 기준 {p.stop_price:,.0f}원",
                f"목표 {p.target1:,.0f} / {p.target2:,.0f}원",
                f"신규 단타자금 기준 비중 최대 {p.weight_pct:.1f}% | 약 {float(self.settings.get('risk.day_trade_capital_krw', 0)) * p.weight_pct / 100:,.0f}원 | 약 {int((float(self.settings.get('risk.day_trade_capital_krw', 0)) * p.weight_pct / 100) // max(p.entry_high_1, 1))}주 | 확신도 {p.confidence:.0f}/100",
                (f"ML: 상승확률 {p.up_probability:.1f}% | 기대수익 {p.expected_return_pct:+.2f}% | 예상 MFE/MAE {p.expected_mfe_pct:+.2f}/{p.expected_mae_pct:+.2f}% | 예측구간 {p.lower_return_pct:+.2f}~{p.upper_return_pct:+.2f}% | 모델품질 {p.model_quality:.0f}/100" if p.model_active else "ML: 학습표본/검증 기준 미충족 → 휴리스틱 중심"),
                f"근거: {p.rationale or '수치 모델 우선 선정'}",
                (f"주의: {', '.join(p.risk_flags)}" if p.risk_flags else ""),
                "",
            ]
        lines.append("※ HLB 고변동 보유 포지션을 반영해 신규 투입 비중을 제한했습니다. 확률은 보장이 아니며 조건이 맞지 않으면 현금 대기가 기본입니다.")
        return "\n".join(x for x in lines if x is not None)

    def intraday(self, scan_replacement: bool = True, force_summary: bool = True) -> list[dict[str, Any]]:
        trade_date = self.today()
        recs = self.db.get_recommendations(trade_date)
        updates = []
        try:
            self.macro.live_snapshot()
        except Exception as exc:
            log.warning("KR live macro refresh failed: %s", exc)
        tol = float(self.settings.get("technical.vwap_confirmation_tolerance_pct", 0.35)) / 100.0
        for r in recs:
            code = r["code"]
            try:
                q = self.kis.current_price(code)
                intra = self.kis.intraday_chart(code)
            except Exception as exc:
                updates.append({"code": code, "name": r["name"], "status": f"API 오류: {exc}"})
                continue
            vwap = session_vwap(intra)
            price = float(q.get("price") or 0)
            status = "관찰"
            if price <= float(r["stop_price"]):
                status = "⛔ 전략 무효/손절 기준 하회"
            elif price >= float(r["target2"]):
                status = "✅ 2차 목표 이상 - 잔여분 익절 검토"
            elif price >= float(r["target1"]):
                status = "✅ 1차 목표 도달 - 일부 익절/손절 상향 검토"
            elif price >= float(r["chase_limit"]):
                status = "🚫 신규 추격 금지"
            else:
                in1 = float(r["entry_low_1"]) <= price <= float(r["entry_high_1"])
                in2 = float(r["entry_low_2"]) <= price <= float(r["entry_high_2"])
                vwap_ok = not vwap or price >= vwap * (1.0 - tol)
                if (in1 or in2) and vwap_ok:
                    status = "🟢 진입 관심 구간 + VWAP 확인"
                elif in1 or in2:
                    status = "🟡 가격은 관심 구간이나 VWAP 약세 - 신규 진입 보류"
                elif vwap and price < vwap * (1.0 - tol):
                    status = "🟡 VWAP 하회 - 신규 진입 보류/추가매수 금지"
            payload = {
                "code": code, "name": r["name"], "price": price, "vwap": vwap,
                "day_high": q.get("high"), "day_low": q.get("low"), "change_pct": q.get("change_pct"),
                "turnover_krw": q.get("turnover_krw"), "volume": q.get("volume"), "status": status,
                "stop_price": r["stop_price"], "target1": r["target1"], "target2": r["target2"],
                "weight_pct": r["weight_pct"],
            }
            self.db.save_snapshot(trade_date, code, payload)
            updates.append(payload)

        # Full-market leadership scan continues even when the 08:30 model abstained.
        # This lets the app surface a genuinely new intraday leader without forcing
        # a morning trade.
        replacement = self.find_replacement(recs) if scan_replacement else None

        # 10-minute monitoring should not spam Telegram. Notify immediately when the
        # state changes (entry/target/stop/chase/VWAP weakness) or on the periodic
        # summary cadence. The app dashboard still sees every saved snapshot.
        changed = False
        important = False
        for u in updates:
            code = str(u.get("code", ""))
            status = str(u.get("status", ""))
            state_key = f"intraday_last_status:{code}"
            prev = str(self.db.get_state(state_key, ""))
            if status and status != prev:
                changed = True
                self.db.set_state(state_key, status)
            if any(x in status for x in ["진입 관심", "전략 무효", "1차 목표", "2차 목표", "추격 금지"]):
                important = important or (status != prev)
        if replacement:
            changed = True
            important = True
            self.db.set_state("last_replacement_candidate", replacement)
        elif scan_replacement:
            # Do not keep showing an old leader after a later full rescan says it no longer qualifies.
            self.db.set_state("last_replacement_candidate", None)

        if force_summary or changed or important:
            self.notifier.send(self.format_intraday(updates, replacement))
        return updates

    def find_replacement(self, recs: list[dict[str, Any]]) -> dict[str, Any] | None:
        existing = {r["code"] for r in recs}
        if recs:
            weakest = min(float(r["score"]) for r in recs)
            required_score = weakest + float(self.settings.get("monitoring.replacement_score_margin", 8.0))
        else:
            # If the morning model abstained, require a strong standalone score before
            # surfacing an intraday candidate. This avoids turning 'no trade' into a
            # weak forced recommendation later in the session.
            weakest = float(self.settings.get("prediction.minimum_final_score", 60.0))
            required_score = max(
                weakest,
                float(self.settings.get("monitoring.new_leader_min_score", 68.0)),
            )
        weights = self.learner.current_weights()
        scan_rows: dict[str, dict[str, Any]] = {}
        filtered = 0
        try:
            rows = self.kis.volume_rank("0000")[:12] + self.kis.fluctuation_rank("0000")[:12]
        except Exception as exc:
            self.db.set_state("last_replacement_scan", {
                "trade_date": self.today(), "phase": "intraday", "required_score": required_score,
                "evaluated_count": 0, "filtered_count": 0, "top": [], "accepted": None,
                "data_status": "api_error", "error": str(exc)[:240],
                "ts": datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec="seconds"),
            })
            return None
        seen = set()
        for raw in rows:
            rr = rank_row(raw)
            code = rr["code"]
            if code in existing or code in seen or not code.strip("0"):
                continue
            seen.add(code)
            try:
                q = self.kis.current_price(code)
                name = q.get("name") or rr["name"]
                if self._excluded(code, name):
                    filtered += 1
                    continue
                change = float(q.get("change_pct") or rr["change_pct"] or 0)
                if change > float(self.settings.get("market.max_one_day_gain_pct", 15.0)):
                    filtered += 1
                    continue
                daily = self.kis.daily_chart(code, 30)
                if daily.empty:
                    filtered += 1
                    continue
                today_reports = self.db.get_broker_reports(self.today())
                rel_reports = [x for x in today_reports if str(x.get("code", "")) == code]
                report_signal = self.research.stock_signals(rel_reports, {code: float(q.get("price") or 0)}).get(code, {}).get("signal", 0.5)
                feats = compute_features(q, daily, broker_report_signal=float(report_signal))
                feats.update(self.macro.equity_features("KR"))
                raw_sc = score(feats, weights)
                final = risk_adjusted_score(raw_sc, change, five_day_return(daily), [])
                scan_rows[code] = {
                    "code": code, "name": name, "price": float(q.get("price") or 0),
                    "change_pct": change, "score": round(final, 2),
                    "required_score": round(required_score, 2),
                    "gap": round(final - required_score, 2),
                    "up_probability": None, "expected_return_pct": None,
                    "risk_flags": [],
                    "reasons": ([f"점수 {final:.1f} < 장중 기준 {required_score:.1f}"] if final < required_score else []),
                }
                if final < required_score:
                    continue
                # Only now run DART/public-news local confirmation.
                discs = {code: self.dart.recent_disclosures(code, int(self.settings.get("local_engine.max_event_age_days", 7)))}
                temp = Candidate(code=code, name=name, price=float(q["price"]), change_pct=change,
                                 turnover_krw=float(q.get("turnover_krw") or 0), features=feats,
                                 raw_score=raw_sc, final_score=final, risk_flags=[])
                ar = self.analyst.enrich([temp.to_dict()], discs)
                evs = ar.get("evaluations", [])
                ev = evs[0] if evs else {}
                news = float(ev.get("news_catalyst", 0.5))
                event_positive = float(ev.get("event_positive_strength", 0.5))
                event_risk_inverse = float(ev.get("event_risk_inverse", 0.5))
                temp.ai_summary = str(ev.get("summary", ""))
                temp.risk_flags = list(ev.get("risk_flags", []))
                temp.features = compute_features(
                    q, daily, news_catalyst=news, broker_report_signal=float(report_signal),
                    event_positive_strength=event_positive, event_risk_inverse=event_risk_inverse,
                )
                temp.features.update(self.macro.equity_features("KR"))
                temp.raw_score = score(temp.features, weights)
                temp.final_score = risk_adjusted_score(temp.raw_score, change, five_day_return(daily), temp.risk_flags)
                reasons = []
                if temp.final_score < required_score:
                    reasons.append(f"이벤트 반영 후 점수 {temp.final_score:.1f} < 기준 {required_score:.1f}")
                if temp.risk_flags:
                    reasons.extend([f"risk: {x}" for x in temp.risk_flags[:3]])
                scan_rows[code].update({
                    "score": round(temp.final_score, 2), "gap": round(temp.final_score-required_score, 2),
                    "risk_flags": temp.risk_flags, "reasons": reasons,
                })
                if temp.final_score >= required_score:
                    p = make_plan(temp, daily, self.cfg)
                    accepted = {
                        "code": code, "name": name, "score": temp.final_score, "price": q["price"],
                        "status": "기존 최하위 후보보다 강한 신규 주도 후보",
                        "entry_low": p.entry_low_1, "entry_high": p.entry_high_1,
                        "chase_limit": p.chase_limit, "summary": temp.ai_summary,
                    }
                    top = sorted(scan_rows.values(), key=lambda z: float(z.get("score", 0)), reverse=True)[:5]
                    self.db.set_state("last_replacement_scan", {
                        "trade_date": self.today(), "phase": "intraday", "required_score": required_score,
                        "evaluated_count": len(scan_rows), "filtered_count": filtered, "top": top,
                        "accepted": accepted, "data_status": "ok",
                        "ts": datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec="seconds"),
                    })
                    return accepted
            except Exception as exc:
                log.debug("replacement scan failed %s: %s", code, exc)
                filtered += 1
                continue
        top = sorted(scan_rows.values(), key=lambda z: float(z.get("score", 0)), reverse=True)[:5]
        self.db.set_state("last_replacement_scan", {
            "trade_date": self.today(), "phase": "intraday", "required_score": required_score,
            "evaluated_count": len(scan_rows), "filtered_count": filtered, "top": top,
            "accepted": None, "data_status": "ok",
            "ts": datetime.now(ZoneInfo(self.settings.timezone)).isoformat(timespec="seconds"),
        })
        return None

    def format_intraday(self, updates: list[dict[str, Any]], replacement: dict[str, Any] | None) -> str:
        now = datetime.now(ZoneInfo(self.settings.timezone)).strftime("%H:%M")
        lines = [f"⏱ {self.today()} {now} 장중 업데이트", ""]
        for u in updates:
            if "price" not in u:
                lines += [f"{u.get('name', u.get('code'))}: {u.get('status')}", ""]
                continue
            lines += [
                f"• {u['name']} ({u['code']}) {u['price']:,.0f}원 ({float(u.get('change_pct') or 0):+.2f}%)",
                f"  VWAP {float(u.get('vwap') or 0):,.0f} | {u['status']}",
                f"  무효화 {float(u['stop_price']):,.0f} / 목표 {float(u['target1']):,.0f} → {float(u['target2']):,.0f}",
                f"  비중 상한 {float(u['weight_pct']):.1f}% (추가매수는 조건 재확인 전 금지)",
                "",
            ]
        if replacement:
            lines += [
                "🔄 교체 후보 감지",
                f"{replacement['name']} ({replacement['code']}) score {replacement['score']:.1f}",
                f"현재 {float(replacement['price']):,.0f}원 | 관심 {float(replacement['entry_low']):,.0f}~{float(replacement['entry_high']):,.0f}",
                f"추격 금지 {float(replacement['chase_limit']):,.0f}원 이상",
                replacement.get("summary", ""),
            ]
        else:
            lines.append("새 후보는 기존 추천보다 충분히 강하지 않아 교체하지 않습니다.")
        return "\n".join(lines)

    def close(self) -> dict[str, Any]:
        trade_date = self.today()
        recs = self.db.get_recommendations(trade_date)
        outcomes: list[dict[str, Any]] = []
        selection_rows: list[dict[str, Any]] = []
        code_returns: dict[str, float] = {}

        # Evaluate every recommended stock regardless of the user's actual trades.
        # Two layers are recorded: (1) selection quality from open->close and MFE/MAE,
        # (2) the planned pullback/stop/target execution simulation.
        for r in recs:
            try:
                bars = self.kis.full_day_intraday(r["code"], trade_date)
                strategy = self.simulate_outcome(r, bars)
                selection = self.evaluate_selection(r, bars, strategy)
            except Exception as exc:
                log.exception("close evaluation failed %s", r["code"])
                strategy = {
                    "entered": False, "exit_reason": f"evaluation_error:{exc}",
                    "return_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0, "reward": 0.0,
                }
                selection = {
                    "open_price": None, "close_price": None, "day_high": None, "day_low": None,
                    "open_to_close_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0,
                    "selection_reward": 0.0, "strategy_entered": False,
                    "strategy_return_pct": 0.0, "strategy_reward": 0.0,
                }
            self.db.save_outcome(trade_date, r["code"], strategy)
            self.db.save_pick_evaluation(trade_date, r["code"], selection)
            outcomes.append({"code": r["code"], "name": r["name"], **strategy})
            selection_rows.append({"code": r["code"], "name": r["name"], **selection})
            if selection.get("open_to_close_pct") is not None:
                code_returns[r["code"]] = float(selection.get("open_to_close_pct") or 0.0)

        # Evaluate the entire morning shadow candidate pool, not only the final recommendations.
        # This creates many more clean supervised samples while remaining fully out-of-sample:
        # the features were frozen before the open and INSERT OR IGNORE prevents later overwrites.
        selected_map = {str(x["code"]): x for x in selection_rows}
        shadow_rows: list[dict[str, Any]] = []
        cost_pct = float(self.settings.get("prediction.estimated_round_trip_cost_bps", 25.0)) / 100.0
        label_threshold = float(self.settings.get("prediction.label_threshold_pct", 0.30))
        reward_target = float(self.settings.get("learning.reward_target_return_pct", 4.0))
        reward_clip = float(self.settings.get("learning.reward_clip", 1.0))
        for snap in self.db.get_candidate_snapshots(trade_date):
            code = str(snap["code"])
            if code in selected_map and selected_map[code].get("open_price"):
                src = selected_map[code]
                op = float(src.get("open_price") or 0.0)
                cl = float(src.get("close_price") or 0.0)
                hi = float(src.get("day_high") or 0.0)
                lo = float(src.get("day_low") or 0.0)
            else:
                try:
                    daily = self.kis.daily_chart(code, 5)
                    if daily.empty:
                        continue
                    row = daily.iloc[-1]
                    if "date" in daily.columns and str(row.get("date", "")) != trade_date.replace("-", ""):
                        continue
                    op = float(row.get("open") or 0.0)
                    cl = float(row.get("close") or 0.0)
                    hi = float(row.get("high") or 0.0)
                    lo = float(row.get("low") or 0.0)
                except Exception as exc:
                    log.debug("shadow close feedback failed %s: %s", code, exc)
                    continue
            if op <= 0 or cl <= 0:
                continue
            gross = (cl / op - 1.0) * 100.0
            net = gross - cost_pct
            mfe = (hi / op - 1.0) * 100.0 if hi > 0 else 0.0
            mae = (lo / op - 1.0) * 100.0 if lo > 0 else 0.0
            composite = 0.68 * net + 0.17 * mfe + 0.15 * mae
            reward = max(-reward_clip, min(reward_clip, composite / max(0.1, reward_target)))
            ev = {
                "open_price": op, "close_price": cl, "day_high": hi, "day_low": lo,
                "gross_open_to_close_pct": gross, "net_open_to_close_pct": net,
                "mfe_pct": mfe, "mae_pct": mae, "label_up": net > label_threshold,
                "selection_reward": reward,
            }
            self.db.save_candidate_evaluation(trade_date, code, ev)
            shadow_rows.append({"code": code, "name": snap.get("name", code), **ev})

        model_feedback = self.predictor.evaluate_day(trade_date)

        # Also learn how useful each brokerage's report direction has been for short-term
        # price reaction. This does NOT require those report stocks to have been recommended.
        broker_reports = self.db.get_broker_reports(trade_date)
        max_report_codes = int(self.settings.get("research.max_report_stocks_for_close_feedback", 80))
        report_codes = []
        seen = set(code_returns)
        for rr in broker_reports:
            code = str(rr.get("code", ""))
            if code and code not in seen:
                seen.add(code)
                report_codes.append(code)
            if len(report_codes) >= max_report_codes:
                break
        for code in report_codes:
            try:
                daily = self.kis.daily_chart(code, 5)
                if daily.empty:
                    continue
                row = daily.iloc[-1]
                if "date" in daily.columns and str(row.get("date", "")) != trade_date.replace("-", ""):
                    continue
                op = float(row.get("open") or 0.0)
                cl = float(row.get("close") or 0.0)
                if op > 0 and cl > 0:
                    code_returns[code] = (cl / op - 1.0) * 100.0
            except Exception as exc:
                log.debug("report close feedback failed %s: %s", code, exc)

        broker_reliability = self.research.update_broker_reliability(trade_date, code_returns)
        learning = self.learner.update(trade_date)
        report = {
            "trade_date": trade_date,
            "selection_evaluations": selection_rows,
            "strategy_outcomes": outcomes,
            "broker_report_count": len(broker_reports),
            "broker_reliability": broker_reliability,
            "shadow_candidate_evaluations": shadow_rows,
            "model_feedback": model_feedback,
            "learning": learning,
        }
        self._write_report(f"close_{trade_date}.json", report)
        self.notifier.send(self.format_close(selection_rows, outcomes, learning, len(broker_reports), model_feedback, len(shadow_rows)))
        return report

    def evaluate_selection(self, rec: dict[str, Any], bars: pd.DataFrame, strategy: dict[str, Any] | None = None) -> dict[str, Any]:
        """Evaluate the *pick itself* regardless of whether the user or plan entered it."""
        if bars is None or bars.empty:
            return {
                "open_price": None, "close_price": None, "day_high": None, "day_low": None,
                "open_to_close_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0,
                "selection_reward": 0.0, "strategy_entered": bool((strategy or {}).get("entered")),
                "strategy_return_pct": float((strategy or {}).get("return_pct") or 0.0),
                "strategy_reward": float((strategy or {}).get("reward") or 0.0),
            }
        b = bars.copy()
        for c in ["open", "high", "low", "close"]:
            if c not in b.columns:
                b[c] = b.get("close", 0)
            b[c] = pd.to_numeric(b[c], errors="coerce").ffill()
        if "time" in b.columns:
            b = b[(b["time"] >= "090000") & (b["time"] <= "153000")].reset_index(drop=True)
        if b.empty:
            return self.evaluate_selection(rec, pd.DataFrame(), strategy)
        open_price = float(b.iloc[0]["open"] or b.iloc[0]["close"] or 0.0)
        close_price = float(b.iloc[-1]["close"] or 0.0)
        day_high = float(b["high"].max())
        day_low = float(b["low"].min())
        if open_price <= 0:
            ret = mfe = mae = reward = 0.0
        else:
            ret = (close_price / open_price - 1.0) * 100.0
            mfe = (day_high / open_price - 1.0) * 100.0
            mae = (day_low / open_price - 1.0) * 100.0
            # Reward the closing result most, while recognizing tradable upside and penalizing drawdown.
            composite = 0.65 * ret + 0.20 * mfe + 0.15 * mae
            target = float(self.settings.get("learning.reward_target_return_pct", 4.0))
            clip = float(self.settings.get("learning.reward_clip", 1.0))
            reward = max(-clip, min(clip, composite / max(0.1, target)))
        strategy = strategy or {}
        return {
            "open_price": round(open_price, 4), "close_price": round(close_price, 4),
            "day_high": round(day_high, 4), "day_low": round(day_low, 4),
            "open_to_close_pct": round(ret, 4), "mfe_pct": round(mfe, 4), "mae_pct": round(mae, 4),
            "selection_reward": round(reward, 6),
            "strategy_entered": bool(strategy.get("entered")),
            "strategy_return_pct": round(float(strategy.get("return_pct") or 0.0), 4),
            "strategy_reward": round(float(strategy.get("reward") or 0.0), 6),
        }

    def simulate_outcome(self, rec: dict[str, Any], bars: pd.DataFrame) -> dict[str, Any]:
        if bars is None or bars.empty:
            return {"entered": False, "exit_reason": "no_intraday_data", "return_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0, "reward": 0.0}
        b = bars.copy()
        for c in ["open", "high", "low", "close"]:
            if c not in b.columns:
                b[c] = b.get("close", 0)
            b[c] = pd.to_numeric(b[c], errors="coerce").ffill()
        if "time" in b.columns:
            b = b[(b["time"] >= "090000") & (b["time"] <= "153000")].reset_index(drop=True)
        zones = [
            (float(rec["entry_low_1"]), float(rec["entry_high_1"]), "entry1"),
            (float(rec["entry_low_2"]), float(rec["entry_high_2"]), "entry2"),
        ]
        entry_i = None
        entry_price = None
        entry_label = None
        for i, row in b.iterrows():
            for lo, hi, label in zones:
                if float(row["low"]) <= hi and float(row["high"]) >= lo:
                    entry_i, entry_price, entry_label = i, hi, label  # conservative long fill at upper edge
                    break
            if entry_i is not None:
                break
        if entry_i is None or entry_price is None:
            return {"entered": False, "exit_reason": "entry_not_touched", "return_pct": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0, "reward": 0.0}

        after = b.iloc[entry_i:].reset_index(drop=True)
        stop = float(rec["stop_price"])
        t1, t2 = float(rec["target1"]), float(rec["target2"])
        t1_taken = False
        half_ret = None
        exit_price = float(after["close"].iloc[-1])
        exit_reason = "close"
        exit_time = str(after.get("time", pd.Series([""])).iloc[-1])
        mfe = (float(after["high"].max()) / entry_price - 1.0) * 100.0
        mae = (float(after["low"].min()) / entry_price - 1.0) * 100.0

        for _, row in after.iterrows():
            lo, hi = float(row["low"]), float(row["high"])
            tm = str(row.get("time", ""))
            # Same-minute ambiguity is resolved conservatively: stop before target.
            if not t1_taken:
                if lo <= stop:
                    exit_price, exit_reason, exit_time = stop, "stop", tm
                    break
                if hi >= t1:
                    t1_taken = True
                    half_ret = (t1 / entry_price - 1.0) * 100.0
                    if hi >= t2 and lo > stop:
                        exit_price, exit_reason, exit_time = t2, "target2", tm
                        break
            else:
                if lo <= stop:
                    exit_price, exit_reason, exit_time = stop, "target1_then_stop", tm
                    break
                if hi >= t2:
                    exit_price, exit_reason, exit_time = t2, "target2", tm
                    break
        final_leg_ret = (exit_price / entry_price - 1.0) * 100.0
        ret = (half_ret + final_leg_ret) / 2.0 if half_ret is not None else final_leg_ret
        if t1_taken and exit_reason == "close":
            exit_reason = "target1_then_close"
        target_ret = float(self.settings.get("learning.reward_target_return_pct", 4.0))
        clip = float(self.settings.get("learning.reward_clip", 1.0))
        reward = max(-clip, min(clip, ret / max(0.1, target_ret)))
        return {
            "entered": True,
            "entry_time": str(b.iloc[entry_i].get("time", "")),
            "entry_price": round(entry_price, 4),
            "entry_label": entry_label,
            "exit_time": exit_time,
            "exit_price": round(exit_price, 4),
            "exit_reason": exit_reason,
            "return_pct": round(ret, 4),
            "mfe_pct": round(mfe, 4),
            "mae_pct": round(mae, 4),
            "reward": round(reward, 6),
        }

    def format_close(self, selections: list[dict[str, Any]], outcomes: list[dict[str, Any]], learning: dict[str, Any], broker_report_count: int = 0, model_feedback: dict[str, Any] | None = None, shadow_count: int = 0) -> str:
        model_feedback = model_feedback or {}
        lines = [
            f"🧠 {self.today()} 장마감 전체 추천 피드백",
            f"오늘 분석한 증권사 기업리포트: {broker_report_count}건 | shadow 후보 평가: {shadow_count}종목",
            "",
        ]
        outcome_map = {o["code"]: o for o in outcomes}
        for s in selections:
            o = outcome_map.get(s["code"], {})
            lines.append(
                f"• {s['name']}: 선정평가 {float(s.get('open_to_close_pct') or 0):+.2f}% "
                f"| MFE {float(s.get('mfe_pct') or 0):+.2f}% | MAE {float(s.get('mae_pct') or 0):+.2f}% "
                f"| selection reward {float(s.get('selection_reward') or 0):+.3f}"
            )
            if o.get("entered"):
                lines.append(
                    f"  계획전략: {float(o.get('return_pct') or 0):+.2f}% | {o.get('exit_reason')}"
                )
            else:
                lines.append(f"  계획전략: 미진입 ({o.get('exit_reason')}) — 그래도 종목선정 학습에는 포함")
        m = learning.get("metrics", {})
        lines += [
            "",
            f"누적 최종추천 표본 {m.get('samples', 0)} | 시가→종가 승률 {m.get('win_rate_pct', 0)}% | 평균 {m.get('avg_return_pct', 0):+.2f}% | 최대DD {m.get('max_drawdown_pct', 0)}%",
        ]
        if model_feedback.get("predictions"):
            lines += [
                f"ML 당일검증: 방향정확도 {100*float(model_feedback.get('directional_accuracy', 0)):.1f}% | Brier {float(model_feedback.get('brier', 0)):.3f} | 수익률 MAE {float(model_feedback.get('return_mae_pct', 0)):.2f}%",
                f"횡단면 Rank-IC {float(model_feedback.get('cross_sectional_rank_ic', 0)):+.3f} | 예측구간 포함률 {100*float(model_feedback.get('interval_coverage', 0)):.1f}%",
            ]
        if learning.get("updated"):
            before, after = learning.get("before", {}), learning.get("after", {})
            changes = sorted(((k, after[k] - before.get(k, 0)) for k in after), key=lambda x: abs(x[1]), reverse=True)[:5]
            lines.append("오늘 선정모델 학습 반영: " + ", ".join(f"{k} {d:+.3f}" for k, d in changes))
        else:
            lines.append("가중치 변경 없음: " + str(learning.get("reason", "")))
        lines.append("※ 실제 매수 여부와 무관한 모델 사후평가이며, 실제 체결수익과는 다를 수 있습니다.")
        return "\n".join(lines)

    def status(self) -> dict[str, Any]:
        rows = self.db.get_pick_evaluations(int(self.settings.get("learning.report_lookback_days", 60)))
        metrics = self.learner.performance_metrics(rows)
        return {
            "mode": "FREE_LOCAL_RULE_ML",
            "free_mode": bool(self.settings.get("free_mode", True)),
            "openai": "disabled",
            "weights": self.learner.current_weights(),
            "metrics": metrics,
            "predictive_model": self.predictor.status(),
            "last_prediction_feedback": self.db.get_state("last_prediction_feedback", {}),
            "model_history": self.db.get_model_history(10),
            "broker_reliability": self.db.get_state("broker_reliability", {}),
            "global_macro": self.db.get_state("global_macro_latest", {}),
        }

    def research_only(self) -> dict[str, Any]:
        reports = self.research.collect_and_analyze(self.today())
        signals = self.research.stock_signals(reports)
        payload = {"trade_date": self.today(), "report_count": len(reports), "reports": reports, "stock_signals": signals}
        self._write_report(f"broker_research_{self.today()}.json", payload)
        if reports:
            self.notifier.send(self.format_research_digest(reports, signals))
        return payload

    def format_research_digest(self, reports: list[dict[str, Any]], signals: dict[str, dict[str, Any]]) -> str:
        brokers = {str(r.get("broker", "")) for r in reports if str(r.get("broker", ""))}
        names: dict[str, str] = {}
        for r in reports:
            code = str(r.get("code", ""))
            if code and code not in names:
                names[code] = str(r.get("name", code))
        ranked = sorted(signals.items(), key=lambda kv: float(kv[1].get("signal", 0.5)), reverse=True)
        today_count = sum(1 for r in reports if str(r.get("report_date", "")) == self.today())
        lookback = int(self.settings.get("research.calendar_lookback_days", 4))
        lines = [
            f"📚 {self.today()} 증권사 기업리포트 분석",
            f"오늘 {today_count}건 | 최근 {lookback}일 유효 {len(reports)}건 | {len(signals)}종목 | {len(brokers)}개 증권사/기관",
            "",
            "상위 리포트 컨센서스",
        ]
        for code, info in ranked[:8]:
            lines.append(
                f"• {names.get(code, code)} ({code}) signal {float(info.get('signal', 0.5)):.2f} "
                f"| {int(info.get('report_count', 0))}건/{int(info.get('broker_count', 0))}곳 "
                f"| sentiment {float(info.get('sentiment', 0)):+.2f} | revision {float(info.get('revision', 0)):+.2f}"
            )
        negatives = [(c, x) for c, x in reversed(ranked) if float(x.get("signal", 0.5)) < 0.46][:5]
        if negatives:
            lines += ["", "하향/부정 변화 주의"]
            for code, info in negatives:
                lines.append(
                    f"• {names.get(code, code)} ({code}) signal {float(info.get('signal', 0.5)):.2f} "
                    f"| sentiment {float(info.get('sentiment', 0)):+.2f} | revision {float(info.get('revision', 0)):+.2f}"
                )
        lines += [
            "",
            "※ 모든 리포트 원문/요약 분석값은 data/reports의 broker_research_날짜.json과 DB에 저장됩니다.",
            "※ 리포트 신호는 장전 종목선정의 한 feature일 뿐, 리포트만으로 추천하지 않습니다.",
        ]
        return "\n".join(lines)
