# v6 GLOBAL FREE 변경사항

## 신규
- KIS 해외주식 read-only API: 미국 현재가, 거래대금 순위, 일봉, 분봉
- 미국 전용 후보선정/계획/장중/장마감/학습 파이프라인
- 미국 전용 SQLite DB 및 ML model directory
- America/New_York timezone 기반 DST 자동 스케줄
- 미국 추천 10분 모니터링 + 30분 전체시장 신규 주도주 재스캔
- FRED 기반 금리/채권/FX/변동성/신용/유동성/원자재 계층
- FRED API key 없는 current-vintage CSV fallback
- KIS 기반 10분 cross-asset live proxies: SPY/QQQ/SMH/IWM/TLT/HYG/UUP/GLD/USO
- PWA 한국/미국 토글 + 글로벌 멀티에셋 상세 화면
- `run.py macro`, `us-premarket`, `us-intraday`, `us-close`, `us-status`
- `doctor`에 KIS US 및 Global Macro 진단

## 모델
- global macro features 및 live cross-asset features를 supervised ensemble에 추가
- 한국/미국 모델은 분리 학습
- 글로벌 feature는 두 시장에 공유
- 한국장 신규 주도주 재평가에도 글로벌 macro feature 반영

## 안전
- 주문 API 없음
- OpenAI/유료 LLM API 없음
- KIS v4.1 retry/backoff 유지
