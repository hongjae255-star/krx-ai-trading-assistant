from __future__ import annotations

import copy
import json
import logging
from dataclasses import replace
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .config import Settings
from .db import Database
from .features import add_cross_sectional_features, compute_features, session_vwap
from .global_macro import GlobalMacroEngine
from .kis import KISClient
from .learner import AdaptiveWeightLearner
from .models import Candidate, TradePlan
from .notifier import TelegramNotifier
from .predictor import PredictiveEnsemble
from .regime import attach_regime_features, infer_market_regime
from .scoring import five_day_return, risk_adjusted_score, score
from .features import atr

log = logging.getLogger(__name__)


def _f(v: Any) -> float:
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return 0.0


def _round_usd(x: float) -> float:
    return round(max(0.0, float(x)) + 1e-10, 2)


def make_us_plan(candidate: Candidate, daily: pd.DataFrame, config: dict[str, Any]) -> TradePlan:
    tcfg = config.get("us_market", {}).get("technical", config.get("technical", {}))
    ref = float(candidate.price)
    a = atr(daily, int(tcfg.get("atr_period", 14)))
    if a <= 0:
        a = max(ref * 0.025, 0.10)
    p1 = float(tcfg.get("entry_pullback_atr", 0.30)); p2 = float(tcfg.get("second_entry_pullback_atr", 0.70))
    stop_a = float(tcfg.get("stop_atr", 1.10)); r1 = float(tcfg.get("target1_r", 1.20)); r2 = float(tcfg.get("target2_r", 2.00))
    e1_hi = ref - 0.08*a; e1_lo = ref - p1*a
    e2_hi = ref - max(p1+0.10, p2-0.15)*a; e2_lo = ref - p2*a
    recent_low = float(daily["low"].tail(5).min()) if daily is not None and not daily.empty and "low" in daily else 0.0
    model_stop = ref - stop_a*a
    stop = max(ref-1.45*a, min(model_stop, recent_low-0.05*a)) if 0 < recent_low < ref else model_stop
    mid = (e1_lo+e1_hi)/2.0; risk = max(mid-stop, 0.4*a)
    pred = dict(candidate.prediction or {}); p_up = float(pred.get("up_probability", 0.0) or 0.0); quality = float(pred.get("quality", 0.0) or 0.0)
    confidence = candidate.final_score if not pred.get("active") else 100*(0.55*p_up+0.45*max(0,min(1,candidate.final_score/100)))*(0.85+0.15*quality)
    return TradePlan(
        code=candidate.code, name=candidate.name, reference_price=_round_usd(ref),
        entry_low_1=_round_usd(min(e1_lo,e1_hi)), entry_high_1=_round_usd(max(e1_lo,e1_hi)),
        entry_low_2=_round_usd(min(e2_lo,e2_hi)), entry_high_2=_round_usd(max(e2_lo,e2_hi)),
        chase_limit=_round_usd(ref+0.35*a), stop_price=_round_usd(stop),
        target1=_round_usd(mid+r1*risk), target2=_round_usd(mid+r2*risk), weight_pct=0.0,
        confidence=round(min(95,max(35,confidence)),1), score=round(candidate.final_score,2),
        rationale=candidate.ai_summary or "US price/volume + global macro + local ML ensemble",
        features=dict(candidate.features or {}), risk_flags=list(candidate.risk_flags or []),
        up_probability=round(100*p_up,1) if p_up else 0.0,
        expected_return_pct=round(float(pred.get("expected_return_pct",0) or 0),2),
        expected_mfe_pct=round(float(pred.get("expected_mfe_pct",0) or 0),2),
        expected_mae_pct=round(float(pred.get("expected_mae_pct",0) or 0),2),
        lower_return_pct=round(float(pred.get("lower_return_pct",0) or 0),2), upper_return_pct=round(float(pred.get("upper_return_pct",0) or 0),2),
        model_active=bool(pred.get("active",False)), model_quality=round(100*quality,1),
    )


class USMarketAssistant:
    """US equity scanner using KIS overseas read-only APIs and a separate learning DB."""

    def __init__(self, settings: Settings, kis: KISClient, macro: GlobalMacroEngine):
        self.settings = settings; self.kis = kis; self.macro = macro
        self.cfg = copy.deepcopy(settings.config)
        self.cfg["market"] = dict(settings.get("us_market.market", {}) or {})
        self.cfg["base_weights"] = dict(settings.get("us_market.base_weights", settings.get("base_weights", {})) or {})
        self.db = Database(settings.path("storage.us_sqlite_path"))
        self.learner = AdaptiveWeightLearner(self.db, self.cfg)
        self.predictor = PredictiveEnsemble(self.db, self.cfg, settings.path("storage.us_model_dir"))
        self.notifier = TelegramNotifier()
        self.tz = ZoneInfo("America/New_York")

    def today(self) -> str:
        return datetime.now(self.tz).date().isoformat()

    def discover(self) -> tuple[list[Candidate], dict[str,pd.DataFrame], str, dict[str,str]]:
        weights = self.learner.current_weights(); market_cfg = self.cfg.get("market", {})
        pool_size = int(market_cfg.get("candidate_pool_size", 24)); min_price = float(market_cfg.get("min_price_usd", 5)); min_turnover = float(market_cfg.get("min_turnover_usd", 50_000_000))
        exchanges = list(self.settings.get("us_market.exchanges", ["NAS","NYS","AMS"]))
        excluded = {str(x).upper() for x in self.settings.get("us_market.exclude_symbols", [])}
        excluded_names = [str(x).upper() for x in self.settings.get("us_market.exclude_name_contains", [" ETF", "2X", "3X", "ULTRA", "BULL", "BEAR", "INVERSE"])]
        rows: dict[str, dict[str,Any]] = {}; exmap: dict[str,str] = {}
        for ex in exchanges:
            try:
                for r in self.kis.overseas_turnover_rank(ex):
                    sym = str(r.get("symb","")).upper().strip()
                    seed_name = str(r.get("name") or r.get("enma") or "").upper()
                    if not sym or sym in rows or sym in excluded or any(x in seed_name for x in excluded_names): continue
                    rows[sym] = r; exmap[sym] = str(r.get("excd") or ex)
            except Exception as exc:
                log.warning("US turnover rank failed %s: %s", ex, exc)
        candidates: list[Candidate] = []; dmap: dict[str,pd.DataFrame] = {}
        for sym, seed in list(rows.items())[: max(60,pool_size*4)]:
            ex = exmap[sym]
            try:
                q = self.kis.overseas_current_price(sym, ex)
                daily = self.kis.overseas_daily_chart(sym, ex, 45)
            except Exception as exc:
                log.warning("US quote/daily failed %s/%s: %s", ex, sym, exc); continue
            price = float(q.get("price",0) or 0); turnover = float(q.get("turnover_usd",0) or seed.get("tamt",0) or 0)
            if price < min_price or turnover < min_turnover or daily.empty: continue
            feats = compute_features({"price":price,"change_pct":q.get("change_pct",0),"turnover_krw":turnover,"volume":q.get("volume",0)}, daily)
            # Macro variables are shared across all stocks but become valuable through interactions in tree models.
            feats.update(self.macro.equity_features("US"))
            raw = score(feats, weights); risk_flags=[]
            h = risk_adjusted_score(raw, float(q.get("change_pct",0)), five_day_return(daily), risk_flags)
            candidates.append(Candidate(code=sym,name=str(q.get("name") or seed.get("name") or sym),price=price,change_pct=float(q.get("change_pct",0)),turnover_krw=turnover,volume=float(q.get("volume",0)),features=feats,raw_score=raw,heuristic_score=h,final_score=h,risk_flags=risk_flags,ai_summary="US liquidity/momentum + cross-asset macro"))
            dmap[sym]=daily
            if len(candidates)>=pool_size: break
        if not candidates: return [], dmap, "US candidate data unavailable", exmap
        add_cross_sectional_features(candidates); regime=infer_market_regime(candidates)
        macro_f=self.macro.equity_features("US")
        for c in candidates:
            c.features=attach_regime_features(dict(c.features or {}),regime); c.features.update(macro_f); c.features["heuristic_score_norm"]=max(0,min(1,c.heuristic_score/100))
            if float(macro_f.get("macro_stress",0.5))>=0.72: c.risk_flags=list(dict.fromkeys((c.risk_flags or [])+["macro_event_risk"]))
        status=self.predictor.train(self.today()); preds=self.predictor.predict([{"features":c.features,"heuristic_score":c.heuristic_score} for c in candidates],self.today(),regime.name)
        for c,p in zip(candidates,preds):
            c.prediction=p.to_dict(); c.model_score=p.model_score; blend=p.blend_weight if p.active else 0.0
            c.final_score=risk_adjusted_score((1-blend)*c.heuristic_score+blend*c.model_score,c.change_pct,five_day_return(dmap[c.code]),c.risk_flags)
        candidates.sort(key=lambda x:x.final_score,reverse=True)
        return candidates,dmap,f"US regime={regime.name}; {self.macro.latest().get('summary','')}; ML={'active' if status.get('active') else 'fallback'}",exmap

    def _allocate(self, plans:list[TradePlan])->list[TradePlan]:
        if not plans:return []
        cap=float(self.settings.get("us_market.max_total_deployed_pct",50)); max_single=float(self.settings.get("us_market.max_single_stock_pct",20)); scores=[max(1,p.score) for p in plans]; total=sum(scores)
        return [replace(p,weight_pct=round(min(max_single,cap*s/total),1)) for p,s in zip(plans,scores)]

    def premarket(self)->list[TradePlan]:
        td=self.today(); cands,dmap,summary,exmap=self.discover(); self.macro.snapshot()
        if not cands:
            self.notifier.send(f"🇺🇸 [{td}] US premarket: candidates unavailable\n{summary}"); return []
        self.db.save_candidate_snapshots(td,cands); self.db.set_state(f"us_exchange_map:{td}",exmap)
        n=int(self.settings.get("us_market.market.final_pick_count",3)); min_score=float(self.settings.get("us_market.minimum_final_score",60))
        selected=[c for c in cands if c.final_score>=min_score][:n]
        plans=self._allocate([make_us_plan(c,dmap[c.code],self.cfg) for c in selected]); self.db.save_recommendations(td,plans)
        self.notifier.send(self.format_premarket(plans,summary)); return plans

    def format_premarket(self,plans:list[TradePlan],summary:str)->str:
        lines=[f"🇺🇸 {self.today()} US 09:15 ET 전략",summary,""]
        if not plans: return "\n".join(lines+["기준 통과 종목 없음 → 현금 대기"])
        for i,p in enumerate(plans,1):
            lines += [f"{i}. {p.name} ({p.code}) ${p.reference_price:.2f}",f"  관심 ${p.entry_low_1:.2f}~${p.entry_high_1:.2f} | 추격금지 ${p.chase_limit:.2f}",f"  무효 ${p.stop_price:.2f} | 목표 ${p.target1:.2f} → ${p.target2:.2f} | 최대 {p.weight_pct:.0f}%"]
        return "\n".join(lines)

    def find_replacement(self, current_recs: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """30-minute full-market rescan for a clearly stronger US leader.

        Works even when the morning model abstained. This is analysis-only.
        """
        current_recs = current_recs or []
        current_codes = {str(r.get("code", "")).upper() for r in current_recs}
        current_best = max([float(r.get("score", 0) or 0) for r in current_recs] or [0.0])
        cands, dmap, summary, exmap = self.discover()
        self.db.set_state(f"us_exchange_map:{self.today()}", {**(self.db.get_state(f"us_exchange_map:{self.today()}", {}) or {}), **exmap})
        min_score = float(self.settings.get("us_market.minimum_final_score", 58.0))
        edge = float(self.settings.get("us_market.replacement_min_score_edge", 4.0))
        for c in cands:
            if c.code.upper() in current_codes:
                continue
            if c.final_score < max(min_score, current_best + edge if current_best else min_score):
                continue
            daily = dmap.get(c.code)
            if daily is None or daily.empty:
                continue
            plan = make_us_plan(c, daily, self.cfg)
            row = {
                "market": "US", "code": c.code, "name": c.name, "score": round(c.final_score, 2),
                "price": c.price, "change_pct": c.change_pct, "status": "신규 주도 후보",
                "entry_low": plan.entry_low_1, "entry_high": plan.entry_high_1,
                "chase_limit": plan.chase_limit, "stop_price": plan.stop_price,
                "target1": plan.target1, "target2": plan.target2,
                "summary": summary, "ts": datetime.now(self.tz).isoformat(timespec="seconds"),
            }
            self.db.set_state("us_last_replacement_candidate", row)
            return row
        self.db.set_state("us_last_replacement_candidate", None)
        return None

    def intraday(self, force_summary:bool=False, scan_replacement: bool=False)->list[dict[str,Any]]:
        td=self.today(); recs=self.db.get_recommendations(td); exmap=self.db.get_state(f"us_exchange_map:{td}",{}) or {}; updates=[]
        # Refresh the intraday cross-asset layer once per configured cache interval.
        try:
            self.macro.live_snapshot()
        except Exception as exc:
            log.warning("US live macro refresh failed: %s", exc)
        for r in recs:
            sym=str(r["code"]); ex=str(exmap.get(sym,"NAS"))
            try:
                q=self.kis.overseas_current_price(sym,ex); bars=self.kis.overseas_intraday_chart(sym,ex,5)
            except Exception as exc:
                log.warning("US intraday failed %s: %s",sym,exc); continue
            pr=float(q.get("price",0)); vwap=session_vwap(bars); status="관망"
            if pr<=float(r["stop_price"]):status="손절/전략 무효화"
            elif pr>=float(r["target2"]):status="2차 목표 도달"
            elif pr>=float(r["target1"]):status="1차 목표 도달"
            elif pr>=float(r["chase_limit"]):status="추격 금지"
            elif float(r["entry_low_1"])<=pr<=float(r["entry_high_1"]):status="1차 관심구간"
            elif vwap and pr>=vwap:status="VWAP 상단"
            row={"code":sym,"name":r.get("name",sym),"price":pr,"change_pct":q.get("change_pct",0),"vwap":vwap,"day_high":q.get("high",0),"day_low":q.get("low",0),"status":status,"ts":datetime.now(self.tz).isoformat(timespec="seconds")}
            self.db.save_snapshot(td,sym,row); updates.append(row)
        replacement = self.find_replacement(recs) if scan_replacement else None
        if replacement:
            self.notifier.send(
                f"⚡ 🇺🇸 US 신규 주도 후보\n{replacement['name']} ({replacement['code']}) ${replacement['price']:.2f}\n"
                f"score {replacement['score']:.1f} | 관심 ${replacement['entry_low']:.2f}~${replacement['entry_high']:.2f} | "
                f"추격금지 ${replacement['chase_limit']:.2f}"
            )
        if force_summary and (updates or replacement):
            lines=[f"🇺🇸 {td} US intraday update"]+[f"• {x['name']} ${x['price']:.2f} | {x['status']} | VWAP ${x['vwap']:.2f}" for x in updates]
            if replacement:
                lines.append(f"⚡ 신규 후보 {replacement['name']} ${replacement['price']:.2f} (score {replacement['score']:.1f})")
            self.notifier.send("\n".join(lines))
        return updates

    def _selection_eval(self, rec:dict[str,Any], bars:pd.DataFrame)->dict[str,Any]:
        if bars is None or bars.empty:return {"code":rec["code"],"open_to_close_pct":0,"mfe_pct":0,"mae_pct":0,"selection_reward":0}
        b=bars.copy();
        for c in ["open","high","low","close"]: b[c]=pd.to_numeric(b[c],errors="coerce").ffill()
        op=float(b.iloc[0]["open"] or b.iloc[0]["close"]); cl=float(b.iloc[-1]["close"]); hi=float(b["high"].max()); lo=float(b["low"].min())
        ret=(cl/op-1)*100 if op else 0; mfe=(hi/op-1)*100 if op else 0; mae=(lo/op-1)*100 if op else 0
        cost_bps=float(self.settings.get("prediction.estimated_round_trip_cost_bps",25)); net=ret-cost_bps/100.0
        threshold=float(self.settings.get("prediction.label_threshold_pct",0.30))
        target=float(self.settings.get("learning.reward_target_return_pct",4)); reward=max(-1,min(1,net/max(.1,target)))
        return {"code":rec["code"],"name":rec.get("name",rec["code"]),"open_price":op,"close_price":cl,"day_high":hi,"day_low":lo,"open_to_close_pct":ret,"gross_open_to_close_pct":ret,"net_open_to_close_pct":net,"label_up":bool(net>threshold),"mfe_pct":mfe,"mae_pct":mae,"selection_reward":reward,"strategy_entered":False,"strategy_return_pct":0,"strategy_reward":0}

    def close(self)->dict[str,Any]:
        td=self.today(); recs=self.db.get_recommendations(td); shadows=self.db.get_candidate_snapshots(td); exmap=self.db.get_state(f"us_exchange_map:{td}",{}) or {}; evaluated=[]
        for r in shadows:
            sym=str(r["code"]); ex=str(exmap.get(sym,"NAS"))
            try: bars=self.kis.overseas_intraday_chart(sym,ex,5,include_previous=False)
            except Exception as exc: log.warning("US close bars failed %s: %s",sym,exc); continue
            ev=self._selection_eval(r,bars); self.db.save_candidate_evaluation(td,sym,ev); evaluated.append(ev)
        recmap={str(r["code"]):r for r in recs}
        for ev in evaluated:
            if ev["code"] in recmap:self.db.save_pick_evaluation(td,ev["code"],ev)
        learning=self.learner.update(); feedback=self.predictor.evaluate_day(td); self.predictor.train(td)
        result={"trade_date":td,"evaluated":len(evaluated),"learning":learning,"prediction_feedback":feedback}; self.db.set_state("us_last_close",result)
        if recs:self.notifier.send(f"🇺🇸 {td} US close feedback\n추천 {len(recs)} | shadow {len(evaluated)} | ML feedback saved")
        return result

    def status(self)->dict[str,Any]:
        return {"market":"US","trade_date":self.today(),"predictive_model":self.predictor.status(),"last_close":self.db.get_state("us_last_close",{}),"recommendations":self.db.get_recommendations(self.today())}
