# KRX AI Trading Assistant v8.0 — DUAL STRATEGY SERVERLESS GLOBAL FREE

**PC/VPS를 24시간 켜놓지 않고** 한국장 + 미국장 + 글로벌 매크로를 모니터링하는 무료 서버리스 버전입니다.

핵심 구조:

```text
GitHub Actions (15분 + 수동 즉시 갱신)
        │
        ├── KIS 한국/미국
        ├── OpenDART
        ├── FRED
        ├── 증권사 리포트
        └── Local ML
                │
                ▼
       Supabase private Storage
        SQLite / models / state
                │
                ├──── Telegram
                │
                ▼
       Supabase public Storage
          dashboard.json
                │
                ▼
         GitHub Pages PWA
                │
                ▼
             휴대폰
```

- OpenAI API: **사용 안 함**
- 자동매매: **없음**
- 추천종목 실제 매수 여부와 무관하게 사후평가/학습
- 한국/미국 모델 별도 학습
- 금리/채권/VIX/FX/신용/유동성/원자재 + 장중 ETF proxy 반영
- GitHub Actions runner가 매번 사라져도 Supabase에서 state 복원

## 가장 먼저 읽을 파일

**`SETUP_V7_SERVERLESS.md`**

## Gold 404 문제

v6의 폐기 FRED series `GOLDAMGBD228NLBM`을 제거했습니다. v7은 `KCROROG`와 `GLD`를 사용합니다.

## 테스트

```bash
pytest -q
```

## 로컬 모드

기존 로컬 PC 방식도 유지됩니다.

```bash
python run.py doctor
python run.py app
```

## v7.3 PWA hosting note
The mobile PWA is deployed with **GitHub Pages**, not Supabase Storage. Supabase Storage intentionally serves HTML as plain text, so it is used only for state and dashboard JSON. Run the `Deploy mobile PWA to GitHub Pages` workflow after enabling Pages with **Settings -> Pages -> Source: GitHub Actions**.


## v7.7 Decision Dashboard

- 추천이 없을 때 **Top 5 탈락 후보 + 정확한 탈락 이유** 표시
- KOSPI/KOSDAQ/NASDAQ/S&P 500/Dow/Russell 2000 30거래일 차트
- MA20 breadth, 5일 breadth, Risk-on/Mixed/Risk-off, 성장주 리더십
- FRED/KIS/지수/후보스캔 **데이터 건강도** 표시
- 미국 지수 직접 호출 실패 시 QQQ/SPY/DIA/IWM proxy fallback을 출처와 함께 표시

새 지수 요인은 먼저 기록/검증하고, 검증 없이 매매 score에 바로 추가하지 않습니다.


## v7.8 Macro resilience + secure manual refresh

- FRED 수집 실패를 **전송 실패 / 이전 정상값 사용 / 실제 하드 실패**로 분리합니다.
- GitHub Actions에서는 무료 `FRED_API_KEY` 사용을 강하게 권장합니다.
- 브라우저에 GitHub 토큰을 노출하지 않고, Supabase Edge Function을 통해 ↻ 버튼으로 현재 KR/US 탭을 즉시 갱신 요청할 수 있습니다.
- 자세한 1회 설정은 `SETUP_V7_8_MANUAL_REFRESH.md`를 읽으세요.


## v7.9 Reliability + 15-minute Telegram heartbeat

- FRED 호출 전에 짧은 health probe를 수행하고, API/CSV가 모두 막히면 19개 시리즈를 각각 오래 재시도하지 않고 직전 정상 snapshot을 즉시 사용합니다.
- Supabase에 저장된 FRED snapshot을 serverless persistent cache로 사용해 15분 monitor마다 느린 FRED 데이터를 불필요하게 다시 받지 않습니다.
- 휴대폰 ↻ 즉시 갱신은 KIS/지수/종목을 강제 갱신하되, FRED는 cache를 우선 사용합니다.
- KRX/US 장중 workflow가 성공할 때마다 추천 유무와 상관없이 Telegram heartbeat를 보냅니다.
- 추천이 없으면 가장 가까운 후보/필요 score를 함께 보여주며, workflow 자체가 실패하면 Telegram 실패 알림을 보냅니다.
- 상세 내용: `CHANGELOG_V7_9_RELIABILITY_TELEGRAM.md`, 업그레이드: `UPGRADE_V7_9.md`.


## v8.0 Dual Strategy — 당일 +1% 목표 / 1–2주 Swing

> **중요:** +1%는 보장 수익률이 아니라 비용 차감 후 목표치입니다. 자동 주문은 없습니다.

### 1) 당일 +1% 목표 lane

- 모든 스캔에서 Top 3를 항상 표시합니다. 기준 미달은 `WATCH`, 통과는 `ACTIONABLE`입니다.
- 기본 순목표 `+1.00%`, 왕복비용 가정 `25bp`를 합쳐 기본 가격 목표는 약 `+1.25%`입니다.
- 유동성, 거래대금 가속, 거래량 수요, 단기 모멘텀, 추세, 수급, 20일 고가 접근, ATR 도달 가능성, 시장 국면, 과열 위험을 결합합니다.
- ATR이 목표보다 너무 작으면 도달 가능성이 낮다고 보고 감점하고, 지나치게 크면 tail/gap 위험으로 감점합니다.
- Risk-on/Mixed/Risk-off에 따라 ACTIONABLE 기준이 동적으로 바뀝니다.

### 2) 1–2주 Swing lane

- 5–10 거래일을 목표 horizon으로 하고 Top 3를 항상 표시합니다.
- 260일 일봉을 가능한 경우 수집해 50/150/200일 이동평균, 52주 고가/저가, 상대강도, 20/60/120일 모멘텀을 평가합니다.
- Mark Minervini의 Stage-2/Trend Template 아이디어, O'Neil/CAN-SLIM에서 공개적으로 설명된 상대강도·수급·시장방향·기관후원 원칙, 학술 momentum/quality 아이디어를 서로 중복되지 않게 결합합니다.
- 미국 후보는 SEC 13F를 작은 보조요인(4% weight)으로만 사용합니다. 13F는 분기자료이며 최대 45일 지연될 수 있어 진입 트리거로 쓰지 않습니다.

### 3) '추천 종목 없음' UX 수정

기존 엄격 추천 엔진은 `allow_abstain=true`를 유지합니다. 즉 품질이 낮을 때 억지 매수 신호를 만들지 않습니다. 대신 v8.0의 두 lane은 데이터가 있으면 **무조건 Top 후보를 보여주고** `WATCH`/`ACTIONABLE`을 구분합니다. 따라서 화면이 계속 빈 상태로 보이는 문제를 해결하면서도 기준을 무작정 낮추지 않습니다.

### 4) 유명 투자회사 13F 추적

기본 추적: Berkshire Hathaway, Bridgewater Associates, ARK Investment Management, Pershing Square, Baupost Group.

GitHub Repository Secret에 아래를 추가하세요.

```text
SEC_USER_AGENT
```

값 예시:

```text
hongjae255 stockbot your-email@example.com
```

실제 연락 가능한 이메일을 사용하고 저장소 코드에는 넣지 마세요.

자세한 방법론은 `STRATEGY_RESEARCH_V8_0.md`, 업그레이드는 `UPGRADE_V8_0.md`를 참고하세요.
