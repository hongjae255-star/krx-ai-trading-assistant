# KRX AI Trading Assistant v7.8 — SERVERLESS GLOBAL FREE

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
