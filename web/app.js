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
  $('#macroCard').innerHTML=`<div class="risk-gauge"><div><b>Global Risk Regime</b><div class="market-label">${d?.source||'local cache'} · 장중 ${liveScore.toFixed(0)} · 실패 ${d?.failures?.length||0}개</div></div><div class="risk-score">${score.toFixed(0)}</div></div><div class="macro-grid"><div class="macro-item"><span>미 2년물</span><b>${macroValue(d,'ust_2y',2,'%')}</b></div><div class="macro-item"><span>미 10년물</span><b>${macroValue(d,'ust_10y',2,'%')}</b></div><div class="macro-item"><span>VIX</span><b>${macroValue(d,'vix')}</b></div><div class="macro-item"><span>USD/KRW</span><b>${macroValue(d,'usdkrw',0)}</b></div><div class="macro-item"><span>HY OAS</span><b>${macroValue(d,'hy_oas',2,'%')}</b></div><div class="macro-item"><span>유동성</span><b>${(100*Number(f.macro_liquidity_support??.5)).toFixed(0)}</b></div></div>`;
}

function activeData(d){
  if(currentMarket==='US') return {recs:d.us?.recommendations||[], learning:d.us?.learning||{}, interval:d.us?.monitor_interval_minutes||15, reports:{report_count:0,broker_count:0,top:[]}, currency:'USD'};
  return {recs:d.recommendations||[], learning:d.learning||{}, interval:d.monitor_interval_minutes||15, reports:d.reports||{}, currency:'KRW'};
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
  const root=$('#recommendations');
  root.innerHTML=!a.recs.length?'<div class="card"><b>현재 신규 추천 없음</b><div class="sub">기준을 통과한 종목이 없으면 현금 대기합니다.</div></div>':a.recs.map(r=>stockCard(r,a.currency)).join('');
  $('#reportCount').textContent=currentMarket==='US'?'GLOBAL':fmt(a.reports.report_count);
  $('#brokerCount').textContent=currentMarket==='US'?'금리·FX·신용 반영':`${fmt(a.reports.broker_count)}개 증권사/기관`;
  $('#winRate').textContent=`${Number(a.learning.win_rate||0).toFixed(1)}%`; $('#avgReturn').textContent=`평균 ${pct(a.learning.avg_return_pct)}`;
  $('#reportTop').innerHTML=currentMarket==='US'?'<div class="list-row"><b>미국 모델은 별도 DB에서 학습</b><span class="signal good">US</span></div>':(a.reports.top||[]).slice(0,5).map((x,i)=>`<div class="list-row"><div><b>${i+1}. ${x.name}</b><div class="sub">${x.count}건 · ${x.broker_count}곳</div></div><span class="signal ${x.signal>=.5?'good':'bad'}">${x.signal.toFixed(2)}</span></div>`).join('') || '<div class="list-row muted">오늘 리포트 없음</div>';
  const f=a.learning.prediction_feedback||{}; $('#learningCard').innerHTML=`<div class="learn-grid"><div class="learn-box"><span class="muted">표본</span><strong>${fmt(a.learning.samples)}</strong></div><div class="learn-box"><span class="muted">승률</span><strong>${Number(a.learning.win_rate||0).toFixed(1)}%</strong></div><div class="learn-box"><span class="muted">평균</span><strong class="${a.learning.avg_return_pct>=0?'change pos':'change neg'}">${pct(a.learning.avg_return_pct)}</strong></div></div><div class="sub" style="margin-top:12px">방향정확도 ${f.directional_accuracy!=null?(100*Number(f.directional_accuracy)).toFixed(1)+'%':'표본 수집 중'}</div>`;
}

function stockCard(r,currency='KRW'){
  const l=r.live||{}, pr=Number(l.price||r.reference_price||0), ch=Number(l.change_pct||0), conf=Number(r.confidence||r.score||0);
  return `<article class="card stock-card ${currency==='USD'?'usd':''}"><div class="stock-top"><div><div class="stock-name">${r.name}<span class="code">${r.code}</span></div><div class="live-price">${money(pr,currency)} <span class="change ${ch>=0?'pos':'neg'}" style="font-size:14px">${pct(ch)}</span></div></div><div class="score">${conf.toFixed(0)}</div></div><div class="status">${l.status||'장전 전략'}${l.vwap?` · VWAP ${money(l.vwap,currency)}`:''}</div><div class="levels"><div class="level"><span class="muted">1차 관심</span><b>${money(r.entry_low_1,currency)}~${money(r.entry_high_1,currency)}</b></div><div class="level"><span class="muted">추격 금지</span><b>${money(r.chase_limit,currency)} 이상</b></div><div class="level stop"><span class="muted">무효화</span><b>${money(r.stop_price,currency)}</b></div><div class="level target"><span class="muted">목표</span><b>${money(r.target1,currency)} → ${money(r.target2,currency)}</b></div></div><div class="weight-row"><span>${r.rationale||'수치/ML 기반 전략'}</span><span class="weight-chip">최대 ${Number(r.weight_pct||0).toFixed(0)}%</span></div></article>`;
}
function openSheet(title,html){$('#sheetTitle').textContent=title;$('#sheetContent').innerHTML=html;$('#sheet').classList.remove('hidden')} function closeSheet(){$('#sheet').classList.add('hidden')} $('#sheetClose').onclick=closeSheet;$('#sheet').addEventListener('click',e=>{if(e.target.id==='sheet')closeSheet()});

async function openTab(tab){
  document.querySelectorAll('.nav-item').forEach(x=>x.classList.toggle('active',x.dataset.tab===tab)); if(tab==='home')return window.scrollTo({top:0,behavior:'smooth'});
  const a=activeData(dashboard||{}), c=a.currency;
  if(tab==='strategy') return openSheet(`${currentMarket==='US'?'미국':'한국'} 오늘의 전략`,a.recs.map(r=>`<div class="detail-card"><b>${r.name} (${r.code})</b><div class="row"><span>현재 상태</span><strong>${r.live?.status||'장전'}</strong></div><div class="row"><span>1차 관심</span><strong>${money(r.entry_low_1,c)} ~ ${money(r.entry_high_1,c)}</strong></div><div class="row"><span>추격 금지</span><strong>${money(r.chase_limit,c)}</strong></div><div class="row"><span>손절/무효</span><strong>${money(r.stop_price,c)}</strong></div><div class="row"><span>목표</span><strong>${money(r.target1,c)} → ${money(r.target2,c)}</strong></div></div>`).join('')||'<div class="muted">추천 없음</div>');
  if(tab==='reports'){if(currentMarket==='US')return showMacro(); const d=await api('/api/reports');return openSheet('증권사 리포트',(d.top||[]).map((x,i)=>`<div class="detail-card"><b>${i+1}. ${x.name} (${x.code})</b><div class="row"><span>리포트 / 증권사</span><strong>${x.count}건 / ${x.broker_count}곳</strong></div><div class="row"><span>신호</span><strong>${x.signal.toFixed(3)}</strong></div></div>`).join('')||'<div class="muted">오늘 리포트 없음</div>')}
  if(tab==='history'||tab==='learning'){if(currentMarket==='US')return openSheet('미국 모델 학습',`<div class="detail-card"><b>US 모델은 한국 모델과 완전히 분리 학습됩니다.</b><div class="row"><span>표본</span><strong>${a.learning.samples||0}</strong></div><div class="row"><span>승률</span><strong>${Number(a.learning.win_rate||0).toFixed(1)}%</strong></div></div>`);const d=await api('/api/history?days=20');return openSheet('추천 종목 사후평가',(d.items||[]).map(x=>`<div class="detail-card"><b>${x.trade_date} · ${x.name}</b><div class="row"><span>시가→종가</span><strong>${pct(x.open_to_close_pct)}</strong></div><div class="row"><span>MFE / MAE</span><strong>${pct(x.mfe_pct)} / ${pct(x.mae_pct)}</strong></div></div>`).join('')||'<div class="muted">평가 데이터 없음</div>')}
  if(tab==='settings')return openSettings(false);
}
function showMacro(){const d=dashboard?.global_macro||{};const rows=Object.entries(d.series||{}).map(([k,v])=>`<div class="row"><span>${k}</span><strong>${Number(v.value||0).toFixed(2)} · 5D ${Number(v.pct_5||0).toFixed(2)}%</strong></div>`).join('');const live=Object.entries(d.live?.rows||{}).map(([k,v])=>`<div class="row"><span>${k} (${v.symbol||''})</span><strong>${money(v.price||0,'USD')} · ${pct(v.change_pct||0)}</strong></div>`).join('');openSheet('글로벌 멀티에셋 상세',`<div class="detail-card"><b>${d.summary||'데이터 수집 중'}</b>${rows}</div><div class="detail-card"><b>15분 장중 프록시</b>${live||'<div class="muted">장중 프록시 수집 전</div>'}</div>`)}
$('#macroMore').onclick=showMacro;
$('#krTab').onclick=()=>{currentMarket='KR';$('#krTab').classList.add('active');$('#usTab').classList.remove('active');if(dashboard)renderHome(dashboard)};
$('#usTab').onclick=()=>{currentMarket='US';$('#usTab').classList.add('active');$('#krTab').classList.remove('active');if(dashboard)renderHome(dashboard)};
function openSettings(authOnly=false){const token=localStorage.getItem('krx_api_token')||'';const cloud=window.KRX_CLOUD_MODE;openSheet(authOnly?'서버 인증 필요':'설정',`${cloud?'':`<div class="detail-card"><b>서버 API 토큰</b><input id="tokenInput" class="token-box" type="password" value="${token}" placeholder="APP_API_TOKEN"><button id="saveToken" class="primary">저장 후 다시 연결</button></div>`}<div class="detail-card"><b>실행 모드</b><div class="row"><span>데이터 소스</span><strong>${cloud?'GitHub Actions + Supabase':'Local Python server'}</strong></div></div><div class="detail-card"><b>시장별 모니터링</b><div class="row"><span>한국 추천종목</span><strong>${dashboard?.monitor_interval_minutes||15}분</strong></div><div class="row"><span>미국 추천종목</span><strong>${dashboard?.us?.monitor_interval_minutes||15}분</strong></div><div class="row"><span>글로벌 매크로</span><strong>15분 캐시</strong></div><div class="sub">미국 시간은 New York timezone으로 계산되어 DST가 자동 반영됩니다.</div></div>`);setTimeout(()=>{const b=$('#saveToken');if(b)b.onclick=()=>{localStorage.setItem('krx_api_token',$('#tokenInput').value.trim());closeSheet();refresh()}},0)}
document.querySelectorAll('[data-tab]').forEach(el=>el.addEventListener('click',()=>openTab(el.dataset.tab)));
async function refresh(){try{renderHome(await api('/api/dashboard'));if(!refreshTimer)armRefresh()}catch(e){$('#liveText').textContent='연결 실패';console.error(e)}}
if('serviceWorker'in navigator)navigator.serviceWorker.register('./sw.js').catch(()=>{});refresh();function armRefresh(){if(refreshTimer)clearInterval(refreshTimer);const sec=Math.max(10,Number(dashboard?.app_refresh_seconds||20));refreshTimer=setInterval(refresh,sec*1000)}setTimeout(armRefresh,1000);
