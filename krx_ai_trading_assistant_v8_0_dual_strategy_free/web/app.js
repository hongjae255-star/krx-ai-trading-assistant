const $ = s => document.querySelector(s);
const fmt = n => Number(n||0).toLocaleString('ko-KR', {maximumFractionDigits:0});
const money = (n,c='KRW') => c==='USD' ? `$${Number(n||0).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2})}` : `${fmt(n)}원`;
const pct = n => `${Number(n||0)>=0?'+':''}${Number(n||0).toFixed(2)}%`;
let dashboard = null, refreshTimer = null, currentMarket='KR';

async function api(path){
  const cloudBase = (window.KRX_DATA_BASE || '').replace(/\/$/, '');
  if(window.KRX_CLOUD_MODE && cloudBase){
    const bust = `?t=${Date.now()}`;
    if(path.startsWith('/api/history')){
      const r = await fetch(`${cloudBase}/history.json${bust}`, {cache:'no-store'});
      if(!r.ok) throw new Error(`cloud history ${r.status}`);
      return r.json();
    }
    const r = await fetch(`${cloudBase}/dashboard.json${bust}`, {cache:'no-store'});
    if(!r.ok) throw new Error(`cloud dashboard ${r.status}`);
    const d = await r.json();
    if(path==='/api/dashboard') return d;
    if(path==='/api/reports') return d.reports || {top:[]};
    if(path==='/api/learning') return d.learning || {};
    if(path==='/api/recommendations') return {items:d.recommendations||[]};
    if(path==='/api/us/recommendations') return {trade_date:d.us?.trade_date,items:d.us?.recommendations||[],learning:d.us?.learning||{}};
    if(path==='/api/global') return d.global_macro || {};
    if(path==='/api/settings') return {monitoring:{active_interval_minutes:d.monitor_interval_minutes},risk:d.risk||{},positions:d.positions||[]};
    throw new Error(`unsupported cloud path ${path}`);
  }
  const token = localStorage.getItem('krx_api_token') || '';
  const r = await fetch(path,{headers:token?{'X-API-Token':token}:{} });
  if(r.status===401){ openSettings(true); throw new Error('API token 필요'); }
  if(!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
}

function macroValue(d,key,digits=2,suffix=''){
  const v=d?.series?.[key]?.value;
  return v==null||Number(v)===0?'-':`${Number(v).toFixed(digits)}${suffix}`;
}
function renderMacro(d){
  const f=d?.features||{};
  const score=100*Number(f.macro_global_risk_on??.5); const liveScore=100*Number(d?.live?.features?.market_live_risk_on??.5);
  const total=Number(d?.total_series_count||Object.keys(d?.series||{}).length||0), fresh=Number(d?.fresh_series_count||0), stale=Number(d?.stale_series_count||0), hard=Number(d?.hard_failure_count??d?.failures?.length??0);
  const keyStatus=d?.api_key_status||'unknown';
  const sourceLabel=keyStatus==='valid'?(d?.source||'FRED_API'):(keyStatus==='missing'?'FRED 키 미설정':keyStatus==='invalid'?'FRED 키 형식 오류':(d?.source||'local cache'));
  $('#macroCard').innerHTML=`<div class="risk-gauge"><div><b>글로벌 시장 분위기</b><div class="market-label">${esc(sourceLabel)} · 최신 ${fresh}/${total||'-'} · 이전값 ${stale} · 실제 실패 ${hard}</div></div><div class="risk-score">${score.toFixed(0)}</div></div><div class="macro-grid"><div class="macro-item"><span>미 2년물</span><b>${macroValue(d,'ust_2y',2,'%')}</b></div><div class="macro-item"><span>미 10년물</span><b>${macroValue(d,'ust_10y',2,'%')}</b></div><div class="macro-item"><span>VIX</span><b>${macroValue(d,'vix')}</b></div><div class="macro-item"><span>USD/KRW</span><b>${macroValue(d,'usdkrw',0)}</b></div><div class="macro-item"><span>HY OAS</span><b>${macroValue(d,'hy_oas',2,'%')}</b></div><div class="macro-item"><span>유동성</span><b>${(100*Number(f.macro_liquidity_support??.5)).toFixed(0)}</b></div></div>${keyStatus!=='valid'?'<div class="refresh-note">GitHub Actions에서는 무료 FRED_API_KEY를 등록하면 공식 API를 사용해 매크로 실패율이 크게 줄어듭니다.</div>':''}`;
}

function activeData(d){
  if(currentMarket==='US') return {recs:d.us?.recommendations||[], learning:d.us?.learning||{}, interval:d.us?.monitor_interval_minutes||15, reports:{report_count:0,broker_count:0,top:[]}, currency:'USD', lanes:d.us?.strategy_lanes||{}, institutional:d.us?.institutional||{}};
  return {recs:d.recommendations||[], learning:d.learning||{}, interval:d.monitor_interval_minutes||15, reports:d.reports||{}, currency:'KRW', lanes:d.strategy_lanes||{}, institutional:{}};
}

const esc = v => String(v??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const TERM_MAP={
  'Stage-2 추세':'장기 상승추세','중기 모멘텀':'최근 상승 힘','상대강도':'시장보다 강한 정도','52주 고가':'1년 고점 근접',
  'CAN-SLIM proxy':'성장주 조건','수급/거래량':'매수세·거래량','품질/리스크':'위험 관리','시장방향':'전체 시장 분위기',
  '유명기관 13F':'미국 대형기관 보유 참고','변동성 수축':'가격 흔들림 감소','ATR':'평균 하루 변동폭','시장 대비 강도':'시장 대비 강도'
};
function easyTerm(v){let x=String(v??'');Object.entries(TERM_MAP).forEach(([a,b])=>{x=x.replaceAll(a,b)});return x}
function simpleWhy(r){
  const strengths=(r?.strengths||[]).slice(0,3).map(easyTerm);
  const reasons=(r?.reasons||[]).slice(0,2).map(easyTerm);
  if(strengths.length)return strengths.map(x=>`✓ ${x}`).join(' · ');
  if(reasons.length)return reasons.join(' · ');
  return '여러 조건을 종합했을 때 현재 후보 중 상대적으로 점수가 높습니다.';
}
function diagnosticData(d){return currentMarket==='US'?(d.us?.candidate_diagnostics||{}):(d.candidate_diagnostics||{})}
function sparkline(points=[]){
  const vals=(points||[]).map(x=>Number(x.close)).filter(Number.isFinite); if(vals.length<2)return '<div class="chart-empty">차트 데이터 대기</div>';
  const lo=Math.min(...vals), hi=Math.max(...vals), span=Math.max(hi-lo,Math.abs(hi)*.002,1e-9), w=320,h=92,p=5;
  const xy=vals.map((v,i)=>`${(p+i*(w-2*p)/(vals.length-1)).toFixed(1)},${(p+(hi-v)*(h-2*p)/span).toFixed(1)}`).join(' ');
  const cls=vals.at(-1)>=vals[0]?'spark-up':'spark-down';
  return `<svg class="spark ${cls}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img"><polyline points="${xy}" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><line x1="5" y1="87" x2="315" y2="87" class="spark-base"/></svg>`;
}
function renderMarketPulse(d){
  const p=d?.market_pulse||{}, idx=p.indexes||{}, side=currentMarket==='US'?(p.us||{}):(p.kr||{}), reg=side.regime||{};
  const keys=currentMarket==='US'?['nasdaq','sp500','dow','russell2000']:['kospi','kosdaq'];
  const total=Number(reg.total||0), above=Number(reg.above_ma20||0), pos=Number(reg.positive_5d||0), score=Number(reg.score??50);
  const regimeCls=score>=65?'good':score<=35?'bad':'neutral';
  $('#marketRegime').innerHTML=`<div class="pulse-top"><div><b>${esc(reg.label||'데이터 대기')}</b><div class="sub">${esc(side.leadership||'리더십 계산 중')} · 최근 5일 시장 대비 강도 ${pct(side.growth_lead_5d_pct||0)}</div></div><div class="pulse-score ${regimeCls}">${score.toFixed(0)}</div></div><div class="pulse-metrics"><div><span>20일 평균선 위</span><b>${above}/${total||'-'}</b></div><div><span>5일 상승</span><b>${pos}/${total||'-'}</b></div><div><span>평균 5일 수익</span><b>${pct(reg.avg_5d_pct||0)}</b></div></div>`;
  $('#indexCharts').innerHTML=keys.map(k=>{const x=idx[k];if(!x)return `<article class="index-card card"><b>${k.toUpperCase()}</b><div class="chart-empty">데이터 대기</div></article>`; const ch=Number(x.change_1d_pct||0), r5=Number(x.return_5d_pct||0);return `<article class="index-card card"><div class="index-head"><div><b>${esc(x.name||k)}</b><div class="index-source">${esc(x.source||'')}${x.proxy?' · proxy':''}${x.stale?' · STALE':''}</div></div><div class="index-last">${Number(x.latest||0).toLocaleString(undefined,{maximumFractionDigits:2})}<span class="${ch>=0?'change pos':'change neg'}">${pct(ch)}</span></div></div>${sparkline(x.points||[])}<div class="index-foot"><span>5D <b class="${r5>=0?'change pos':'change neg'}">${pct(r5)}</b></span><span>MA20 ${pct(x.distance_ma20_pct||0)}</span><span>${esc(x.trend||'')}</span></div></article>`}).join('');
}
function renderDataHealth(d){
  const h=d?.data_health||{}, good=h.status==='good', waiting=h.status==='waiting'; $('#healthBadge').textContent=good?'데이터 정상':waiting?'데이터 대기':'일부 지연'; $('#healthBadge').className=`health-badge ${good?'ok':waiting?'':'warn'}`;
  const mf=Number(h.macro_failures||0), ms=Number(h.macro_stale||0), key=h.macro_api_key_status||'unknown';
  const fredText=mf>0?`${mf} 실패`:ms>0?`${ms} 이전값`:key==='missing'?'키 필요':key==='invalid'?'키 오류':'OK';
  const items=[['FRED',fredText,mf===0&&ms===0&&key==='valid'?'ok':(mf===0&&ms===0&&key==='unknown'?'ok':'warn')],['장중 프록시',Number(h.live_proxy_failures||0),null],['지수',Number(h.index_failures||0)+Number(h.index_stale||0),null],['KR 스캔',h.kr_scan==='ok'?0:(h.kr_scan==='not_scanned'?null:1),null],['US 스캔',h.us_scan==='ok'?0:(h.us_scan==='not_scanned'?null:1),null]];
  $('#dataHealth').innerHTML=items.map(([n,v,forced])=>{if(typeof v==='string')return `<div class="health-item ${forced||'warn'}"><span>${n}</span><b>${v}</b></div>`;const cls=v===0?'ok':v==null?'idle':'warn';return `<div class="health-item ${cls}"><span>${n}</span><b>${v===0?'OK':v==null?'대기':`${v} 경고`}</b></div>`}).join('');
}
function renderDiagnostics(d){
  const x=diagnosticData(d), top=x.top||[]; $('#scanSummary').textContent=x.phase==='intraday'?`장중 전체스캔 · ${x.evaluated_count||0}개 평가`:`장전 후보 · ${x.evaluated_count||0}개 평가`;
  if(!top.length){$('#candidateDiagnostics').innerHTML=`<div class="diag-head"><b>${esc(x.summary||'후보 데이터 없음')}</b><span class="diag-status">${esc(x.data_status||'대기')}</span></div><div class="sub">전체 스캔 후 기준에 근접한 후보가 생기면 여기에 표시됩니다.</div>`;return;}
  const req=Number(x.required_score||top[0]?.required_score||0);
  $('#candidateDiagnostics').innerHTML=`<div class="diag-head"><div><b>${x.accepted?'신규 주도 후보 감지':'왜 추천하지 않았나'}</b><div class="sub">추천 기준 ${req?req.toFixed(1):'-'}${x.required_probability_pct?` · 오를 가능성 ${Number(x.required_probability_pct).toFixed(1)}% 이상`:''}</div></div><span class="diag-status ${x.data_status==='ok'?'ok':''}">${esc(x.data_status||'ok')}</span></div><div class="diag-list">${top.slice(0,5).map((r,i)=>{const gap=Number(r.gap||0),prob=r.up_probability,er=r.expected_return_pct;return `<div class="diag-row"><div class="diag-rank">${i+1}</div><div class="diag-main"><div><b>${esc(r.name||r.code)}</b><span class="code">${esc(r.code)}</span></div><div class="diag-reasons">${(r.reasons||[]).slice(0,3).map(z=>`<span>${esc(z)}</span>`).join('')||'<span>점수 순위/선발 슬롯 기준</span>'}</div><div class="diag-extra">${prob!=null?`오를 가능성 ${Number(prob).toFixed(1)}% · `:''}${er!=null?`기대수익 ${pct(er)} · `:''}${r.change_pct!=null?`당일 ${pct(r.change_pct)}`:''}</div></div><div class="diag-score"><strong>${Number(r.score||0).toFixed(1)}</strong><small class="${gap>=0?'change pos':'change neg'}">${gap>=0?'+':''}${gap.toFixed(1)}</small></div></div>`}).join('')}</div>`;
}


function laneStatusBadge(status){
  const action=String(status||'WATCH').toUpperCase()==='ACTIONABLE';
  return `<span class="lane-badge ${action?'action':'watch'}">${action?'조건 충족':'WATCH'}</span>`;
}
function laneCard(r,type,currency){
  const score=Number(r.score||0), req=Number(r.required_score||0), gap=score-req;
  const strengths=(r.strengths||[]).slice(0,5).map(x=>`<span>${esc(x)}</span>`).join('');
  const reasons=(r.reasons||[]).slice(0,3).map(x=>`<li>${esc(x)}</li>`).join('');
  const inst=(r.institutional?.matches||[]).slice(0,3).map(x=>`${esc(x.manager)} ${esc(x.change||'HOLD')}`).join(' · ');
  if(type==='day'){
    return `<article class="card lane-card"><div class="lane-card-top"><div><div class="stock-name">${esc(r.name||r.code)}<span class="code">${esc(r.code)}</span></div><div class="lane-price">${money(r.reference_price,currency)} <span class="change ${Number(r.change_pct||0)>=0?'pos':'neg'}">${pct(r.change_pct||0)}</span></div></div>${laneStatusBadge(r.status)}</div><div class="lane-scoreline"><strong>${score.toFixed(1)}</strong><span>필요 ${req.toFixed(1)} · <b class="${gap>=0?'change pos':'change neg'}">${gap>=0?'+':''}${gap.toFixed(1)}</b></span></div><div class="sub"><b>왜 후보인가?</b> ${simpleWhy(r)}</div><div class="lane-metrics"><div><span>순 목표</span><b>+${Number(r.net_target_pct||1).toFixed(2)}%</b></div><div><span>가격 목표</span><b>${money(r.target_price,currency)}</b></div><div><span>계획 손절</span><b>-${Number(r.stop_pct||0).toFixed(2)}%</b></div><div><span>평균 하루 변동폭</span><b>${Number(r.atr_pct||0).toFixed(2)}%</b></div>${r.historical_probability!=null?`<div><span>10년 패턴 유사 성공률</span><b>${Number(r.historical_probability).toFixed(1)}%</b></div>`:''}</div><div class="strength-tags">${strengths}</div>${reasons?`<ul class="lane-reasons">${reasons}</ul>`:''}<div class="lane-warning">${esc(r.warning||'+1%는 목표치이며 보장되지 않습니다.')}</div></article>`;
  }
  return `<article class="card lane-card swing"><div class="lane-card-top"><div><div class="stock-name">${esc(r.name||r.code)}<span class="code">${esc(r.code)}</span></div><div class="lane-price">${money(r.reference_price,currency)}</div></div>${laneStatusBadge(r.status)}</div><div class="lane-scoreline"><strong>${score.toFixed(1)}</strong><span>필요 ${req.toFixed(1)} · <b class="${gap>=0?'change pos':'change neg'}">${gap>=0?'+':''}${gap.toFixed(1)}</b></span></div><div class="sub"><b>왜 후보인가?</b> ${simpleWhy(r)}</div><div class="lane-metrics"><div><span>장기 추세 조건</span><b>${esc(r.trend_template||'-')}</b></div><div><span>최근 20일</span><b class="${Number(r.return_20d_pct||0)>=0?'change pos':'change neg'}">${pct(r.return_20d_pct||0)}</b></div><div><span>최근 60일</span><b class="${Number(r.return_60d_pct||0)>=0?'change pos':'change neg'}">${pct(r.return_60d_pct||0)}</b></div><div><span>시장 대비 강도</span><b>${Number(r.rs_proxy_pct||0).toFixed(0)}</b></div>${r.historical_probability!=null?`<div><span>10년 패턴 유사 성공률</span><b>${Number(r.historical_probability).toFixed(1)}%</b></div>`:''}</div><div class="strength-tags">${strengths}</div>${inst?`<div class="institution-chip">기관보유 참고 · ${inst}</div>`:''}${reasons?`<ul class="lane-reasons">${reasons}</ul>`:''}<div class="lane-warning">${esc(r.warning||'1–2주 상승 가능성 점수이며 보장되지 않습니다.')}</div></article>`;
}
function renderStrategyLanes(d){
  const a=activeData(d), lanes=a.lanes||{}, day=lanes.day_1pct||{}, swing=lanes.swing||{};
  const dayItems=day.items||[], swingItems=swing.items||[];
  $('#dayLaneCount').textContent=dayItems.length?`${Number(day.actionable_count||0)} 충족 / ${dayItems.length} 표시`:'대기';
  $('#swingLaneCount').textContent=swingItems.length?`${Number(swing.actionable_count||0)} 충족 / ${swingItems.length} 표시`:'대기';
  $('#dayLane').innerHTML=dayItems.length?dayItems.map(r=>laneCard(r,'day',a.currency)).join(''):'<div class="card lane-empty"><b>후보 생성 대기</b><div class="sub">장전 또는 전체 스캔이 완료되면 기준 미달 종목도 WATCH로 Top 3가 표시됩니다.</div></div>';
  $('#swingLane').innerHTML=swingItems.length?swingItems.map(r=>laneCard(r,'swing',a.currency)).join(''):'<div class="card lane-empty"><b>후보 생성 대기</b><div class="sub">장전 분석에서 5–10거래일 후보를 생성합니다. 장기 일봉 수집 실패 시에도 가능한 범위에서 WATCH 후보를 유지합니다.</div></div>';
}
function renderInstitutional(d){
  const sec=$('#institutionalSection');
  if(currentMarket!=='US'){sec.classList.add('hidden');return;}
  sec.classList.remove('hidden');
  const st=d.us?.institutional||{}, managers=st.managers||[];
  $('#institutionalStatus').textContent=st.status==='ok'?'SEC 최신 수집':st.status==='partial'?'일부 수집':st.status==='setup_needed'?'설정 필요':st.status||'대기';
  if(st.status==='setup_needed'){
    $('#institutional13f').innerHTML=`<b>SEC_USER_AGENT 설정 필요</b><div class="sub">GitHub Secret에 연락 가능한 이메일을 포함한 User-Agent를 넣으면 Berkshire, Bridgewater, ARK, Pershing Square, Baupost의 13F를 추적합니다.</div><div class="lane-warning">13F는 실시간 매매내역이 아니라 분기 공시이며 최대 45일 늦을 수 있습니다.</div>`;return;
  }
  if(!managers.length){$('#institutional13f').innerHTML='<div class="muted">13F 데이터 대기 중</div>';return;}
  $('#institutional13f').innerHTML=`<div class="institution-list">${managers.map(m=>{const changes=(m.changes||[]).slice(0,4);return `<div class="institution-row"><div><b>${esc(m.name)}</b><div class="sub">보고 ${esc(m.report_date||'-')} · 제출 ${esc(m.filing_date||'-')} · ${Number(m.holdings_count||0)}종목</div></div><div class="institution-changes">${changes.map(x=>`<span class="${['NEW','ADD'].includes(x.change)?'up':['EXIT','REDUCE'].includes(x.change)?'down':''}">${esc(x.change)} ${esc(x.issuer)}</span>`).join('')||'<span>변동 데이터 없음</span>'}</div></div>`}).join('')}</div><div class="lane-warning">${esc(st.limitations||'13F는 분기 공시라 실시간 매매와 차이가 있을 수 있습니다.')}</div>`;
}

function renderHome(d){
  dashboard=d; const a=activeData(d);
  const pubMs=d?.cloud?.generated_at ? new Date(d.cloud.generated_at).getTime() : 0;
  const ageMin=pubMs ? Math.max(0,(Date.now()-pubMs)/60000) : null;
  $('#liveText').textContent=window.KRX_CLOUD_MODE ? (ageMin!=null && ageMin>35 ? `클라우드 데이터 지연 ${Math.round(ageMin)}분` : '클라우드 동기화') : '서버 연결';
  const publishedAt=d?.cloud?.generated_at ? new Date(d.cloud.generated_at) : null;
  const publishedText=publishedAt && !Number.isNaN(publishedAt.getTime()) ? ` · 게시 ${publishedAt.toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}` : '';
  $('#serverTime').textContent=`${currentMarket==='US'?(d.us?.trade_date||d.trade_date):d.trade_date} · ${new Date(d.server_time).toLocaleTimeString('ko-KR',{hour:'2-digit',minute:'2-digit',second:'2-digit'})}${publishedText}`;
  $('#interval').textContent=a.interval;
  $('#lastUpdate').textContent=currentMarket==='US'?'미국 동부시간 자동 대응':(d.last_update?`최근 ${String(d.last_update).slice(11,16)}`:'장전');
  $('.eyebrow').textContent=currentMarket==='US'?'US + GLOBAL AI TRADING ASSISTANT':'KRX + GLOBAL AI TRADING ASSISTANT';
  $('h1').textContent=currentMarket==='US'?'미국 시장 모니터링':'한국 시장 모니터링';
  const pos=(d.positions||[]).find(x=>x.code==='028300') || (d.positions||[])[0];
  if(pos && currentMarket==='KR'){$('#positionWarning').classList.remove('hidden');$('#positionWarning').innerHTML=`<strong>⚠ 고변동 보유 포지션</strong><div style="margin-top:5px">${pos.name} ${fmt(pos.quantity)}주 · 평단 ${fmt(pos.average_price)}원</div><div class="sub">신규 단타 비중 계산에 위험 노출이 반영됩니다.</div>`;} else $('#positionWarning').classList.add('hidden');
  const leader=currentMarket==='KR'?d.replacement_candidate:(d.us?.replacement_candidate||null);
  if(leader && leader.code){const lc=currentMarket==='US'?'USD':'KRW';$('#leaderAlert').classList.remove('hidden');$('#leaderAlert').innerHTML=`<strong>⚡ 장중 신규 주도 후보</strong><div class="leader-grid"><div><div class="stock-name">${leader.name||leader.code}<span class="code">${leader.code}</span></div><div class="leader-price">${money(leader.price,lc)}</div></div><span class="leader-score">score ${Number(leader.score||0).toFixed(1)}</span></div><div class="sub">${leader.status||''}</div>`;} else $('#leaderAlert').classList.add('hidden');
  renderMacro(d.global_macro||{});
  renderMarketPulse(d); renderDataHealth(d);
  renderStrategyLanes(d); renderInstitutional(d);
  const root=$('#recommendations');
  root.innerHTML=!a.recs.length?'<div class="card no-rec"><b>엄격 실행기준 통과 종목 없음</b><div class="sub">오류가 아닙니다. 위 <b>듀얼 전략 후보</b>에는 기준 미달도 WATCH로 계속 표시되며, 아래 진단에서 탈락 이유를 확인할 수 있습니다.</div></div>':a.recs.map(r=>stockCard(r,a.currency)).join('');
  renderDiagnostics(d);
  $('#reportCount').textContent=currentMarket==='US'?'GLOBAL':fmt(a.reports.report_count);
  $('#brokerCount').textContent=currentMarket==='US'?'금리·FX·신용 반영':`${fmt(a.reports.broker_count)}개 증권사/기관`;
  $('#winRate').textContent=`${Number(a.learning.win_rate||0).toFixed(1)}%`; $('#avgReturn').textContent=`평균 ${pct(a.learning.avg_return_pct)}`;
  $('#reportTop').innerHTML=currentMarket==='US'?'<div class="list-row"><b>미국 모델은 별도 DB에서 학습</b><span class="signal good">US</span></div>':(a.reports.top||[]).slice(0,5).map((x,i)=>`<div class="list-row"><div><b>${i+1}. ${x.name}</b><div class="sub">${x.count}건 · ${x.broker_count}곳</div></div><span class="signal ${x.signal>=.5?'good':'bad'}">${x.signal.toFixed(2)}</span></div>`).join('') || '<div class="list-row muted">오늘 리포트 없음</div>';
  const f=a.learning.prediction_feedback||{}; $('#learningCard').innerHTML=`<div class="learn-grid"><div class="learn-box"><span class="muted">표본</span><strong>${fmt(a.learning.samples)}</strong></div><div class="learn-box"><span class="muted">승률</span><strong>${Number(a.learning.win_rate||0).toFixed(1)}%</strong></div><div class="learn-box"><span class="muted">평균</span><strong class="${a.learning.avg_return_pct>=0?'change pos':'change neg'}">${pct(a.learning.avg_return_pct)}</strong></div></div><div class="sub" style="margin-top:12px">방향정확도 ${f.directional_accuracy!=null?(100*Number(f.directional_accuracy)).toFixed(1)+'%':'표본 수집 중'}</div>`;
}

function stockCard(r,currency='KRW'){
  const l=r.live||{}, pr=Number(l.price||r.reference_price||0), ch=Number(l.change_pct||0), conf=Number(r.confidence||r.score||0);
  return `<article class="card stock-card ${currency==='USD'?'usd':''}"><div class="stock-top"><div><div class="stock-name">${r.name}<span class="code">${r.code}</span></div><div class="live-price">${money(pr,currency)} <span class="change ${ch>=0?'pos':'neg'}" style="font-size:14px">${pct(ch)}</span></div></div><div class="score">${conf.toFixed(0)}</div></div><div class="status">${l.status||'장전 전략'}${l.vwap?` · VWAP ${money(l.vwap,currency)}`:''}</div><div class="levels"><div class="level"><span class="muted">1차 관심</span><b>${money(r.entry_low_1,currency)}~${money(r.entry_high_1,currency)}</b></div><div class="level"><span class="muted">추격 금지</span><b>${money(r.chase_limit,currency)} 이상</b></div><div class="level stop"><span class="muted">무효화</span><b>${money(r.stop_price,currency)}</b></div><div class="level target"><span class="muted">목표</span><b>${money(r.target1,currency)} → ${money(r.target2,currency)}</b></div></div><div class="weight-row"><span><b>선정 이유:</b> ${easyTerm(r.rationale||'여러 수치 조건을 종합해 선정')}</span><span class="weight-chip">최대 ${Number(r.weight_pct||0).toFixed(0)}%</span></div></article>`;
}
function openSheet(title,html){$('#sheetTitle').textContent=title;$('#sheetContent').innerHTML=html;$('#sheet').classList.remove('hidden')} function closeSheet(){$('#sheet').classList.add('hidden')} $('#sheetClose').onclick=closeSheet;$('#sheet').addEventListener('click',e=>{if(e.target.id==='sheet')closeSheet()});

async function openTab(tab){
  document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab)); if(tab==='home')return window.scrollTo({top:0,behavior:'smooth'});
  const a=activeData(dashboard||{}), c=a.currency;
  if(tab==='strategy'){
    const day=a.lanes?.day_1pct?.items||[], swing=a.lanes?.swing?.items||[];
    const laneHtml=`<div class="detail-card"><b>하루 +1% 목표 후보</b>${day.map(r=>`<div class="row"><span>${esc(r.name)} · ${esc(r.status)}</span><strong>${Number(r.score||0).toFixed(1)} / ${Number(r.required_score||0).toFixed(1)}</strong></div>`).join('')||'<div class="sub">대기 중</div>'}</div><div class="detail-card"><b>1–2주 Swing 후보</b>${swing.map(r=>`<div class="row"><span>${esc(r.name)} · ${esc(r.status)}</span><strong>${Number(r.score||0).toFixed(1)} / ${Number(r.required_score||0).toFixed(1)}</strong></div>`).join('')||'<div class="sub">대기 중</div>'}</div>`;
    const formal=a.recs.map(r=>`<div class="detail-card"><b>${r.name} (${r.code})</b><div class="row"><span>현재 상태</span><strong>${r.live?.status||'장전'}</strong></div><div class="row"><span>1차 관심</span><strong>${money(r.entry_low_1,c)} ~ ${money(r.entry_high_1,c)}</strong></div><div class="row"><span>추격 금지</span><strong>${money(r.chase_limit,c)}</strong></div><div class="row"><span>손절/무효</span><strong>${money(r.stop_price,c)}</strong></div><div class="row"><span>목표</span><strong>${money(r.target1,c)} → ${money(r.target2,c)}</strong></div></div>`).join('')||'<div class="detail-card"><b>지금 바로 매매할 만큼 강한 후보 없음</b><div class="sub">관심 후보와 실제 매매 기준 통과 종목은 다릅니다.</div></div>';
    return openSheet(`${currentMarket==='US'?'미국':'한국'} 듀얼 전략`,laneHtml+formal);
  }
  if(tab==='reports'){if(currentMarket==='US')return showMacro(); const d=await api('/api/reports');return openSheet('증권사 리포트',(d.top||[]).map((x,i)=>`<div class="detail-card"><b>${i+1}. ${x.name} (${x.code})</b><div class="row"><span>리포트 / 증권사</span><strong>${x.count}건 / ${x.broker_count}곳</strong></div><div class="row"><span>신호</span><strong>${x.signal.toFixed(3)}</strong></div></div>`).join('')||'<div class="muted">오늘 리포트 없음</div>')}
  if(tab==='history'||tab==='learning'){if(currentMarket==='US')return openSheet('미국 모델 학습',`<div class="detail-card"><b>US 모델은 한국 모델과 완전히 분리 학습됩니다.</b><div class="row"><span>표본</span><strong>${a.learning.samples||0}</strong></div><div class="row"><span>승률</span><strong>${Number(a.learning.win_rate||0).toFixed(1)}%</strong></div></div>`);const d=await api('/api/history?days=20');const lessons=(a.learning.lessons||[]).map(x=>`<div class="row"><span>배운 점</span><strong>${esc(x)}</strong></div>`).join('');const learn=`<div class="detail-card"><b>오늘 모델이 실제로 배운 내용</b><div class="row"><span>오늘 평가한 장전 후보</span><strong>${Number(a.learning.today_shadow_samples||0)}개</strong></div><div class="row"><span>누적 후보 학습표본</span><strong>${Number(a.learning.shadow_samples||0)}개</strong></div>${lessons||'<div class="sub">아직 학습 표본이 충분하지 않거나 오늘 학습 전입니다.</div>'}</div>`;return openSheet('추천 결과와 다음날 학습',learn+((d.items||[]).map(x=>`<div class="detail-card"><b>${x.trade_date} · ${x.name}</b><div class="row"><span>장 시작→마감</span><strong>${pct(x.open_to_close_pct)}</strong></div><div class="row"><span>장중 최고상승 / 최대하락</span><strong>${pct(x.mfe_pct)} / ${pct(x.mae_pct)}</strong></div></div>`).join('')||'<div class="muted">평가 데이터 없음</div>'))}
  if(tab==='settings')return openSettings(false);
}
function showMacro(){const d=dashboard?.global_macro||{}, stale=new Set(d.stale_series||[]), hard=new Set(d.failures||[]);const rows=Object.entries(d.series||{}).map(([k,v])=>`<div class="row"><span>${k}${stale.has(k)?' · STALE':hard.has(k)?' · FAIL':''}</span><strong>${Number(v.value||0).toFixed(2)} · 5D ${Number(v.pct_5||0).toFixed(2)}%</strong></div>`).join('');const live=Object.entries(d.live?.rows||{}).map(([k,v])=>`<div class="row"><span>${k} (${v.symbol||''})${v.stale?' · STALE':''}</span><strong>${money(v.price||0,'USD')} · ${pct(v.change_pct||0)}</strong></div>`).join('');const key=d.api_key_status||'unknown';const health=`<div class="detail-card"><b>FRED 수집 상태</b><div class="row"><span>API key</span><strong>${esc(key)}</strong></div><div class="row"><span>최신 / stale / hard fail</span><strong>${Number(d.fresh_series_count||0)} / ${Number(d.stale_series_count||0)} / ${Number(d.hard_failure_count??d.failures?.length??0)}</strong></div><div class="row"><span>전송 실패(복구 포함)</span><strong>${Number(d.transport_failure_count||0)}</strong></div></div>`;openSheet('글로벌 멀티에셋 상세',`${health}<div class="detail-card"><b>${d.summary||'데이터 수집 중'}</b>${rows}</div><div class="detail-card"><b>15분 장중 프록시</b>${live||'<div class="muted">장중 프록시 수집 전</div>'}</div>`)}
$('#macroMore').onclick=showMacro;
$('#krTab').onclick=()=>{currentMarket='KR';$('#krTab').classList.add('active');$('#usTab').classList.remove('active');if(dashboard)renderHome(dashboard)};
$('#usTab').onclick=()=>{currentMarket='US';$('#usTab').classList.add('active');$('#krTab').classList.remove('active');if(dashboard)renderHome(dashboard)};
function openSettings(authOnly=false){const token=localStorage.getItem('krx_api_token')||'';const cloud=window.KRX_CLOUD_MODE;const manualKey=localStorage.getItem('krx_manual_refresh_key')||'';openSheet(authOnly?'서버 인증 필요':'설정',`${cloud?'':`<div class="detail-card"><b>서버 API 토큰</b><input id="tokenInput" class="token-box" type="password" value="${token}" placeholder="APP_API_TOKEN"><button id="saveToken" class="primary">저장 후 다시 연결</button></div>`}${cloud?`<div class="detail-card"><b>즉시 갱신 인증키</b><input id="refreshKeyInput" class="token-box" type="password" value="${esc(manualKey)}" placeholder="MANUAL_REFRESH_KEY"><button id="saveRefreshKey" class="primary">이 기기에 저장</button><div class="sub">GitHub 토큰은 브라우저에 저장되지 않습니다. 이 키는 Supabase Edge Function에서만 검증됩니다.</div></div>`:''}<div class="detail-card"><b>실행 모드</b><div class="row"><span>데이터 소스</span><strong>${cloud?'GitHub Actions + Supabase':'Local Python server'}</strong></div></div><div class="detail-card"><b>시장별 모니터링</b><div class="row"><span>한국 추천종목</span><strong>${dashboard?.monitor_interval_minutes||15}분</strong></div><div class="row"><span>미국 추천종목</span><strong>${dashboard?.us?.monitor_interval_minutes||15}분</strong></div><div class="row"><span>글로벌 매크로</span><strong>15분 캐시</strong></div><div class="sub">↻ 버튼을 누르면 선택한 시장 + 글로벌 매크로 전체 갱신을 즉시 요청하고 완료될 때까지 자동 확인합니다.</div></div>`);setTimeout(()=>{const b=$('#saveToken');if(b)b.onclick=()=>{localStorage.setItem('krx_api_token',$('#tokenInput').value.trim());closeSheet();refresh()};const rb=$('#saveRefreshKey');if(rb)rb.onclick=()=>{localStorage.setItem('krx_manual_refresh_key',$('#refreshKeyInput').value.trim());closeSheet()}},0)}
document.querySelectorAll('[data-tab]').forEach(el=>el.addEventListener('click',()=>openTab(el.dataset.tab)));
async function refresh(){try{renderHome(await api('/api/dashboard'));if(!refreshTimer)armRefresh()}catch(e){$('#liveText').textContent='연결 실패';console.error(e)}}
function sleep(ms){return new Promise(r=>setTimeout(r,ms))}
async function manualRefresh(){
  const btn=$('#manualRefreshBtn'); if(btn?.disabled)return;
  if(!window.KRX_CLOUD_MODE){await refresh();return;}
  const endpoint=(window.KRX_REFRESH_ENDPOINT||'').trim();
  if(!endpoint){openSheet('즉시 갱신 설정 필요','<div class="detail-card">Supabase manual-re최신 Edge Function을 먼저 배포해야 합니다.</div>');return;}
  let key=localStorage.getItem('krx_manual_refresh_key')||'';
  if(!key){key=(prompt('즉시 갱신 인증키(MANUAL_REFRESH_KEY)를 입력하세요.')||'').trim();if(!key)return;localStorage.setItem('krx_manual_refresh_key',key)}
  const before=dashboard?.cloud?.generated_at||'';
  try{
    btn.disabled=true;btn.classList.add('busy');$('#liveText').textContent='업데이트 요청 중';
    const r=await fetch(endpoint,{method:'POST',headers:{'Content-Type':'application/json','X-Refresh-Key':key},body:JSON.stringify({market:currentMarket})});
    if(r.status===401){localStorage.removeItem('krx_manual_refresh_key');throw new Error('즉시 갱신 인증키가 맞지 않습니다. 설정에서 다시 입력하세요.')}
    if(!r.ok){let detail='';try{detail=JSON.stringify(await r.json())}catch(_){detail=await r.text()}throw new Error(`갱신 요청 실패 ${r.status}: ${detail.slice(0,180)}`)}
    $('#liveText').textContent='GitHub Actions 실행 중';
    for(let i=0;i<48;i++){await sleep(5000);const next=await api('/api/dashboard');const after=next?.cloud?.generated_at||'';if(after && after!==before){renderHome(next);$('#liveText').textContent='방금 갱신 완료';setTimeout(()=>{if(dashboard)renderHome(dashboard)},2500);return}}
    throw new Error('갱신 작업은 시작됐지만 4분 안에 게시 완료를 확인하지 못했습니다. Actions 실행 상태를 확인하세요.');
  }catch(e){console.error(e);$('#liveText').textContent='갱신 실패';openSheet('즉시 갱신 실패',`<div class="detail-card">${esc(e.message||e)}</div>`)}finally{btn.disabled=false;btn.classList.remove('busy')}
}
$('#manualRefreshBtn').onclick=manualRefresh;
if('serviceWorker'in navigator)navigator.serviceWorker.register('./sw.js').catch(()=>{});refresh();function armRefresh(){if(refreshTimer)clearInterval(refreshTimer);const sec=Math.max(10,Number(dashboard?.app_refresh_seconds||20));refreshTimer=setInterval(refresh,sec*1000)}setTimeout(armRefresh,1000);
