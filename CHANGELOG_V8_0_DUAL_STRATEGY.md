# v8.0 — Dual Strategy + Institutional Context

## New

- `stockbot/strategy_lanes.py`
  - 당일 **순 +1% 목표** lane
  - **1–2주 Swing** lane
  - Top 3 always-visible WATCH/ACTIONABLE UX
  - risk-on/mixed/risk-off dynamic thresholds
  - Stage-2 / momentum / relative strength / 52-week high / demand / volatility contraction scoring
- `stockbot/institutional.py`
  - SEC EDGAR Form 13F tracker
  - Berkshire, Bridgewater, ARK, Pershing Square, Baupost
  - NEW/ADD/REDUCE/EXIT comparison
  - 13F weight intentionally capped at 4% in swing score
- KIS daily pagination
  - domestic 100-row API limitation을 여러 구간으로 조회해 200-day MA / 52-week screen 지원
  - overseas BYMD pagination for a small deep-history shortlist
- PWA
  - 두 lane을 동시에 표시
  - formal strict recommendation과 WATCH 후보를 분리
  - 미국 탭에서 13F manager changes 표시
- Telegram
  - formal 추천이 없어도 두 lane의 최상위 후보를 heartbeat에 표시
- Cloud manual job
  - `institutional` job 추가

## Candidate pool

- KR discovery pool: 24 → 32
- US discovery pool: 24 → 28
- expensive swing deep-history calls: KR Top 10, US Top 6

## Safety / reliability

- 자동주문 없음
- +1%는 net target, guarantee 아님
- 13F는 delayed context로만 사용
- strict formal abstention remains enabled
- KIS/SEC failure가 나도 WATCH lane 전체 실행을 죽이지 않도록 fallback 처리
